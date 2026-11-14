"""Graph-powered insights. The graph engine (ArangoDB or memory) supplies the
connections; the money and stock numbers come from the analytics frames."""

from __future__ import annotations

import math

import pandas as pd

from app.analytics.common import Insight, insight, pct, records
from app.analytics.stock import TIER_ORDER
from app.graph import mermaid
from app.store.dataset import Dataset


def _stock_map(stock_df: pd.DataFrame) -> dict[str, dict]:
    return {r["item_id"]: r for r in records(stock_df)}


def _name(ds: Dataset, iid: str) -> str:
    return ds.item_index["name"].get(iid, iid)


# ------------------------------------------------------------------ suppliers
def supplier_priority(stock_df: pd.DataFrame, graph, ds: Dataset) -> Insight:
    sm = _stock_map(stock_df)
    rows = []
    for s in graph.suppliers_with_items():
        risky = [sm[i] | {"item_id": i} for i in s["item_ids"]
                 if i in sm and sm[i]["tier"] in ("CRITICAL", "SOON")]
        if not risky:
            continue
        days = [r["days_left"] for r in risky if r["days_left"] is not None]
        rows.append({
            "supplier_id": s["supplier_id"], "supplier": s["name"],
            "critical": sum(r["tier"] == "CRITICAL" for r in risky),
            "soon": sum(r["tier"] == "SOON" for r in risky),
            "items": [{"item_id": r["item_id"], "name": r["name"], "tier": r["tier"],
                       "days_left": r["days_left"], "order_qty": r["order_qty"]} for r in
                      sorted(risky, key=lambda r: (TIER_ORDER[r["tier"]], r["days_left"] or 0))],
            "order_value_paise": int(sum(r["order_value_paise"] for r in risky)),
            "cash_needed_paise": int(sum(r["order_value_incl_gst_paise"] for r in risky)),
            "lost_revenue_paise": int(sum(r["lost_revenue_paise"] for r in risky)),
            "first_stockout_days": min(days) if days else None,
            "lead_time_days": max(r["lead_time_days"] for r in risky),
        })
    rows.sort(key=lambda r: (-r["critical"], -r["lost_revenue_paise"], -r["soon"],
                             -r["order_value_paise"]))
    for rank, r in enumerate(rows, 1):
        r["rank"] = rank
    return insight(
        kind="supplier_priority", title="Which supplier to call first",
        as_of=ds.as_of.isoformat(), engine=graph.engine,
        headline={"call_first": rows[0]["supplier"] if rows else None,
                  "call_first_id": rows[0]["supplier_id"] if rows else None,
                  "suppliers_to_call": len(rows)},
        rows=rows,
        formula=("graph: supplier -supplies-> item; for each supplier, count its CRITICAL/SOON "
                 "items; rank by critical count, then sales you would lose before delivery "
                 "(shortfall = max(0, d × L − stock) × price), then soon count"),
        confidence="ok" if rows else "no_data",
    )


def supplier_risk(stock_df: pd.DataFrame, graph, ds: Dataset, supplier_id: str,
                  delay_days: int = 2) -> Insight:
    sm = _stock_map(stock_df)
    item_ids = graph.supplier_items(supplier_id)
    sname = ds.supplier_index["name"].get(supplier_id, supplier_id) if len(ds.suppliers) else supplier_id
    rows = []
    for iid in item_ids:
        r = sm.get(iid)
        if r is None or r["confidence"] != "ok":
            continue
        L2 = r["lead_time_days"] + delay_days
        d = r["d_adj"] or 0.0
        shortfall = max(0.0, d * L2 - r["stock"])
        dl = r["days_left"]
        goes_critical = dl is not None and dl < L2
        cover = []
        if goes_critical:
            ctx = graph.item_context(iid) or {}
            for pid in ctx.get("peers", []):
                p = sm.get(pid)
                if (p and p["supplier_id"] != supplier_id and p["stock"] > 0
                        and (p["days_left"] is None or p["days_left"] >= L2)):
                    cover.append({"item_id": pid, "name": p["name"], "stock": p["stock"]})
        rows.append({
            "item_id": iid, "name": r["name"], "stock": r["stock"], "d_adj": d,
            "days_left": dl, "normal_lead_days": r["lead_time_days"], "delayed_lead_days": L2,
            "runs_out_before_delivery": goes_critical, "shortfall_units": round(shortfall, 1),
            "lost_revenue_paise": int(round(shortfall * r["unit_price_paise"])),
            "lost_margin_paise": int(round(shortfall * r["unit_margin_paise"])),
            "cover_with": cover[:3],
        })
    rows.sort(key=lambda x: (not x["runs_out_before_delivery"], -x["lost_revenue_paise"]))
    hit = [r for r in rows if r["runs_out_before_delivery"]]
    return insight(
        kind="supplier_risk", title=f"If {sname} is {delay_days} day(s) late",
        as_of=ds.as_of.isoformat(), engine=graph.engine,
        headline={"supplier": sname, "supplier_id": supplier_id, "delay_days": delay_days,
                  "items_supplied": len(item_ids), "items_running_out": len(hit),
                  "lost_revenue_paise": sum(r["lost_revenue_paise"] for r in hit),
                  "lost_margin_paise": sum(r["lost_margin_paise"] for r in hit)},
        rows=rows,
        formula=("graph: supplier -supplies-> items; delayed lead L' = L + delay; runs out if "
                 "days_left < L'; shortfall = max(0, d × L' − stock); lost revenue = shortfall × "
                 "avg taxable price; cover_with = in-stock items of the same category from "
                 "other suppliers (graph: item -belongs_to-> category <-belongs_to- item)"),
        confidence="ok" if item_ids else "no_data",
    )


def supplier_dependency(graph, ds: Dataset, period_days: int = 30) -> Insight:
    w = ds.sales_window(period_days)
    rev = w.groupby("item_id")["line_total_paise"].sum()
    total = int(rev.sum())
    rows = []
    for s in graph.suppliers_with_items():
        v = int(rev.reindex(s["item_ids"]).fillna(0).sum())
        rows.append({"supplier_id": s["supplier_id"], "supplier": s["name"],
                     "items": len(s["item_ids"]), "revenue_paise": v,
                     "share_pct": pct(v, total)})
    rows.sort(key=lambda r: -r["revenue_paise"])
    shares = [(r["share_pct"] or 0) / 100 for r in rows]
    hhi = round(sum(x * x for x in shares) * 10000)
    return insight(
        kind="supplier_dependency", title="How much of your sales depends on each supplier",
        as_of=ds.as_of.isoformat(), engine=graph.engine,
        headline={"top_supplier": rows[0]["supplier"] if rows else None,
                  "top_share_pct": rows[0]["share_pct"] if rows else None,
                  "hhi": hhi,
                  "concentration": "high" if hhi > 2500 else "moderate" if hhi > 1500 else "low"},
        rows=rows,
        formula=("graph: supplier -supplies-> items; share = revenue of the supplier's items ÷ "
                 "total revenue; HHI = Σ share² × 10,000 (>2,500 = highly concentrated)"),
        confidence="ok" if rows else "no_data",
    )


# ------------------------------------------------------------------ items
def bought_together(graph, ds: Dataset, item_id: str, n: int = 5, min_together: int = 3) -> Insight:
    res = graph.co_purchase(item_id, max(n * 4, 20))
    base, total = res["base_bills"], res["total_bills"]
    rows = []
    for p in res["pairs"]:
        if p["together"] < min_together or not base or not p["other_bills"]:
            continue
        lift = p["together"] * total / (base * p["other_bills"])
        chance = base * p["other_bills"] / total
        z = (p["together"] - chance) / math.sqrt(chance) if chance > 0 else 0.0
        rows.append({"item_id": p["item_id"], "name": _name(ds, p["item_id"]),
                     "bills_together": p["together"], "expected_by_chance": round(chance, 1),
                     "confidence_pct": round(100 * p["together"] / base, 1),
                     "lift": round(lift, 2), "z_score": round(z, 1),
                     "associated": bool(lift >= 1.2 and z >= 3)})
    rows.sort(key=lambda r: (not r["associated"], -r["lift"] if r["associated"] else 0,
                             -r["bills_together"]))
    rows = rows[:n]
    return insight(
        kind="bought_together", title=f"Bought together with {_name(ds, item_id)}",
        as_of=ds.as_of.isoformat(), engine=graph.engine,
        headline={"item_id": item_id, "name": _name(ds, item_id), "bills_with_item": base,
                  "total_bills": total,
                  "strongest": rows[0]["name"] if rows and rows[0]["associated"] else None},
        rows=rows,
        formula=("graph: bill -contains-> item; together = bills containing both; "
                 "expected = bills with item × bills with other ÷ all bills; lift = together ÷ "
                 "expected; z = (together − expected) ÷ √expected; 'associated' if lift ≥ 1.2 "
                 "and z ≥ 3 (real pattern, not chance)"),
        confidence="ok" if rows else "no_data",
    )


def substitutes(stock_df: pd.DataFrame, graph, ds: Dataset, item_id: str, n: int = 5,
                band: float = 0.25) -> Insight:
    ctx = graph.item_context(item_id)
    if not ctx:
        return insight(kind="substitutes", title="Substitutes", confidence="no_data",
                       engine=graph.engine, as_of=ds.as_of.isoformat())
    sm = _stock_map(stock_df)
    mrp = ctx["item"]["mrp_paise"]
    rows = []
    for pid in ctx["peers"]:
        p = sm.get(pid)
        pm = int(ds.item_index["mrp_paise"].get(pid, 0))
        if not p or not mrp or abs(pm - mrp) > band * mrp:
            continue
        available = p["stock"] > 0 and p["tier"] in ("OK", "UNKNOWN")
        rows.append({"item_id": pid, "name": p["name"], "mrp_paise": pm,
                     "price_diff_pct": round(100 * (pm - mrp) / mrp, 1), "stock": p["stock"],
                     "tier": p["tier"], "days_left": p["days_left"],
                     "same_supplier": p["supplier_id"] == ctx["item"]["supplier_id"],
                     "available": available})
    rows.sort(key=lambda r: (not r["available"], abs(r["price_diff_pct"])))
    return insight(
        kind="substitutes", title=f"Substitutes for {ctx['item']['name']}",
        as_of=ds.as_of.isoformat(), engine=graph.engine,
        headline={"item_id": item_id, "name": ctx["item"]["name"], "category": ctx["category"],
                  "available": sum(r["available"] for r in rows)},
        rows=rows[:n],
        formula=(f"graph: item -belongs_to-> category <-belongs_to- other items; keep those "
                 f"within ±{int(band * 100)}% of MRP; available = in stock and not at risk"),
        confidence="ok" if rows else "no_data",
    )


def supply_path(stock_df: pd.DataFrame, graph, ds: Dataset, item_id: str) -> Insight:
    ctx = graph.item_context(item_id)
    if not ctx:
        return insight(kind="supply_path", title="Supply path", confidence="no_data",
                       engine=graph.engine, as_of=ds.as_of.isoformat())
    engine = graph.engine
    st = _stock_map(stock_df).get(item_id, {})
    bt = bought_together(graph, ds, item_id, n=3)
    together = [{"item_id": r["item_id"], "name": r["name"], "together": r["bills_together"]}
                for r in bt.rows if r["associated"]]
    diagram = mermaid.item_flow(ctx, st, together)
    return insight(
        kind="supply_path", title=f"Supply path - {ctx['item']['name']}",
        as_of=ds.as_of.isoformat(), engine=engine,
        headline={"item_id": item_id, "name": ctx["item"]["name"],
                  "supplier": (ctx.get("supplier") or {}).get("name"),
                  "category": ctx["category"], "stock": st.get("stock"),
                  "days_left": st.get("days_left"), "tier": st.get("tier")},
        rows=together, meta={"mermaid": diagram, "context": ctx},
        formula="graph: supplier -supplies-> item -belongs_to-> category; bill -contains-> item",
    )


def supplier_flow(stock_df: pd.DataFrame, graph, ds: Dataset, supplier_id: str | None = None,
                  only_at_risk: bool = True) -> Insight:
    chains = graph.supply_chain(supplier_id)
    sm = _stock_map(stock_df)
    if only_at_risk and not supplier_id:
        chains = [c for c in chains if sm.get(c["item_id"], {}).get("tier") in ("CRITICAL", "SOON")]
    diagram = mermaid.supplier_flow(chains, sm)
    return insight(
        kind="supplier_flow", title="Supply flow" + (" - items at risk" if only_at_risk and
                                                      not supplier_id else ""),
        as_of=ds.as_of.isoformat(), engine=graph.engine,
        headline={"links": len(chains)}, rows=chains, meta={"mermaid": diagram},
        formula="graph: supplier -supplies-> item, coloured by stock tier",
        confidence="ok" if chains else "no_data",
    )
