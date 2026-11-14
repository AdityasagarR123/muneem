"""Payments mix, sales trends, top items, and expense spikes."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analytics.common import Insight, change_pct, insight, pct
from app.analytics.festivals import FestivalCalendar
from app.analytics.kpis import PURCHASE_CATEGORIES
from app.store.dataset import Dataset

GRAINS = {"day": "D", "week": "W-SUN", "month": "MS"}


# ------------------------------------------------------------------ payments
def payment_mix(ds: Dataset, period_days: int = 90) -> Insight:
    w = ds.sales_window(period_days)
    bills = w.groupby(["invoice_id", "payment_mode"])["line_total_paise"].sum().reset_index()
    total = int(bills["line_total_paise"].sum())
    by_mode = (bills.groupby("payment_mode")
                    .agg(value_paise=("line_total_paise", "sum"), bills=("invoice_id", "count"))
                    .reset_index())
    by_mode["share_pct"] = (100 * by_mode["value_paise"] / total).round(2) if total else 0
    by_mode["avg_bill_paise"] = (by_mode["value_paise"] / by_mode["bills"]).round().astype("int64")
    wk = w.assign(week=w["date"].dt.to_period("W-SUN").dt.start_time)
    weekly = wk.pivot_table(index="week", columns="payment_mode", values="line_total_paise",
                            aggfunc="sum", fill_value=0)
    shares = (weekly.div(weekly.sum(axis=1), axis=0) * 100).round(2)
    series = [{"week": idx, **{m: float(v) for m, v in row.items()}}
              for idx, row in shares.iterrows()]
    upi_trend = None
    if "UPI" in shares and len(shares) >= 8:
        upi_trend = {"first_4_weeks_pct": round(float(shares["UPI"].iloc[:4].mean()), 2),
                     "last_4_weeks_pct": round(float(shares["UPI"].iloc[-4:].mean()), 2)}
    credit = int(by_mode.loc[by_mode["payment_mode"] == "credit", "value_paise"].sum())
    return insight(
        kind="payment_mix", title=f"How customers pay (last {period_days} days)",
        as_of=ds.as_of.isoformat(),
        headline={"total_paise": total, "upi_trend": upi_trend, "credit_given_paise": credit,
                  "credit_share_pct": pct(credit, total)},
        rows=by_mode.sort_values("value_paise", ascending=False).to_dict("records"),
        meta={"weekly_share_pct": series},
        formula="share = Σ bill value by payment mode ÷ Σ bill value; weekly shares use Mon-Sun weeks",
        assumptions=["one payment mode per bill", "credit = udhaar given (repayments not tracked)"],
        confidence="ok" if total else "no_data",
    )


# ------------------------------------------------------------------ trends
def sales_trend(ds: Dataset, cal: FestivalCalendar, item_id: str | None = None,
                category: str | None = None, grain: str = "day", period_days: int = 90) -> Insight:
    w = ds.sales_window(period_days)
    label = "All items"
    if item_id:
        w = w[w["item_id"] == item_id]
        label = ds.item_index["name"].get(item_id, item_id)
    elif category:
        w = w[w["category"].str.lower() == category.lower()]
        label = category
    end = ds.as_of_ts
    start = end - pd.Timedelta(days=period_days - 1)
    daily = (w.groupby("date").agg(units=("qty", "sum"), revenue_paise=("line_total_paise", "sum"),
                                   margin_paise=("margin_paise", "sum"))
              .reindex(pd.date_range(start, end, freq="D"), fill_value=0))
    if grain == "day":
        out = daily.copy()
        out["units_ma7"] = out["units"].rolling(7, min_periods=1).mean().round(2)
    else:
        rs = daily.resample(GRAINS.get(grain, "W-SUN"), label="left", closed="left")
        out = rs.sum()
        # partial first/last periods: per-day averages keep the chart honest
        out["days"] = rs["units"].count()
        out["units_per_day"] = (out["units"] / out["days"]).round(2)
        out["revenue_per_day_paise"] = (out["revenue_paise"] / out["days"]).round().astype("int64")
    out.index.name = "period"
    rows = out.reset_index().to_dict("records")
    half = len(daily) // 2
    first, second = daily["units"].iloc[:half].sum(), daily["units"].iloc[half:].sum()
    fests = []
    cats = [category] if category else (
        [ds.item_index["category"].get(item_id)] if item_id else list(ds.items["category"].unique()))
    for f in cal.festivals:
        if f.start <= end.date() and f.end >= start.date() and any(c in f.uplift for c in cats):
            fests.append({"name": f.name, "start": f.start, "end": f.end, "peak": f.peak})
    return insight(
        kind="sales_trend", title=f"Sales trend - {label}", as_of=ds.as_of.isoformat(),
        headline={"label": label, "grain": grain, "units": float(daily["units"].sum()),
                  "revenue_paise": int(daily["revenue_paise"].sum()),
                  "avg_units_per_day": round(float(daily["units"].mean()), 1),
                  "last_7_days_avg_units_per_day": round(float(daily["units"].iloc[-7:].mean()), 1),
                  "best_day": daily["units"].idxmax() if daily["units"].sum() else None,
                  "best_day_units": float(daily["units"].max()),
                  "second_half_vs_first_half_pct": change_pct(second, first)},
        rows=rows, meta={"festivals": fests},
        formula="units and revenue summed per period; 7-day moving average for daily grain",
        confidence="ok" if len(w) else "no_data",
    )


def top_items(ds: Dataset, by: str = "revenue", n: int = 10, period_days: int = 30) -> Insight:
    from app.analytics.margin import item_margins

    g = item_margins(ds, period_days)
    col = {"revenue": "revenue_paise", "margin": "margin_paise", "units": "units",
           "margin_pct": "margin_pct"}.get(by, "revenue_paise")
    g = g.sort_values(col, ascending=False).head(max(1, min(n, 50)))
    keep = ["item_id", "name", "category", "units", "revenue_paise", "margin_paise", "margin_pct",
            "bills"]
    return insight(
        kind="top_items", title=f"Top {len(g)} items by {by} (last {period_days} days)",
        as_of=ds.as_of.isoformat(), headline={"by": by, "n": len(g)},
        rows=g[keep].to_dict("records"),
        formula="ranked by Σ over sales lines in the period (margin after GST)",
        confidence="ok" if len(g) else "no_data",
    )


# ------------------------------------------------------------------ expenses
def expense_report(ds: Dataset, window_days: int = 30, windows: int = 4) -> Insight:
    exp = ds.expenses
    if not len(exp):
        return insight(kind="expenses", title="Expenses", confidence="no_data",
                       as_of=ds.as_of.isoformat())
    end = ds.as_of_ts
    first = exp["date"].min()
    totals = []
    for k in range(windows):
        we = end - pd.Timedelta(days=k * window_days)
        ws = we - pd.Timedelta(days=window_days - 1)
        if ws < first - pd.Timedelta(days=3):
            break
        e = exp[(exp["date"] >= ws) & (exp["date"] <= we)]
        totals.append(e.groupby("category")["amount_paise"].sum().rename(
            f"{ws.date().isoformat()}..{we.date().isoformat()}"))
    if not totals:
        return insight(kind="expenses", title="Expenses", confidence="no_data",
                       as_of=ds.as_of.isoformat())
    table = pd.concat(totals, axis=1).fillna(0).astype("int64")
    rows = []
    for cat, vals in table.iterrows():
        cur = int(vals.iloc[0])
        prior = vals.iloc[1:].to_numpy(dtype=float)
        mean = float(prior.mean()) if len(prior) else 0.0
        sd = float(np.std(prior, ddof=1)) if len(prior) > 1 else 0.0
        is_purchase = cat in PURCHASE_CATEGORIES
        spike = (not is_purchase and len(prior) >= 2 and cur > mean + 2 * sd
                 and cur >= 1.25 * mean and cur - mean >= 100_000)
        rows.append({
            "category": cat, "kind": "purchases" if is_purchase else "overhead",
            "current_paise": cur, "prior_mean_paise": int(round(mean)),
            "change_pct": change_pct(cur, mean), "spike": bool(spike),
            "windows": {k: int(v) for k, v in vals.items()},
        })
    rows.sort(key=lambda r: (not r["spike"], -r["current_paise"]))
    spikes = [r["category"] for r in rows if r["spike"]]
    return insight(
        kind="expenses", title=f"Expenses - last {window_days} days vs earlier",
        as_of=ds.as_of.isoformat(),
        headline={"spikes": spikes,
                  "overheads_paise": sum(r["current_paise"] for r in rows if r["kind"] == "overhead"),
                  "purchases_paise": sum(r["current_paise"] for r in rows if r["kind"] == "purchases")},
        rows=rows,
        formula=(f"compare the latest {window_days}-day total per category with earlier "
                 f"{window_days}-day windows; spike if > mean + 2σ AND ≥ 1.25× mean AND "
                 "≥ ₹1,000 above mean (supplier purchases are excluded from spike checks)"),
        confidence="ok",
    )
