"""Dashboard KPIs with comparison to the previous period."""

from __future__ import annotations

import pandas as pd

from app.analytics.common import Insight, change_pct, insight, pct
from app.store.dataset import Dataset

PURCHASE_CATEGORIES = {"supplier", "purchase", "purchases", "stock"}


def _period(ds: Dataset, end: pd.Timestamp, days: int) -> dict:
    w = ds.sales_window(days, end)
    start = end - pd.Timedelta(days=days - 1)
    exp = ds.expenses
    if len(exp):
        e = exp[(exp["date"] >= start) & (exp["date"] <= end)]
        purchases = int(e[e["category"].isin(PURCHASE_CATEGORIES)]["amount_paise"].sum())
        overheads = int(e[~e["category"].isin(PURCHASE_CATEGORIES)]["amount_paise"].sum())
    else:
        purchases = overheads = 0
    rev = int(w["line_total_paise"].sum())
    taxable = int(w["taxable_paise"].sum())
    margin = int(w["margin_paise"].sum())
    bills = int(w["invoice_id"].nunique())
    pay = w.groupby("payment_mode")["line_total_paise"].sum()
    days_with_sales = int(w["date"].nunique())
    return {
        "from": start.date().isoformat(), "to": end.date().isoformat(),
        "revenue_paise": rev, "taxable_revenue_paise": taxable,
        "gst_collected_paise": int(w["gst_paise"].sum()),
        "cogs_paise": int(w["cost_total_paise"].sum()), "gross_margin_paise": margin,
        "gross_margin_pct": pct(margin, taxable), "bills": bills,
        "avg_bill_paise": int(round(rev / bills)) if bills else 0,
        "units": float(w["qty"].sum()), "items_sold": int(w["item_id"].nunique()),
        "overheads_paise": overheads, "purchases_paise": purchases,
        "net_profit_estimate_paise": margin - overheads,
        "days_with_sales": days_with_sales,
        "avg_daily_revenue_paise": int(round(rev / days_with_sales)) if days_with_sales else 0,
        "payment_split": {m: {"value_paise": int(v), "share_pct": pct(v, rev)}
                          for m, v in pay.sort_values(ascending=False).items()},
    }


def compute_kpis(ds: Dataset, period_days: int = 30) -> Insight:
    end = ds.as_of_ts
    cur = _period(ds, end, period_days)
    prev_end = end - pd.Timedelta(days=period_days)
    has_prev = len(ds.sales) and ds.sales["date"].min() <= prev_end - pd.Timedelta(
        days=period_days - 1)
    prev = _period(ds, prev_end, period_days) if has_prev else None

    items = ds.items.set_index("item_id")
    stock = ds.stock
    stock_value_cost = int((stock * items["cost_paise"]).sum())
    stock_value_mrp = int((stock * items["mrp_paise"]).sum())

    w = ds.sales_window(period_days)
    gst_by_rate = (w.groupby("gst_rate")
                    .agg(taxable_paise=("taxable_paise", "sum"), gst_paise=("gst_paise", "sum"),
                         revenue_paise=("line_total_paise", "sum"))
                    .reset_index().to_dict("records"))

    deltas = {}
    if prev:
        for k in ("revenue_paise", "gross_margin_paise", "bills", "avg_bill_paise",
                  "net_profit_estimate_paise", "overheads_paise"):
            deltas[k.replace("_paise", "") + "_change_pct"] = change_pct(cur[k], prev[k])
    partial = cur["days_with_sales"] < period_days * 0.8
    return insight(
        kind="kpis", title=f"Business summary - last {period_days} days",
        as_of=ds.as_of.isoformat(),
        headline={**cur, "stock_value_cost_paise": stock_value_cost,
                  "stock_value_mrp_paise": stock_value_mrp, **deltas},
        rows=gst_by_rate,
        meta={"previous": prev, "gst_by_rate": gst_by_rate},
        formula=("revenue = Σ qty × price (GST-inclusive); taxable = revenue ÷ (1 + GST%); "
                 "gross margin = taxable − Σ qty × cost_ex_gst; net profit estimate = gross margin "
                 "− overhead expenses (supplier purchases excluded: they are already in cost)"),
        assumptions=["stock value uses current stock × cost / MRP"],
        confidence="partial" if partial else "ok",
    )
