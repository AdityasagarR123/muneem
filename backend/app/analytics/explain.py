"""explain_change - "why did revenue drop this week?" answered deterministically.

Three views of the same change:
1. Bridge   revenue = bills × average bill; the change is split between the two with
            log-shares, so the parts add up exactly.
2. Causes   estimated ₹ effect of known reasons, in this order:
            calendar (weekday mix) · festival · regulars who stopped coming · probable
            stock-outs · price changes · remainder ("other / normal variation").
            Every effect is an estimate with its method stated; the remainder makes the
            parts add up to the actual change to the paisa.
3. Movers   which categories and items moved most (supporting detail).
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.analytics.common import Insight, change_pct, insight, pct
from app.analytics.festivals import FestivalCalendar
from app.analytics.metrics import comparison_period, resolve_period
from app.store.dataset import Dataset

BASELINE_DAYS = 56


def _daily(ds: Dataset, start: date, end: date, by: str | None = None) -> pd.DataFrame | pd.Series:
    s = ds.sales_x
    s = s[(s["date"] >= pd.Timestamp(start)) & (s["date"] <= pd.Timestamp(end))]
    if by is None:
        return s.groupby("date")["line_total_paise"].sum()
    return s.groupby(["date", by])["line_total_paise"].sum().unstack(fill_value=0)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _is_fest_day(cal: FestivalCalendar, d: date) -> bool:
    return any(f.contains(d) for f in cal.festivals)


def _weekday_baseline(ds: Dataset, cal: FestivalCalendar, before: date) -> tuple[dict, dict]:
    """Average revenue per weekday (total and per category) over the 8 weeks before
    `before`, skipping festival days so the baseline is 'normal'."""
    start = before - timedelta(days=BASELINE_DAYS)
    tot = _daily(ds, start, before - timedelta(days=1))
    cat = _daily(ds, start, before - timedelta(days=1), "category")
    days = [d for d in _days(start, before - timedelta(days=1)) if not _is_fest_day(cal, d)]
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    tot = tot.reindex(idx, fill_value=0)
    cat = cat.reindex(idx, fill_value=0)
    wd_tot = tot.groupby(tot.index.weekday).mean().to_dict()
    wd_cat = cat.groupby(cat.index.weekday).mean().to_dict("index") if len(cat.columns) else {}
    overall = float(tot.mean()) if len(tot) else 0.0
    for w in range(7):
        wd_tot.setdefault(w, overall)
    return wd_tot, wd_cat


def explain_change(ds: Dataset, cal: FestivalCalendar, period: str = "last_7_days",
                   compare: str = "previous_period", at_risk_ids: set[str] | None = None) -> Insight:
    first_day = ds.sales["date"].min().date()
    s1, e1, label = resolve_period(ds.as_of, period, first_day)
    cmp = comparison_period(s1, e1, compare if compare != "none" else "previous_period")
    s0, e0, clabel = cmp
    sx = ds.sales_x
    cur = sx[(sx["date"] >= pd.Timestamp(s1)) & (sx["date"] <= pd.Timestamp(e1))]
    prev = sx[(sx["date"] >= pd.Timestamp(s0)) & (sx["date"] <= pd.Timestamp(e0))]
    r1, r0 = int(cur["line_total_paise"].sum()), int(prev["line_total_paise"].sum())
    delta = r1 - r0
    if s0 < first_day or r0 == 0:
        return insight(kind="explain_change", title="Why sales changed", confidence="no_data",
                       as_of=ds.as_of.isoformat(),
                       assumptions=["Not enough history before this period to compare"])

    # ---- 1. bridge: bills x average bill --------------------------------------
    b1, b0 = cur["invoice_id"].nunique(), prev["invoice_id"].nunique()
    a1, a0 = r1 / b1 if b1 else 0, r0 / b0 if b0 else 0
    bridge = []
    if r1 > 0 and b0 and b1 and a0 and a1:
        lt = math.log(r1 / r0) if r1 != r0 else 0.0
        lb, la = math.log(b1 / b0), math.log(a1 / a0)
        eb = int(round(delta * lb / lt)) if lt else 0
        bridge = [
            {"part": "Number of bills", "before": b0, "now": b1, "change_pct": change_pct(b1, b0),
             "effect_paise": eb},
            {"part": "Average bill", "before_paise": int(round(a0)), "now_paise": int(round(a1)),
             "change_pct": change_pct(a1, a0), "effect_paise": delta - eb},
        ]

    # ---- 2. causes -------------------------------------------------------------
    wd_tot, wd_cat = _weekday_baseline(ds, cal, s0)
    days1, days0 = _days(s1, e1), _days(s0, e0)
    exp1 = sum(wd_tot[d.weekday()] for d in days1)
    exp0 = sum(wd_tot[d.weekday()] for d in days0)
    causes: list[dict] = []
    calendar_eff = int(round(exp1 - exp0))

    def wk(days: list[date]) -> int:
        return sum(d.weekday() >= 5 for d in days)

    wk1, wk0 = wk(days1), wk(days0)
    cal_name = ("Fewer weekend days than before" if wk1 < wk0 else
                "More weekend days than before" if wk1 > wk0 else "Weekday mix")
    causes.append({
        "driver": cal_name, "kind": "calendar",
        "effect_paise": calendar_eff,
        "detail": {"weekend_days_now": wk1, "weekend_days_before": wk0,
                   "days_now": len(days1), "days_before": len(days0)},
        "method": "normal revenue for each weekday (8 weeks before, festival days excluded) "
                  "summed over each period's days; effect = expected now − expected before",
    })

    def festival_excess(days: list[date], frame: pd.DataFrame) -> tuple[int, list[str]]:
        total, names = 0.0, set()
        if frame.empty:
            return 0, []
        bycat = frame.groupby(["date", "category"])["line_total_paise"].sum()
        for d in days:
            for f in cal.festivals:
                if not f.contains(d):
                    continue
                for cat in f.uplift:
                    actual = float(bycat.get((pd.Timestamp(d), cat), 0))
                    base = float(wd_cat.get(d.weekday(), {}).get(cat, 0.0))
                    total += actual - base
                    names.add(f.name)
        return int(round(total)), sorted(names)

    f1, n1 = festival_excess(days1, cur)
    f0, n0 = festival_excess(days0, prev)
    fest_eff = f1 - f0
    if f1 or f0:
        fname = ", ".join(sorted(set(n1) | set(n0)))
        causes.append({
            "driver": (f"{fname} rush was in the earlier period" if f0 > f1 else f"{fname} demand"),
            "kind": "festival",
            "effect_paise": fest_eff, "detail": {"extra_now_paise": f1, "extra_before_paise": f0},
            "method": "festival-category sales on festival days minus their normal weekday level; "
                      "effect = extra now − extra before",
        })

    if at_risk_ids:
        lap1 = int(cur.loc[cur["customer_id"].isin(at_risk_ids), "line_total_paise"].sum())
        lap0 = int(prev.loc[prev["customer_id"].isin(at_risk_ids), "line_total_paise"].sum())
        if lap0 or lap1:
            n_lost = prev.loc[prev["customer_id"].isin(at_risk_ids) &
                              ~prev["customer_id"].isin(set(cur["customer_id"])), "customer_id"].nunique()
            causes.append({
                "driver": "Regular customers who stopped coming", "kind": "lapsed_customers",
                "effect_paise": lap1 - lap0, "detail": {"customers_absent_now": int(n_lost)},
                "method": "spend now − spend before by customers currently flagged as churn risk",
            })

    q1 = cur.groupby("item_id")["qty"].sum() / len(days1)
    q0 = prev.groupby("item_id")["qty"].sum() / len(days0)
    price0 = (prev.groupby("item_id")["line_total_paise"].sum() / prev.groupby("item_id")["qty"].sum())
    stock = ds.stock
    so_rows, so_eff = [], 0
    fest_before = {cat for d in days0 for f in cal.festivals if f.contains(d) for cat in f.uplift}
    for iid, base in q0.items():
        now = float(q1.get(iid, 0.0))
        if ds.item_index["category"].get(iid) in fest_before:
            continue  # demand ended with the festival - not a stock-out
        if base >= 1 and now < 0.3 * base and float(stock.get(iid, 0)) < base:
            lost = (base - now) * len(days1) * float(price0.get(iid, 0))
            so_eff -= int(round(lost))
            so_rows.append({"item_id": iid, "name": ds.item_index["name"].get(iid, iid),
                            "per_day_before": round(base, 2), "per_day_now": round(now, 2),
                            "stock_now": float(stock.get(iid, 0)), "lost_paise": int(round(lost))})
    if so_rows:
        causes.append({
            "driver": "Probable stock-outs", "kind": "stockout", "effect_paise": so_eff,
            "detail": {"items": so_rows},
            "method": "items that sold ≥1/day before, sell <30% of that now and have less than a "
                      "day of stock: (units/day before − now) × days × price",
        })

    p1 = cur.groupby("item_id").agg(rev=("line_total_paise", "sum"), q=("qty", "sum"))
    common = p1.index.intersection(price0.index)
    unit1 = p1.loc[common, "rev"] / p1.loc[common, "q"]
    pdiff = unit1 - price0.loc[common]
    meaningful = (pdiff.abs() / price0.loc[common]) > 0.01
    price_eff = int(round(float((pdiff[meaningful] * p1.loc[common, "q"][meaningful]).sum())))
    if abs(price_eff) >= 0.01 * r0:
        causes.append({
            "driver": "Price changes / discounts", "kind": "price", "effect_paise": price_eff,
            "method": "Σ (average price now − before) × units sold now, items whose price moved >1%",
        })

    explained = sum(c["effect_paise"] for c in causes)
    causes.append({"driver": "Other / normal variation", "kind": "remainder",
                   "effect_paise": delta - explained,
                   "method": "actual change − all estimated effects above"})
    for c in causes:
        c["share_of_change_pct"] = pct(c["effect_paise"], delta) if delta else None

    # ---- 3. movers ----------------------------------------------------------------
    def movers(col: str, k: int = 4) -> list[dict]:
        a = cur.groupby(col)["line_total_paise"].sum()
        b = prev.groupby(col)["line_total_paise"].sum()
        d = a.sub(b, fill_value=0).sort_values(key=lambda x: -x.abs()).head(k)
        names = ds.item_index["name"] if col == "item_id" else None
        return [{"name": names.get(i, i) if names is not None else i, "now_paise": int(a.get(i, 0)),
                 "before_paise": int(b.get(i, 0)), "change_paise": int(v),
                 "change_pct": change_pct(a.get(i, 0), b.get(i, 0))} for i, v in d.items()]

    ranked = sorted([c for c in causes if c["kind"] != "remainder"],
                    key=lambda c: -abs(c["effect_paise"]))
    top = [c for c in ranked if delta and abs(c["effect_paise"]) >= 0.1 * abs(delta)
           and np.sign(c["effect_paise"]) == np.sign(delta)][:3]
    return insight(
        kind="explain_change",
        title=f"Why sales changed - {label} vs {clabel}", as_of=ds.as_of.isoformat(),
        headline={
            "period_label": label, "from": s1.isoformat(), "to": e1.isoformat(),
            "compare_label": clabel, "compare_from": s0.isoformat(), "compare_to": e0.isoformat(),
            "now_paise": r1, "before_paise": r0, "change_paise": delta,
            "change_pct": change_pct(r1, r0), "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
            "main_reasons": [c["driver"] for c in top],
            "explained_pct": pct(sum(c["effect_paise"] for c in ranked), delta) if delta else None,
        },
        rows=causes,
        meta={"bridge": bridge, "category_movers": movers("category"), "item_movers": movers("item_id")},
        formula=("change = sales now − sales before; bridge: sales = bills × average bill (log split); "
                 "causes are estimates (method on each row) and the remainder makes them add up to "
                 "the actual change"),
        assumptions=["Cause estimates can overlap slightly; they are indicators, not exact accounting",
                     "Festival dates come from the festival calendar"],
    )
