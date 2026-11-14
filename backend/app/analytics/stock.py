"""Demand, safety stock, reorder quantity, stock tiers, dead stock, slow movers.

Unique bits beyond the textbook formulas:
* festival-aware baseline - days inside a past festival window are excluded from
  "normal" demand, so the week after Diwali Muneem does not re-order Diwali volumes;
* forward festival multiplier for the coming lead+review horizon;
* lost-sales estimate - what you will miss even if you order right now;
* insufficient history -> "insufficient_data", never a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd

from app.analytics.common import Insight, insight, records
from app.analytics.festivals import FestivalCalendar
from app.core.money import add_gst, taxable_from_inclusive
from app.store.dataset import Dataset

TIER_ORDER = {"CRITICAL": 0, "SOON": 1, "OK": 2, "UNKNOWN": 3}

STOCK_FORMULA = (
    "d = average units/day over the last 14 days (days inside a past festival window are "
    "replaced by the most recent normal days); σ = std-dev of daily units over 28 days; "
    "d_adj = d × festival multiplier for the coming L+R days; safety_stock = Z × σ × √L; "
    "reorder_point = d_adj × L + safety_stock; "
    "order_qty = ceil(max(0, d_adj × (L + R) + safety_stock − stock)) rounded up to pack size; "
    "days_left = stock ÷ d_adj; tier = CRITICAL if days_left < L, SOON if < L + R, else OK"
)


@dataclass(frozen=True)
class StockParams:
    z: float = 1.65
    review_days: int = 2
    demand_window: int = 14
    sigma_window: int = 28
    min_history: int = 14
    lookback: int = 90
    dead_days: int = 30
    slow_ratio: float = 0.20

    @classmethod
    def from_settings(cls, s) -> "StockParams":
        return cls(z=s.service_z, review_days=s.review_period_days,
                   demand_window=s.demand_window_days, sigma_window=s.sigma_window_days,
                   min_history=s.min_history_days, dead_days=s.dead_stock_days,
                   slow_ratio=s.slow_mover_ratio)


def tier_for(days_left: float | None, lead: float, review: float) -> str:
    if days_left is None:
        return "OK"
    if days_left < lead:
        return "CRITICAL"
    if days_left < lead + review:
        return "SOON"
    return "OK"


def reorder_math(d: float, sigma: float, lead: float, review: float, stock: float,
                 pack: int = 1, z: float = 1.65) -> dict:
    """The core formula, kept tiny and pure so it can be golden-tested."""
    ss = z * sigma * math.sqrt(lead) if lead > 0 else 0.0
    rop = d * lead + ss
    need = d * (lead + review) + ss - stock
    qty_raw = math.ceil(need - 1e-9) if need > 0 else 0
    pack = max(int(pack), 1)
    qty = math.ceil(qty_raw / pack) * pack if qty_raw > 0 else 0
    days_left = (stock / d) if d > 0 else None
    return {
        "safety_stock": ss, "reorder_point": rop, "order_qty_raw": qty_raw,
        "order_qty": qty, "days_left": days_left, "tier": tier_for(days_left, lead, review),
    }


def _daily_matrix(ds: Dataset, lookback: int) -> pd.DataFrame:
    end = ds.as_of_ts
    start = end - pd.Timedelta(days=lookback - 1)
    s = ds.sales[(ds.sales["date"] >= start) & (ds.sales["date"] <= end)]
    idx = pd.date_range(start, end, freq="D")
    if s.empty:
        return pd.DataFrame(0.0, index=idx, columns=ds.items["item_id"])
    pv = s.pivot_table(index="date", columns="item_id", values="qty", aggfunc="sum",
                       fill_value=0)
    return pv.reindex(index=idx, columns=ds.items["item_id"], fill_value=0).astype(float)


def _unit_prices(ds: Dataset, days: int = 30) -> pd.Series:
    """Average realised taxable (ex-GST) price per unit, last `days` days."""
    w = ds.sales_window(days)
    if w.empty:
        return pd.Series(dtype=float)
    g = w.groupby("item_id").agg(t=("taxable_paise", "sum"), q=("qty", "sum"))
    return (g["t"] / g["q"]).where(g["q"] > 0)


def compute_stock(ds: Dataset, p: StockParams, cal: FestivalCalendar) -> pd.DataFrame:
    """One row per item with every input and output of the reorder maths (paise)."""
    daily = _daily_matrix(ds, p.lookback)
    dates = [ts.date() for ts in daily.index]
    as_of = ds.as_of
    fest_cache: dict[str, np.ndarray] = {}
    prices = _unit_prices(ds)
    first = ds.first_sale
    stock = ds.stock
    W, SW = p.demand_window, p.sigma_window
    rows = []
    for it in ds.items.itertuples(index=False):
        iid, cat = it.item_id, it.category
        L, R, pack = int(it.lead_time_days), int(p.review_days), int(it.pack_size)
        st = float(stock.get(iid, 0.0))
        if cat not in fest_cache:
            fest_cache[cat] = np.array([cal.is_festival_day(cat, d) for d in dates], dtype=bool)
        fs = first.get(iid)
        hist = (ds.as_of_ts - fs).days + 1 if fs is not None and pd.notna(fs) else 0
        mrp_taxable = int(taxable_from_inclusive(int(it.mrp_paise), int(it.gst_rate)))
        price = prices.get(iid)
        unit_price = int(round(price)) if price is not None and pd.notna(price) else mrp_taxable
        cost = int(it.cost_paise)
        base = {
            "item_id": iid, "name": it.name, "category": cat, "supplier_id": it.supplier_id,
            "stock": st, "pack_size": pack, "lead_time_days": L, "review_days": R,
            "history_days": int(hist), "unit_cost_paise": cost, "unit_price_paise": unit_price,
            "unit_margin_paise": unit_price - cost, "gst_rate": int(it.gst_rate),
        }
        if hist < p.min_history:
            rows.append({**base, "d_naive": None, "d_base": None, "d_adj": None, "sigma": None,
                         "festive_baseline_adjusted": False, "forward_multiplier": 1.0,
                         "festivals_ahead": [], "safety_stock": None, "reorder_point": None,
                         "order_qty_raw": None, "order_qty": 0, "days_left": None,
                         "tier": "UNKNOWN", "order_value_paise": 0,
                         "order_value_incl_gst_paise": 0, "shortfall_units": 0.0,
                         "lost_revenue_paise": 0, "lost_margin_paise": 0,
                         "naive_order_qty": None, "confidence": "insufficient_data",
                         "note": f"only {hist} day(s) of sales history; need {p.min_history}"})
            continue
        n = min(hist, len(dates))
        series = daily[iid].to_numpy()[-n:]
        fest = fest_cache[cat][-n:]
        d_naive = float(series[-W:].mean())
        sig_naive = float(np.std(series[-SW:], ddof=1)) if len(series[-SW:]) > 1 else 0.0
        adjusted, note = False, ""
        if fest[-W:].any():
            clean = series[~fest]
            if len(clean) >= W:
                d = float(clean[-W:].mean())
                sig_src = clean[-SW:]
                adjusted = True
                note = "festival days excluded from the baseline"
            else:
                d, sig_src = d_naive, series[-SW:]
                note = "inside a festival window without enough normal days to compare"
        else:
            d, sig_src = d_naive, series[-SW:]
        sigma = float(np.std(sig_src, ddof=1)) if len(sig_src) > 1 else 0.0
        mult, ahead = cal.forward_multiplier(cat, as_of + timedelta(days=1),
                                             as_of + timedelta(days=L + R))
        d_adj, sigma_adj = d * mult, sigma * mult
        m = reorder_math(d_adj, sigma_adj, L, R, st, pack, p.z)
        naive_qty = (reorder_math(d_naive, sig_naive, L, R, st, pack, p.z)["order_qty"]
                     if adjusted else None)
        shortfall = max(0.0, d_adj * L - st)
        ov = m["order_qty"] * cost
        rows.append({
            **base, "d_naive": round(d_naive, 3), "d_base": round(d, 3),
            "d_adj": round(d_adj, 3), "sigma": round(sigma_adj, 3),
            "festive_baseline_adjusted": adjusted, "forward_multiplier": mult,
            "festivals_ahead": ahead, "safety_stock": round(m["safety_stock"], 2),
            "reorder_point": round(m["reorder_point"], 2), "order_qty_raw": m["order_qty_raw"],
            "order_qty": m["order_qty"],
            "days_left": None if m["days_left"] is None else round(m["days_left"], 2),
            "tier": m["tier"], "order_value_paise": ov,
            "order_value_incl_gst_paise": int(add_gst(ov, int(it.gst_rate))),
            "shortfall_units": round(shortfall, 2),
            "lost_revenue_paise": int(round(shortfall * unit_price)),
            "lost_margin_paise": int(round(shortfall * (unit_price - cost))),
            "naive_order_qty": naive_qty, "confidence": "ok", "note": note,
        })
    df = pd.DataFrame(rows)
    df["_tier_rank"] = df["tier"].map(TIER_ORDER)
    return df.sort_values(["_tier_rank", "days_left", "item_id"],
                          na_position="last").drop(columns="_tier_rank").reset_index(drop=True)


def stock_status_insight(stock_df: pd.DataFrame, ds: Dataset, item_ids=None) -> Insight:
    df = stock_df if item_ids is None else stock_df[stock_df["item_id"].isin(item_ids)]
    counts = df["tier"].value_counts().to_dict()
    return insight(
        kind="stock_status", title="Stock status", as_of=ds.as_of.isoformat(),
        headline={"critical": counts.get("CRITICAL", 0), "soon": counts.get("SOON", 0),
                  "ok": counts.get("OK", 0), "insufficient_data": counts.get("UNKNOWN", 0),
                  "items": len(df)},
        rows=df.to_dict("records"), formula=STOCK_FORMULA,
        confidence="insufficient_data" if len(df) and (df["tier"] == "UNKNOWN").all() else "ok",
        assumptions=["Z = 1.65 (~95% service level)", "lead time from item/supplier",
                     "festival dates are indicative"],
    )


# ------------------------------------------------------------------ reorder plan
def reorder_plan(stock_df: pd.DataFrame, ds: Dataset, budget_paise: int | None = None,
                 supplier_id: str | None = None) -> Insight:
    df = stock_df[(stock_df["order_qty"] > 0) & (stock_df["confidence"] == "ok")].copy()
    below_rop = df["stock"] <= df["reorder_point"]
    df = df[(df["tier"] != "OK") | below_rop]
    if supplier_id:
        df = df[df["supplier_id"] == supplier_id]
    df["_rank"] = df["tier"].map(TIER_ORDER)
    df = df.sort_values(["_rank", "lost_revenue_paise", "days_left"],
                        ascending=[True, False, True]).drop(columns="_rank")
    sup_names = ds.supplier_index["name"] if len(ds.suppliers) else pd.Series(dtype=str)
    rows, deferred = [], []
    remaining = budget_paise
    for r in records(df):
        qty, status = r["order_qty"], "full"
        if remaining is not None:
            unit_cash = add_gst(r["unit_cost_paise"], r["gst_rate"])
            if qty * unit_cash > remaining:
                packs = remaining // (r["pack_size"] * unit_cash) if unit_cash else 0
                qty = int(packs * r["pack_size"])
                status = "reduced" if qty > 0 else "deferred"
        if status == "deferred":
            deferred.append({"item_id": r["item_id"], "name": r["name"], "tier": r["tier"],
                             "wanted_qty": r["order_qty"]})
            continue
        value = qty * r["unit_cost_paise"]
        cash = int(add_gst(value, r["gst_rate"]))
        if remaining is not None:
            remaining = max(0, remaining - cash)
        dl = r["days_left"]
        why = (f"stock {r['stock']:g} lasts {dl:g} day(s); supplier needs {r['lead_time_days']}"
               if dl is not None else "below reorder point")
        rows.append({
            "item_id": r["item_id"], "name": r["name"], "supplier_id": r["supplier_id"],
            "supplier": sup_names.get(r["supplier_id"], r["supplier_id"]), "tier": r["tier"],
            "stock": r["stock"], "days_left": dl, "d_adj": r["d_adj"],
            "order_qty": qty, "wanted_qty": r["order_qty"], "pack_size": r["pack_size"],
            "status": status, "order_value_paise": value, "order_value_incl_gst_paise": cash,
            "lost_revenue_paise": r["lost_revenue_paise"], "why": why,
            "inputs": {"d": r["d_adj"], "sigma": r["sigma"], "L": r["lead_time_days"],
                       "R": r["review_days"], "stock": r["stock"],
                       "safety_stock": r["safety_stock"], "pack": r["pack_size"]},
        })
    by_sup: dict[str, dict] = {}
    for r in rows:
        s = by_sup.setdefault(r["supplier_id"], {"supplier_id": r["supplier_id"],
                                                 "supplier": r["supplier"], "items": 0,
                                                 "order_value_paise": 0,
                                                 "order_value_incl_gst_paise": 0,
                                                 "critical": 0})
        s["items"] += 1
        s["order_value_paise"] += r["order_value_paise"]
        s["order_value_incl_gst_paise"] += r["order_value_incl_gst_paise"]
        s["critical"] += int(r["tier"] == "CRITICAL")
    total = sum(r["order_value_paise"] for r in rows)
    total_cash = sum(r["order_value_incl_gst_paise"] for r in rows)
    return insight(
        kind="reorder_plan", title="What to order", as_of=ds.as_of.isoformat(),
        headline={"items": len(rows), "suppliers": len(by_sup),
                  "order_value_paise": total, "cash_needed_paise": total_cash,
                  "critical_items": sum(r["tier"] == "CRITICAL" for r in rows),
                  "deferred_items": len(deferred), "budget_paise": budget_paise},
        rows=rows, formula=STOCK_FORMULA,
        meta={"by_supplier": sorted(by_sup.values(), key=lambda s: (-s["critical"],
                                                                     -s["order_value_paise"])),
              "deferred": deferred},
        assumptions=["Order value at cost ex-GST; cash needed includes GST on purchase",
                     "Critical items first, then by sales you would lose"],
    )


# ------------------------------------------------------------------ dead / slow
def dead_stock(ds: Dataset, days: int = 30) -> Insight:
    cutoff = ds.as_of_ts - pd.Timedelta(days=days - 1)
    last = ds.last_sale
    rows = []
    for it in ds.items.itertuples(index=False):
        st = float(ds.stock.get(it.item_id, 0.0))
        if st <= 0:
            continue
        ls = last.get(it.item_id)
        if ls is not None and pd.notna(ls) and ls >= cutoff:
            continue
        cost = int(it.cost_paise)
        breakeven = int(add_gst(cost, int(it.gst_rate)))
        rows.append({
            "item_id": it.item_id, "name": it.name, "category": it.category,
            "supplier_id": it.supplier_id, "stock": st,
            "last_sale": None if ls is None or pd.isna(ls) else ls,
            "days_since_last_sale": None if ls is None or pd.isna(ls) else (ds.as_of_ts - ls).days,
            "money_trapped_paise": int(round(st * cost)),
            "mrp_paise": int(it.mrp_paise), "breakeven_price_paise": breakeven,
            "max_discount_pct": round(100 * (1 - breakeven / it.mrp_paise), 1) if it.mrp_paise else 0,
        })
    rows.sort(key=lambda r: -r["money_trapped_paise"])
    total = sum(r["money_trapped_paise"] for r in rows)
    return insight(
        kind="dead_stock", title=f"Dead stock (no sale in {days} days)", as_of=ds.as_of.isoformat(),
        headline={"items": len(rows), "money_trapped_paise": total, "days": days},
        rows=rows,
        formula=(f"dead if stock > 0 and no sale in the last {days} days; "
                 "money_trapped = stock × cost_ex_gst; breakeven_price = cost × (1 + GST); "
                 "max_discount = 1 − breakeven ÷ MRP"),
        confidence="ok" if rows else "no_data",
    )


def slow_movers(ds: Dataset, ratio: float = 0.2, days: int = 30) -> Insight:
    w = ds.sales_window(days)
    vel = (w.groupby("item_id")["qty"].sum() / days).reindex(ds.items["item_id"], fill_value=0.0)
    items = ds.items.set_index("item_id")
    df = pd.DataFrame({"velocity": vel, "category": items["category"], "name": items["name"],
                       "stock": ds.stock, "cost_paise": items["cost_paise"]})
    med = df[df["velocity"] > 0].groupby("category")["velocity"].median()
    df["category_median"] = df["category"].map(med)
    slow = df[(df["velocity"] > 0) & (df["velocity"] < ratio * df["category_median"])].copy()
    slow["stock_value_paise"] = (slow["stock"] * slow["cost_paise"]).round().astype("int64")
    slow["days_of_cover"] = (slow["stock"] / slow["velocity"]).round(1)
    rows = slow.reset_index().rename(columns={"index": "item_id"}).sort_values(
        "stock_value_paise", ascending=False)
    rows = rows[["item_id", "name", "category", "velocity", "category_median", "stock",
                 "days_of_cover", "stock_value_paise"]]
    return insight(
        kind="slow_movers", title="Slow movers", as_of=ds.as_of.isoformat(),
        headline={"items": len(rows), "stock_value_paise": int(rows["stock_value_paise"].sum())},
        rows=rows.to_dict("records"),
        formula=f"velocity = units in last {days} days ÷ {days}; slow if velocity < "
                f"{int(ratio * 100)}% of its category median",
        confidence="ok" if len(rows) else "no_data",
    )
