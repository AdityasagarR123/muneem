"""Customer analytics: value, RFM segments, churn risk, udhaar ledger, cohorts, lifecycle.

All rules are deterministic and explainable. Customer metrics only cover bills linked to a
customer; every result reports that coverage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analytics.common import Insight, insight, pct, records
from app.store.dataset import Dataset

OVERDUE_DAYS = 30
MIN_OVERDUE_PAISE = 50_000  # ₹500

SEGMENTS = {
    "Champions": ("Buy often, recently, and spend the most", "Thank them / early access to offers"),
    "Loyal": ("Buy often", "Suggest bigger packs or combos"),
    "New": ("First bill in the last 30 days", "Welcome message, invite them back"),
    "Promising": ("Bought recently but not often yet", "Nudge a second / third visit"),
    "At risk": ("Used to buy often, not seen lately", "Re-engagement message now"),
    "Hibernating": ("Rare and not recent", "Low-cost reminder during festivals"),
    "Needs attention": ("Average on all three", "Keep in regular broadcasts"),
}

RFM_FORMULA = (
    "R = recency rank, F = bills in last 90 days rank, M = spend in last 90 days rank, each "
    "scored 1-5 by quintile. Champions R≥4,F≥4,M≥4; Loyal F≥4; At risk R≤2 & F≥3; "
    "Promising R≥4 & F≤2; Hibernating R≤2 & F≤2; New = first bill within 30 days"
)
CHURN_FORMULA = (
    "at risk of churn if the customer has ≥3 bills and days since last bill > max(2.5 × their "
    "usual gap between bills, 14); usual gap = median days between their bills; monthly value = "
    "their spend ÷ active months"
)


def coverage_pct(ds: Dataset, start: pd.Timestamp | None = None) -> float | None:
    s = ds.sales if start is None else ds.sales[ds.sales["date"] >= start]
    total = s["invoice_id"].nunique()
    if not total:
        return None
    return round(100 * s.loc[s["customer_id"] != "", "invoice_id"].nunique() / total, 1)


def _bills(ds: Dataset) -> pd.DataFrame:
    """One row per (customer, bill)."""
    s = ds.sales_x[ds.sales_x["customer_id"] != ""]
    return (s.groupby(["customer_id", "invoice_id"])
             .agg(date=("date", "min"), revenue_paise=("line_total_paise", "sum"),
                  margin_paise=("margin_paise", "sum"), payment_mode=("payment_mode", "first"))
             .reset_index())


def _quintile(s: pd.Series, higher_is_better: bool = True) -> pd.Series:
    if s.empty:
        return s
    r = s.rank(method="first", ascending=higher_is_better)
    return np.ceil(5 * r / len(r)).clip(1, 5).astype(int)


def ledger(ds: Dataset) -> pd.DataFrame:
    """Udhaar (khata) per customer with FIFO ageing: repayments settle the oldest credit first."""
    b = _bills(ds)
    debits = b[b["payment_mode"] == "credit"]
    rep = ds.repayments
    rows = []
    cust_ids = sorted(set(debits["customer_id"]) | set(rep["customer_id"]))
    for cid in cust_ids:
        d = debits[debits["customer_id"] == cid].sort_values("date")
        r = rep[rep["customer_id"] == cid]
        paid = int(r["amount_paise"].sum())
        total = int(d["revenue_paise"].sum())
        remaining, oldest = paid, None
        for row in d.itertuples():
            if remaining >= row.revenue_paise:
                remaining -= row.revenue_paise
            else:
                oldest = row.date
                break
        outstanding = total - paid
        age = (ds.as_of_ts - oldest).days if oldest is not None and outstanding > 0 else 0
        rows.append({
            "customer_id": cid, "credit_given_paise": total, "repaid_paise": paid,
            "outstanding_paise": outstanding, "oldest_unpaid": oldest if outstanding > 0 else None,
            "days_overdue": age, "last_payment": r["date"].max() if len(r) else None,
            "credit_bills": int(len(d)),
            "overdue": bool(outstanding >= MIN_OVERDUE_PAISE and age > OVERDUE_DAYS),
        })
    return pd.DataFrame(rows, columns=[
        "customer_id", "credit_given_paise", "repaid_paise", "outstanding_paise", "oldest_unpaid",
        "days_overdue", "last_payment", "credit_bills", "overdue"])


def customer_stats(ds: Dataset) -> pd.DataFrame:
    """One row per customer (only customers with at least one linked bill, plus khata)."""
    b = _bills(ds)
    as_of = ds.as_of_ts
    d90 = as_of - pd.Timedelta(days=89)
    rows = []
    for cid, g in b.groupby("customer_id"):
        days = np.sort(g["date"].dt.normalize().unique())
        gaps = np.diff(days).astype("timedelta64[D]").astype(int) if len(days) > 1 else np.array([])
        first, last = g["date"].min(), g["date"].max()
        span_days = max((last - first).days + 1, 30)
        recent = g[g["date"] >= d90]
        rows.append({
            "customer_id": cid, "bills": int(len(g)), "first_bill": first, "last_bill": last,
            "recency_days": int((as_of - last).days),
            "usual_gap_days": float(np.median(gaps)) if len(gaps) else None,
            "revenue_paise": int(g["revenue_paise"].sum()), "margin_paise": int(g["margin_paise"].sum()),
            "bills_90d": int(len(recent)), "revenue_90d_paise": int(recent["revenue_paise"].sum()),
            "avg_bill_paise": int(round(g["revenue_paise"].mean())),
            "monthly_value_paise": int(round(g["revenue_paise"].sum() / span_days * 30)),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # churn risk
    gap = df["usual_gap_days"].fillna(np.inf)
    df["churn_threshold_days"] = np.maximum(2.5 * gap, 14).replace(np.inf, np.nan).round(1)
    df["at_risk"] = (df["bills"] >= 3) & (df["recency_days"] > df["churn_threshold_days"].fillna(np.inf))
    df["risk_ratio"] = (df["recency_days"] / df["usual_gap_days"]).round(1)
    # RFM
    df["R"] = _quintile(-df["recency_days"].astype(float))
    df["F"] = _quintile(df["bills_90d"].astype(float))
    df["M"] = _quintile(df["revenue_90d_paise"].astype(float))
    new = (as_of - df["first_bill"]).dt.days < 30
    seg = np.select(
        [new & (df["bills"] <= 2),
         (df["R"] >= 4) & (df["F"] >= 4) & (df["M"] >= 4),
         df["at_risk"] | ((df["R"] <= 2) & (df["F"] >= 3)),
         (df["F"] >= 4),
         (df["R"] >= 4) & (df["F"] <= 2),
         (df["R"] <= 2) & (df["F"] <= 2)],
        ["New", "Champions", "At risk", "Loyal", "Promising", "Hibernating"], default="Needs attention")
    df["segment"] = seg
    # profile + udhaar
    cust = ds.customers.set_index("customer_id")
    df["name"] = df["customer_id"].map(cust["name"]).fillna("")
    df["phone"] = df["customer_id"].map(cust["phone"]).fillna("")
    df["area"] = df["customer_id"].map(cust["area"]).fillna("")
    df["khata"] = df["customer_id"].map(cust["khata"]).fillna(False).astype(bool)
    led = ledger(ds).set_index("customer_id")
    df["udhaar_outstanding_paise"] = df["customer_id"].map(led["outstanding_paise"]).fillna(0).astype("int64")
    df["udhaar_overdue"] = df["customer_id"].map(led["overdue"]).fillna(False).astype(bool)
    return df.sort_values("revenue_90d_paise", ascending=False).reset_index(drop=True)


def segment_map(stats: pd.DataFrame) -> dict[str, str]:
    return dict(zip(stats["customer_id"], stats["segment"])) if len(stats) else {}


# ------------------------------------------------------------------ insights
def _label(row) -> str:
    return row["name"] or row["customer_id"]


def segments_insight(ds: Dataset, stats: pd.DataFrame) -> Insight:
    total_rev = stats["revenue_90d_paise"].sum() if len(stats) else 0
    rows = []
    for seg, (desc, action) in SEGMENTS.items():
        g = stats[stats["segment"] == seg]
        rows.append({
            "segment": seg, "customers": int(len(g)), "description": desc, "suggested_action": action,
            "revenue_90d_paise": int(g["revenue_90d_paise"].sum()),
            "revenue_share_pct": pct(g["revenue_90d_paise"].sum(), total_rev),
            "avg_recency_days": round(float(g["recency_days"].mean()), 1) if len(g) else None,
            "top_customers": [_label(r) for r in g.head(3).to_dict("records")],
        })
    rows.sort(key=lambda r: -r["revenue_90d_paise"])
    cov = coverage_pct(ds, ds.as_of_ts - pd.Timedelta(days=89))
    return insight(
        kind="customer_segments", title="Customer segments (last 90 days)", as_of=ds.as_of.isoformat(),
        headline={"customers": int(len(stats)), "bills_linked_pct": cov,
                  "champions": int((stats["segment"] == "Champions").sum()) if len(stats) else 0,
                  "at_risk": int((stats["segment"] == "At risk").sum()) if len(stats) else 0},
        rows=rows, formula=RFM_FORMULA,
        assumptions=[f"Only bills linked to a customer count ({cov}% of bills in the last 90 days)"],
        confidence="ok" if len(stats) else "no_data")


def at_risk_insight(ds: Dataset, stats: pd.DataFrame, limit: int = 25) -> Insight:
    risk = stats[stats["at_risk"]].sort_values("monthly_value_paise", ascending=False) if len(stats) else stats
    rows = [{"customer_id": r["customer_id"], "name": r["name"], "phone": r["phone"],
             "segment": r["segment"], "bills": r["bills"], "last_bill": r["last_bill"],
             "recency_days": r["recency_days"], "usual_gap_days": r["usual_gap_days"],
             "risk_ratio": r["risk_ratio"], "monthly_value_paise": r["monthly_value_paise"],
             "khata": r["khata"]} for r in risk.head(limit).to_dict("records")]
    return insight(
        kind="at_risk_customers", title="Customers who may have stopped buying",
        as_of=ds.as_of.isoformat(),
        headline={"customers": int(len(risk)),
                  "monthly_value_at_risk_paise": int(risk["monthly_value_paise"].sum()) if len(risk) else 0},
        rows=rows, formula=CHURN_FORMULA, confidence="ok" if len(risk) else "no_data")


def udhaar_insight(ds: Dataset, stats: pd.DataFrame | None = None) -> Insight:
    led = ledger(ds)
    names = ds.customers.set_index("customer_id")["name"]
    led["name"] = led["customer_id"].map(names).fillna("")
    led = led.sort_values(["overdue", "outstanding_paise"], ascending=False)
    over = led[led["overdue"]]
    return insight(
        kind="udhaar", title="Udhaar (khata) - money customers owe you", as_of=ds.as_of.isoformat(),
        headline={"customers_with_balance": int((led["outstanding_paise"] > 0).sum()),
                  "outstanding_paise": int(led["outstanding_paise"].clip(lower=0).sum()),
                  "overdue_customers": int(len(over)),
                  "overdue_paise": int(over["outstanding_paise"].sum())},
        rows=records(led),
        formula=("outstanding = credit bills − repayments; repayments settle the oldest credit bill "
                 f"first; overdue if outstanding ≥ ₹{MIN_OVERDUE_PAISE // 100} and the oldest unpaid "
                 f"bill is older than {OVERDUE_DAYS} days"),
        confidence="ok" if len(led) else "no_data")


def cohorts_insight(ds: Dataset) -> Insight:
    b = _bills(ds)
    if b.empty:
        return insight(kind="cohorts", title="Cohorts", confidence="no_data", as_of=ds.as_of.isoformat())
    b["month"] = b["date"].dt.to_period("M")
    first = b.groupby("customer_id")["month"].min().rename("cohort")
    b = b.join(first, on="customer_id")
    b["offset"] = (b["month"] - b["cohort"]).apply(lambda x: x.n)
    size = first.value_counts().sort_index()
    act = b.groupby(["cohort", "offset"])["customer_id"].nunique()
    rows = []
    for (cohort, off), n in act.items():
        rows.append({"cohort": str(cohort), "month_offset": int(off), "cohort_size": int(size[cohort]),
                     "active": int(n), "retention_pct": pct(n, size[cohort])})
    first_month = str(size.index.min())
    return insight(
        kind="cohorts", title="Monthly cohorts - share of customers who came back",
        as_of=ds.as_of.isoformat(), headline={"cohorts": len(size), "customers": int(size.sum())},
        rows=rows,
        formula="cohort = month of first linked bill; retention = customers of the cohort with a bill "
                "in month k ÷ cohort size",
        assumptions=[f"{first_month} also contains customers who started before the data begins"],
    )


def lifecycle_insight(ds: Dataset, stats: pd.DataFrame) -> Insight:
    n = len(stats)
    stages = [
        ("Bought once", n),
        ("Came back (2+ bills)", int((stats["bills"] >= 2).sum()) if n else 0),
        ("Regular (5+ bills)", int((stats["bills"] >= 5).sum()) if n else 0),
        ("Regular and active (bill in last 30 days)",
         int(((stats["bills"] >= 5) & (stats["recency_days"] < 30)).sum()) if n else 0),
    ]
    rows, prev = [], None
    for name, count in stages:
        rows.append({"stage": name, "customers": count,
                     "of_previous_pct": pct(count, prev) if prev else 100.0,
                     "of_all_pct": pct(count, n)})
        prev = count
    return insight(
        kind="lifecycle_funnel", title="Customer journey funnel", as_of=ds.as_of.isoformat(),
        headline={"customers": n}, rows=rows,
        formula="counts of customers by number of linked bills; last stage also needs a bill in the "
                "last 30 days",
        assumptions=["A counter shop has no website visits or carts, so this funnel follows the "
                     "customer journey instead"],
        confidence="ok" if n else "no_data")


def profile_insight(ds: Dataset, stats: pd.DataFrame, customer_id: str) -> Insight:
    row = stats[stats["customer_id"] == customer_id]
    cust = ds.customers[ds.customers["customer_id"] == customer_id]
    if row.empty and cust.empty:
        return insight(kind="customer_profile", title="Customer", confidence="no_data",
                       as_of=ds.as_of.isoformat())
    lines = ds.sales_x[ds.sales_x["customer_id"] == customer_id]
    top = (lines.groupby("name").agg(bills=("invoice_id", "nunique"), qty=("qty", "sum"),
                                     revenue_paise=("line_total_paise", "sum"))
                .sort_values("revenue_paise", ascending=False).head(8).reset_index())
    monthly = (lines.assign(month=lines["date"].dt.to_period("M").astype(str))
                    .groupby("month")["line_total_paise"].sum().rename("revenue_paise").reset_index())
    led = ledger(ds)
    led = led[led["customer_id"] == customer_id]
    head = records(row)[0] if len(row) else {"customer_id": customer_id, **records(cust)[0]}
    if len(led):
        head["udhaar"] = records(led)[0]
    head["top_items"] = records(top)
    return insight(kind="customer_profile",
                   title=f"Customer - {head.get('name') or customer_id}", as_of=ds.as_of.isoformat(),
                   headline=head, rows=records(monthly),
                   formula="all figures from bills linked to this customer; " + CHURN_FORMULA)
