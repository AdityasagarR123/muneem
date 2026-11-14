"""Metrics layer - one tested definition per business metric, used by the dashboard, the
insight engine and the chat agent alike.

    query_metric(ds, "revenue", group_by="category", period="this_month",
                 compare="same_period_last_month")

The agent fills these arguments; it never writes SQL or computes a number itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from app.analytics.common import Insight, change_pct, insight, pct
from app.store.dataset import Dataset

PERIODS = ("last_7_days", "last_30_days", "last_90_days", "this_week", "last_week", "this_month",
           "last_month", "all")
COMPARES = ("previous_period", "same_period_last_month", "none")
DIMENSIONS = ("day", "week", "month", "weekday", "category", "item", "supplier", "payment_mode",
              "customer", "customer_segment", "area")
TIME_DIMS = ("day", "week", "month")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class Metric:
    label: str
    unit: str  # paise | count | pct | units
    formula: str
    fn: Callable[[dict], float | None]
    customer: bool = False  # depends on customer-linked bills


def _div(a, b):
    return None if not b else a / b


METRICS: dict[str, Metric] = {
    "revenue": Metric("Sales (incl. GST)", "paise", "Σ qty × price paid", lambda b: b["revenue"]),
    "taxable_revenue": Metric("Sales before GST", "paise", "Σ price ÷ (1 + GST%)", lambda b: b["taxable"]),
    "gst": Metric("GST collected", "paise", "sales − sales before GST", lambda b: b["gst"]),
    "gross_margin": Metric("Margin after GST", "paise", "sales before GST − qty × cost",
                           lambda b: b["margin"]),
    "margin_pct": Metric("Margin %", "pct", "margin ÷ sales before GST × 100",
                         lambda b: None if not b["taxable"] else round(100 * b["margin"] / b["taxable"], 2)),
    "orders": Metric("Bills", "count", "number of distinct bills", lambda b: b["orders"]),
    "units": Metric("Units sold", "units", "Σ qty", lambda b: b["units"]),
    "aov": Metric("Average bill", "paise", "sales ÷ bills",
                  lambda b: None if not b["orders"] else round(b["revenue"] / b["orders"])),
    "customers": Metric("Customers who bought", "count", "distinct customers on linked bills",
                        lambda b: b["customers"], True),
    "new_customers": Metric("New customers", "count", "customers whose first-ever bill is in the period",
                            lambda b: b["new_customers"], True),
    "returning_customers": Metric("Returning customers", "count",
                                  "customers in the period whose first bill was earlier",
                                  lambda b: b["customers"] - b["new_customers"], True),
    "repeat_rate_pct": Metric("Returning customer share", "pct", "returning ÷ customers × 100",
                              lambda b: None if not b["customers"] else
                              round(100 * (b["customers"] - b["new_customers"]) / b["customers"], 2), True),
    "returning_customer_bills": Metric("Bills from returning customers", "count",
                                       "bills linked to customers first seen before the period",
                                       lambda b: b["returning_orders"], True),
    "revenue_per_customer": Metric("Spend per customer", "paise",
                                   "sales on linked bills ÷ customers",
                                   lambda b: None if not b["customers"] else
                                   round(b["linked_revenue"] / b["customers"]), True),
    "customer_coverage_pct": Metric("Bills linked to a customer", "pct", "linked bills ÷ bills × 100",
                                    lambda b: None if not b["orders"] else
                                    round(100 * b["linked_orders"] / b["orders"], 2)),
}


# ------------------------------------------------------------------ periods
def _month_shift(d: date, months: int) -> date:
    y, m = d.year + (d.month - 1 + months) // 12, (d.month - 1 + months) % 12 + 1
    last = (date(y + (m // 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def resolve_period(as_of: date, period: str, first_day: date | None = None) -> tuple[date, date, str]:
    if period.startswith("last_") and period.endswith("_days"):
        n = int(period[5:-5])
        return as_of - timedelta(days=n - 1), as_of, f"last {n} days"
    if period == "this_week":
        return as_of - timedelta(days=as_of.weekday()), as_of, "this week so far"
    if period == "last_week":
        end = as_of - timedelta(days=as_of.weekday() + 1)
        return end - timedelta(days=6), end, "last week"
    if period == "this_month":
        return as_of.replace(day=1), as_of, f"{as_of:%B} so far"
    if period == "last_month":
        end = as_of.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, f"{end:%B %Y}"
    if period == "all":
        return first_day or as_of, as_of, "all data"
    raise ValueError(f"Unknown period '{period}'. Use one of {', '.join(PERIODS)} or last_N_days")


def comparison_period(start: date, end: date, mode: str) -> tuple[date, date, str] | None:
    if mode == "none":
        return None
    if mode == "previous_period":
        n = (end - start).days + 1
        cend = start - timedelta(days=1)
        return cend - timedelta(days=n - 1), cend, "previous period"
    if mode == "same_period_last_month":
        return _month_shift(start, -1), _month_shift(end, -1), "same days last month"
    raise ValueError(f"Unknown comparison '{mode}'")


# ------------------------------------------------------------------ aggregation
def _lines(ds: Dataset, start: date, end: date, filters: dict, seg: dict) -> pd.DataFrame:
    s = ds.sales_x
    s = s[(s["date"] >= pd.Timestamp(start)) & (s["date"] <= pd.Timestamp(end))]
    if filters.get("category"):
        s = s[s["category"] == filters["category"]]
    if filters.get("item_id"):
        s = s[s["item_id"] == filters["item_id"]]
    if filters.get("supplier_id"):
        s = s[s["supplier_id"] == filters["supplier_id"]]
    if filters.get("payment_mode"):
        s = s[s["payment_mode"] == filters["payment_mode"]]
    if filters.get("customer_segment"):
        s = s[s["customer_id"].map(seg).eq(filters["customer_segment"])]
    if filters.get("customer_id"):
        s = s[s["customer_id"] == filters["customer_id"]]
    return s


def _group_key(ds: Dataset, s: pd.DataFrame, by: str, seg: dict) -> pd.Series:
    if by == "day":
        return s["date"].dt.strftime("%Y-%m-%d")
    if by == "week":
        return (s["date"] - pd.to_timedelta(s["date"].dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    if by == "month":
        return s["date"].dt.strftime("%Y-%m")
    if by == "weekday":
        return s["date"].dt.weekday.map(dict(enumerate(WEEKDAYS)))
    if by == "category":
        return s["category"]
    if by == "item":
        return s["name"]
    if by == "supplier":
        names = ds.supplier_index["name"] if len(ds.suppliers) else pd.Series(dtype=str)
        return s["supplier_id"].map(names).fillna(s["supplier_id"])
    if by == "payment_mode":
        return s["payment_mode"]
    if by == "customer":
        names = ds.customers.set_index("customer_id")["name"]
        lab = s["customer_id"].map(names).fillna("")
        # anonymous bills are not a customer: left out (NaN keys are dropped by groupby)
        return lab.where(lab != "", s["customer_id"]).where(s["customer_id"] != "")
    if by == "customer_segment":
        return s["customer_id"].map(seg).fillna("(no customer)")
    if by == "area":
        areas = ds.customers.set_index("customer_id")["area"]
        return s["customer_id"].map(areas).replace("", None).fillna("(unknown)")
    raise ValueError(f"Unknown dimension '{by}'. Use one of {', '.join(DIMENSIONS)}")


def _base(s: pd.DataFrame, first_purchase: pd.Series, start: date) -> dict:
    linked = s[s["customer_id"] != ""]
    fp = linked["customer_id"].map(first_purchase)
    new_ids = linked.loc[fp >= pd.Timestamp(start), "customer_id"]
    returning = linked[fp < pd.Timestamp(start)]
    return {
        "revenue": int(s["line_total_paise"].sum()), "taxable": int(s["taxable_paise"].sum()),
        "gst": int(s["gst_paise"].sum()), "margin": int(s["margin_paise"].sum()),
        "units": float(s["qty"].sum()), "orders": int(s["invoice_id"].nunique()),
        "customers": int(linked["customer_id"].nunique()), "new_customers": int(new_ids.nunique()),
        "linked_orders": int(linked["invoice_id"].nunique()),
        "linked_revenue": int(linked["line_total_paise"].sum()),
        "returning_orders": int(returning["invoice_id"].nunique()),
    }


def _value(metric: Metric, s: pd.DataFrame, fp: pd.Series, start: date):
    return metric.fn(_base(s, fp, start)) if len(s) else (0 if metric.unit in ("paise", "count", "units") else None)


def query_metric(ds: Dataset, metric: str, group_by: str | None = None,
                 period: str = "last_30_days", compare: str = "previous_period",
                 filters: dict | None = None, segments: dict | None = None,
                 limit: int = 25) -> Insight:
    if metric not in METRICS:
        raise ValueError(f"Unknown metric '{metric}'. Use one of {', '.join(METRICS)}")
    m = METRICS[metric]
    filters, seg = filters or {}, segments or {}
    first_day = ds.sales["date"].min().date() if len(ds.sales) else ds.as_of
    start, end, label = resolve_period(ds.as_of, period, first_day)
    cmp = comparison_period(start, end, compare)
    fp = ds.customer_first_purchase
    cur = _lines(ds, start, end, filters, seg)
    prev = _lines(ds, cmp[0], cmp[1], filters, seg) if cmp else None
    money = m.unit == "paise"
    vk = "value_paise" if money else "value"
    ck = "compare_value_paise" if money else "compare_value"
    dk = "change_paise" if money else "change"

    total = _value(m, cur, fp, start)
    total_prev = _value(m, prev, fp, cmp[0]) if cmp else None
    has_prev = cmp is not None and cmp[0] >= first_day

    rows: list[dict] = []
    if group_by:
        key = _group_key(ds, cur, group_by, seg)
        groups = {g: part for g, part in cur.groupby(key)}
        prev_groups = {}
        if prev is not None and group_by not in TIME_DIMS:
            prev_groups = {g: part for g, part in prev.groupby(_group_key(ds, prev, group_by, seg))}
        for g, part in groups.items():
            # "new customer" is judged against the start of each time bucket, not the whole period
            gstart = max(start, part["date"].min().date().replace(day=1)) if group_by == "month" else \
                max(start, part["date"].min().date() - timedelta(days=part["date"].min().weekday())) \
                if group_by == "week" else part["date"].min().date() if group_by == "day" else start
            v = _value(m, part, fp, gstart)
            row = {"group": g, vk: v}
            if prev_groups is not None and group_by not in TIME_DIMS and has_prev:
                pv = _value(m, prev_groups[g], fp, cmp[0]) if g in prev_groups else 0
                row[ck] = pv
                row[dk] = None if v is None or pv is None else v - pv
                row["change_pct"] = change_pct(v, pv)
            rows.append(row)
        if group_by in TIME_DIMS or group_by == "weekday":
            order = WEEKDAYS if group_by == "weekday" else None
            rows.sort(key=lambda r: order.index(r["group"]) if order else r["group"])
        else:
            rows.sort(key=lambda r: -(r[vk] or 0))
            if m.unit in ("paise", "count", "units") and total:
                for r in rows:
                    r["share_pct"] = pct(r[vk] or 0, total)
            rows = rows[:limit]
    coverage = None
    if m.customer:
        orders = cur["invoice_id"].nunique()
        coverage = pct(cur.loc[cur["customer_id"] != "", "invoice_id"].nunique(), orders)
    headline = {
        "metric": metric, "label": m.label, "unit": m.unit, "period": period, "period_label": label,
        "from": start.isoformat(), "to": end.isoformat(), vk: total,
        "group_by": group_by, "filters": filters,
    }
    if cmp:
        headline.update({"compare_label": cmp[2], "compare_from": cmp[0].isoformat(),
                         "compare_to": cmp[1].isoformat()})
        if has_prev:
            headline[ck] = total_prev
            headline[dk] = None if total is None or total_prev is None else total - total_prev
            headline["change_pct"] = change_pct(total, total_prev)
    notes = []
    if cmp and not has_prev:
        notes.append("No data for the comparison period")
    if coverage is not None:
        notes.append(f"Customer figures cover {coverage}% of bills (only bills linked to a customer)")
    return insight(
        kind="metric", title=f"{m.label} - {label}" + (f" by {group_by.replace('_', ' ')}" if group_by else ""),
        as_of=ds.as_of.isoformat(), headline=headline, rows=rows,
        formula=f"{m.label} = {m.formula}; period {start} to {end}"
                + (f"; compared with {cmp[0]} to {cmp[1]}" if cmp and has_prev else ""),
        assumptions=notes, meta={"coverage_pct": coverage},
        confidence="ok" if len(cur) else "no_data")
