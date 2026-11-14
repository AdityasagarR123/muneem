from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.agent.tools import ToolError, run_tool
from app.analytics import customers as C
from app.analytics import kpis
from app.analytics import metrics as M
from app.ingest.readers import read_csv_bytes
from app.ingest.validate import phone_key, validate_tables
from app.main import create_app
from app.store.manager import DataManager


@pytest.fixture(scope="module")
def stats(ds):
    return C.customer_stats(ds)


@pytest.fixture(scope="module")
def manager(settings):
    m = DataManager(settings)
    m.startup()
    return m


# ------------------------------------------------------------------ planted stories
def test_customer_coverage_is_partial_and_reported(ds):
    assert 50 <= C.coverage_pct(ds) <= 70
    assert ds.sales.loc[ds.sales["payment_mode"] == "credit", "customer_id"].ne("").all()


def test_churned_regulars_are_flagged(stats, expected):
    risk = set(stats.loc[stats["at_risk"], "customer_id"])
    planted = set(expected["crm"]["churned_customers"])
    assert planted <= risk
    assert len(risk - planted) <= 3  # a few genuine lapses are fine, noise is not
    churned = stats[stats["customer_id"].isin(planted)]
    assert (churned["segment"] == "At risk").all()


def test_udhaar_exact_and_overdue(ds, expected):
    led = C.ledger(ds).set_index("customer_id")
    for r in expected["crm"]["overdue_customers"]:
        assert led.at[r["customer_id"], "outstanding_paise"] == r["outstanding_paise"]
        assert bool(led.at[r["customer_id"], "overdue"])
    assert int(led["outstanding_paise"].sum()) == expected["crm"]["udhaar_outstanding_paise"]
    assert led["overdue"].sum() <= 10


def test_fifo_ageing():
    items = "item_id,name,category,mrp_incl_gst,cost_ex_gst,gst_rate\nA,Atta,Staples,1000,800,0\n"
    sales = ("invoice_id,date,item_id,qty,unit_price_incl_gst,payment_mode,customer_id\n"
             "B1,2026-09-01,A,1,1000,credit,C1\nB2,2026-10-01,A,2,1000,credit,C1\n"
             "B3,2026-11-10,A,1,1000,UPI,C1\n")
    rep = "date,customer_id,amount\n2026-10-15,C1,1500\n"
    report, ds = validate_tables({"items": read_csv_bytes(items.encode()),
                                  "sales": read_csv_bytes(sales.encode()),
                                  "repayments": read_csv_bytes(rep.encode())}, today=date(2026, 11, 14))
    assert report.ok
    row = C.ledger(ds).set_index("customer_id").loc["C1"]
    assert row["outstanding_paise"] == 150000          # ₹3,000 credit - ₹1,500 paid
    assert row["oldest_unpaid"] == pd.Timestamp("2026-10-01")  # Sept bill settled first
    assert row["days_overdue"] == 40 and bool(row["overdue"])  # as of last bill, 10 Nov


def test_segments_cover_everyone(ds, stats):
    ins = C.segments_insight(ds, stats)
    assert sum(r["customers"] for r in ins.rows) == len(stats)
    assert abs(sum(r["revenue_share_pct"] or 0 for r in ins.rows) - 100) < 0.5


def test_diwali_cohort_and_funnel(ds, stats, expected):
    first = ds.customer_first_purchase
    diwali = [c for c in expected["crm"]["diwali_new_customers"] if c in first.index]
    assert (first[diwali] >= pd.Timestamp("2026-10-20")).all()
    f = C.lifecycle_insight(ds, stats).rows
    assert [r["customers"] for r in f] == sorted([r["customers"] for r in f], reverse=True)


# ------------------------------------------------------------------ metrics layer
def test_periods():
    assert M.resolve_period(date(2026, 11, 13), "this_month")[:2] == (date(2026, 11, 1), date(2026, 11, 13))
    assert M.resolve_period(date(2026, 11, 13), "last_month")[:2] == (date(2026, 10, 1), date(2026, 10, 31))
    assert M.resolve_period(date(2026, 11, 13), "this_week")[0] == date(2026, 11, 9)  # Monday
    assert M.resolve_period(date(2026, 11, 13), "last_14_days")[0] == date(2026, 10, 31)
    s, e, _ = M.comparison_period(date(2026, 3, 1), date(2026, 3, 31), "same_period_last_month")
    assert (s, e) == (date(2026, 2, 1), date(2026, 2, 28))
    with pytest.raises(ValueError):
        M.resolve_period(date(2026, 11, 13), "next_year")


def test_metric_matches_kpis_and_groups_add_up(ds):
    k = kpis.compute_kpis(ds, 30).headline
    r = M.query_metric(ds, "revenue", "category", "last_30_days")
    assert r.headline["value_paise"] == k["revenue_paise"]
    assert sum(x["value_paise"] for x in r.rows) == k["revenue_paise"]
    assert r.headline["change_pct"] == k["revenue_change_pct"]
    c = M.query_metric(ds, "customers", None, "last_30_days").headline["value"]
    n = M.query_metric(ds, "new_customers", None, "last_30_days").headline["value"]
    rt = M.query_metric(ds, "returning_customers", None, "last_30_days").headline["value"]
    assert c == n + rt
    assert M.query_metric(ds, "customers", None, "last_30_days").assumptions  # coverage stated


def test_month_to_date_comparison(ds):
    r = M.query_metric(ds, "revenue", None, "this_month", "same_period_last_month").headline
    assert (r["from"], r["compare_from"], r["compare_to"]) == ("2026-11-01", "2026-10-01", "2026-10-13")
    assert r["change_pct"] is not None


def test_new_customers_by_month_uses_each_month_start(ds):
    rows = M.query_metric(ds, "new_customers", "month", "all", "none").rows
    by = {r["group"]: r["value"] for r in rows}
    assert by["2026-08"] < by["2026-07"] / 2  # July holds everyone already shopping when data starts


def test_top_customers_excludes_anonymous(ds, manager):
    rows = M.query_metric(ds, "revenue", "customer", "last_90_days", "none", segments=manager.segments()).rows
    assert all(r["group"] for r in rows)


# ------------------------------------------------------------------ identity resolution
def test_phone_normalisation():
    assert phone_key("+91 98765-43210") == phone_key("09876543210") == "9876543210"
    assert phone_key("+91-XXXXX-10023") == "+91-XXXXX-10023"


def test_customer_merge_and_phone_only_bills():
    items = "item_id,name,category,mrp_incl_gst,cost_ex_gst,gst_rate\nA,Atta,Staples,100,80,0\n"
    cust = "customer_id,name,phone,khata\nK1,Ramesh,98765 43210,yes\nK2,Ramesh G,+91-9876543210,no\n"
    sales = ("invoice_id,date,item_id,qty,unit_price_incl_gst,payment_mode,customer_id,phone\n"
             "B1,2026-11-01,A,1,100,UPI,K2,\nB2,2026-11-02,A,1,100,cash,,9876543210\n"
             "B3,2026-11-03,A,1,100,UPI,,91234 56789\nB4,2026-11-04,A,1,100,credit,,\n")
    rep = "date,customer_id,amount\n2026-11-05,ZZ,50\n"
    report, ds = validate_tables({k: read_csv_bytes(v.encode()) for k, v in
                                  {"items": items, "customers": cust, "sales": sales,
                                   "repayments": rep}.items()}, today=date(2026, 11, 14))
    assert report.ok
    ids = list(ds.sales.sort_values("invoice_id")["customer_id"])
    assert ids[:2] == ["K1", "K1"]           # merged duplicate + phone-only bill
    assert ids[2] == "C-9123456789"          # unknown phone -> new customer
    assert ids[3] == ""                      # anonymous credit bill
    assert len(ds.repayments) == 0           # unknown customer's repayment dropped
    warnings = " ".join(report.warning_summary)
    assert "merged" in warnings and "credit" in warnings and "unknown customer" in warnings


# ------------------------------------------------------------------ tools + API
def test_tools(manager, expected):
    out = run_tool(manager, "at_risk_customers", {})
    assert set(expected["crm"]["churned_customers"]) <= {r["customer_id"] for r in out.insight.rows}
    with pytest.raises(ToolError):
        run_tool(manager, "query_metric", {"metric": "happiness"})
    with pytest.raises(ToolError):
        run_tool(manager, "query_metric", {"metric": "revenue", "period": "next_year"})
    out = run_tool(manager, "query_metric", {"metric": "revenue", "item": "aata 5kg",
                                             "period": "this_month", "compare": "same_period_last_month"})
    assert out.resolved["item"]["id"] == "I001" and out.visuals[0].type == "kpis"
    prof = run_tool(manager, "customer_profile", {"customer": "C0009"})
    assert prof.insight.headline["udhaar"]["overdue"]


def test_ambiguous_customer_name_asks(manager):
    names = manager.require().customers["name"]
    dup = names[names.duplicated()].iloc[0]
    with pytest.raises(ToolError) as e:
        run_tool(manager, "customer_profile", {"customer": dup})
    assert "Which one" in str(e.value) and len(e.value.suggestions) >= 2


def test_crm_api(settings):
    with TestClient(create_app()) as c:
        assert c.get("/api/metrics").json()["metrics"]
        r = c.get("/api/metrics/query", params={"metric": "aov", "group_by": "payment_mode"}).json()
        assert r["rows"] and r["headline"]["unit"] == "paise"
        assert c.get("/api/metrics/query", params={"metric": "nope"}).status_code == 422
        assert c.get("/api/customers/at-risk").json()["headline"]["customers"] >= 14
        assert c.get("/api/customers/udhaar").json()["headline"]["overdue_customers"] >= 5
        assert c.get("/api/customers", params={"segment": "Champions"}).json()["total"] > 0
        assert c.get("/api/customers/C0009").json()["headline"]["customer_id"] == "C0009"
        assert c.get("/api/customers/nobody-here").status_code == 404
        assert c.get("/api/status").json()["postgres"]["status"] == "disabled"
