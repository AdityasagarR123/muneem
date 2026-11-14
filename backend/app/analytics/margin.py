"""GST-aware margin report. MRP / selling price is GST-inclusive, so GST is backed
out before computing margin - otherwise both takings and margin are overstated."""

from __future__ import annotations

import pandas as pd

from app.analytics.common import Insight, insight, pct
from app.core.money import taxable_from_inclusive
from app.store.dataset import Dataset

MARGIN_FORMULA = (
    "taxable = price_incl_gst ÷ (1 + GST%); gst = price − taxable; "
    "margin = taxable − qty × cost_ex_gst; margin% = margin ÷ taxable. "
    "Flag 'high revenue, low profit' = revenue in top 25% AND margin% in bottom 25% AND "
    "margin% below half the shop's median margin%."
)

GROUP_KEYS = {"item": "item_id", "category": "category", "supplier": "supplier_id"}


def item_margins(ds: Dataset, period_days: int) -> pd.DataFrame:
    w = ds.sales_window(period_days)
    g = (w.groupby("item_id")
          .agg(units=("qty", "sum"), revenue_paise=("line_total_paise", "sum"),
               taxable_paise=("taxable_paise", "sum"), gst_paise=("gst_paise", "sum"),
               cost_paise=("cost_total_paise", "sum"), margin_paise=("margin_paise", "sum"),
               bills=("invoice_id", "nunique"))
          .reset_index())
    items = ds.items[["item_id", "name", "category", "supplier_id", "gst_rate", "mrp_paise",
                      "cost_paise"]].rename(columns={"cost_paise": "unit_cost_paise"})
    g = g.merge(items, on="item_id", how="left")
    g["margin_pct"] = (100 * g["margin_paise"] / g["taxable_paise"].where(g["taxable_paise"] > 0)
                       ).round(2)
    mrp_taxable = taxable_from_inclusive(g["mrp_paise"].astype("int64"),
                                         g["gst_rate"].astype("int64"))
    g["unit_margin_at_mrp_paise"] = mrp_taxable - g["unit_cost_paise"]
    sold = g[g["revenue_paise"] > 0]
    flags = pd.Series("", index=g.index)
    if len(sold) >= 4:
        rev_q3 = sold["revenue_paise"].quantile(0.75)
        mp_q1 = sold["margin_pct"].quantile(0.25)
        mp_med = sold["margin_pct"].median()
        hrlp = ((g["revenue_paise"] >= rev_q3) & (g["margin_pct"] <= mp_q1)
                & (g["margin_pct"] < 0.5 * mp_med))
        flags = flags.where(~hrlp, "high_revenue_low_profit")
    flags = flags.where(~(g["margin_paise"] < 0), "selling_below_cost")
    g["flag"] = flags
    return g.sort_values("revenue_paise", ascending=False).reset_index(drop=True)


def margin_report(ds: Dataset, period_days: int = 30, group_by: str = "item") -> Insight:
    g = item_margins(ds, period_days)
    total_tax = int(g["taxable_paise"].sum())
    total_margin = int(g["margin_paise"].sum())
    flagged = g[g["flag"] != ""]
    if group_by in ("category", "supplier"):
        key = GROUP_KEYS[group_by]
        agg = (g.groupby(key)
                .agg(units=("units", "sum"), revenue_paise=("revenue_paise", "sum"),
                     taxable_paise=("taxable_paise", "sum"), gst_paise=("gst_paise", "sum"),
                     cost_paise=("cost_paise", "sum"), margin_paise=("margin_paise", "sum"),
                     items=("item_id", "nunique"))
                .reset_index())
        agg["margin_pct"] = (100 * agg["margin_paise"] / agg["taxable_paise"].where(
            agg["taxable_paise"] > 0)).round(2)
        if group_by == "supplier" and len(ds.suppliers):
            agg["supplier"] = agg["supplier_id"].map(ds.supplier_index["name"])
        agg["share_of_margin_pct"] = (100 * agg["margin_paise"] / total_margin).round(1) \
            if total_margin else None
        rows = agg.sort_values("margin_paise", ascending=False).to_dict("records")
    else:
        g["share_of_margin_pct"] = (100 * g["margin_paise"] / total_margin).round(1) \
            if total_margin else None
        rows = g.to_dict("records")
    return insight(
        kind="margin_report", title=f"Margin after GST by {group_by} (last {period_days} days)",
        as_of=ds.as_of.isoformat(),
        headline={"taxable_revenue_paise": total_tax, "margin_paise": total_margin,
                  "margin_pct": pct(total_margin, total_tax),
                  "high_revenue_low_profit": flagged[flagged["flag"] == "high_revenue_low_profit"][
                      "item_id"].tolist(),
                  "selling_below_cost": flagged[flagged["flag"] == "selling_below_cost"][
                      "item_id"].tolist()},
        rows=rows, formula=MARGIN_FORMULA,
        assumptions=["cost_ex_gst is today's cost from the item list (no purchase history)",
                     "GST shown is output tax on sales; input-tax credit is not netted"],
        confidence="ok" if len(g) else "no_data",
    )
