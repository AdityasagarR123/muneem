"""Every planted story in the sample data must be found, with exact rupee values."""

from app.analytics import kpis, margin, sales, stock
from app.graph import insights as g
from app.graph.backends import MemoryGraph


def test_stockout_stories(stock_df, expected):
    tiers = stock_df.set_index("item_id")["tier"]
    for iid in expected["critical_items"]:
        assert tiers[iid] == "CRITICAL", iid
    for iid in expected["soon_items"]:
        assert tiers[iid] == "SOON", iid
    assert set(tiers[tiers == "CRITICAL"].index) == set(expected["critical_items"])


def test_reorder_plan_covers_risky_items(stock_df, ds, expected):
    plan = stock.reorder_plan(stock_df, ds)
    ids = {r["item_id"] for r in plan.rows}
    assert set(expected["critical_items"]) <= ids
    assert plan.rows[0]["tier"] == "CRITICAL"
    total = sum(r["order_value_paise"] for r in plan.rows)
    assert plan.headline["order_value_paise"] == total


def test_reorder_plan_respects_budget(stock_df, ds):
    plan = stock.reorder_plan(stock_df, ds, budget_paise=500_000)  # ₹5,000
    assert sum(r["order_value_incl_gst_paise"] for r in plan.rows) <= 500_000
    assert plan.headline["deferred_items"] + len(plan.rows) >= 1


def test_dead_stock_exact_rupees(ds, expected):
    res = stock.dead_stock(ds, 30)
    got = {r["item_id"]: r["money_trapped_paise"] for r in res.rows}
    want = {r["item_id"]: r["money_trapped_paise"] for r in expected["dead_stock"]}
    assert got == want
    assert res.headline["money_trapped_paise"] == expected["dead_stock_total_paise"]


def test_high_revenue_low_margin(ds, expected):
    res = margin.margin_report(ds, 30, "item")
    assert expected["high_revenue_low_margin"] in res.headline["high_revenue_low_profit"]
    assert len(res.headline["high_revenue_low_profit"]) <= 4


def test_expense_spike(ds, expected):
    res = sales.expense_report(ds)
    assert res.headline["spikes"] == [expected["expense_spike_category"]]


def test_festival_baseline_avoids_overordering(stock_df, expected):
    r = stock_df.set_index("item_id").loc[expected["festive_adjusted_item"]]
    assert bool(r["festive_baseline_adjusted"])
    assert r["d_adj"] < r["d_naive"] * 0.6
    assert r["naive_order_qty"] > r["order_qty"]


def test_bought_together(ds, expected):
    a, b = expected["bought_together"]
    res = g.bought_together(MemoryGraph(ds), ds, a, 5)
    assert res.rows[0]["item_id"] == b
    assert res.rows[0]["lift"] > 1.5 and res.rows[0]["associated"]
    # only the planted pair is a real association for atta
    assert [r["item_id"] for r in res.rows if r["associated"]] == [b]


def test_supplier_to_call_first(stock_df, ds, expected):
    res = g.supplier_priority(stock_df, MemoryGraph(ds), ds)
    assert res.headline["call_first_id"] == expected["top_risk_supplier"]


def test_kpis_are_consistent(ds):
    k = kpis.compute_kpis(ds, 30)
    h = k.headline
    assert h["revenue_paise"] == h["taxable_revenue_paise"] + h["gst_collected_paise"]
    assert h["gross_margin_paise"] == h["taxable_revenue_paise"] - h["cogs_paise"]
    assert sum(r["gst_paise"] for r in k.rows) == h["gst_collected_paise"]
    assert k.meta["previous"] is not None


def test_upi_share_rises(ds):
    t = sales.payment_mix(ds, 120).headline["upi_trend"]
    assert t["last_4_weeks_pct"] > t["first_4_weeks_pct"]
