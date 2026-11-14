"""Analytics, graph and item endpoints. All return the Insight shape
(headline + rows + formula + confidence + engine)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import kpis as kpis_mod
from app.analytics import margin as margin_mod
from app.analytics import sales as sales_mod
from app.analytics import stock as stock_mod
from app.analytics.common import Insight, insight, records
from app.api.deps import dataset, manager, resolve_item, resolve_supplier
from app.core.lexicon import match_names
from app.core.money import to_paise
from app.graph import insights as g
from app.graph import mermaid
from app.store.manager import DataManager

router = APIRouter(prefix="/api", tags=["insights"], dependencies=[Depends(dataset)])
Period = Query(30, ge=1, le=365)


# ------------------------------------------------------------------ analytics
@router.get("/kpis", response_model=Insight)
def kpis(period_days: int = Period, m: DataManager = Depends(manager)):
    return kpis_mod.compute_kpis(m.require(), period_days)


@router.get("/stock", response_model=Insight)
def stock_status(item: str | None = None, tier: str | None = None,
                 m: DataManager = Depends(manager)):
    df = m.stock()
    if tier:
        df = df[df["tier"] == tier.upper()]
    ids = [resolve_item(m, item)] if item else None
    return stock_mod.stock_status_insight(df, m.require(), ids)


@router.get("/reorder", response_model=Insight)
def reorder(budget_rs: float | None = Query(None, ge=0), supplier: str | None = None,
            m: DataManager = Depends(manager)):
    sid = resolve_supplier(m, supplier) if supplier else None
    budget = to_paise(budget_rs) if budget_rs is not None else None
    return stock_mod.reorder_plan(m.stock(), m.require(), budget, sid)


@router.get("/dead-stock", response_model=Insight)
def dead_stock(days: int = Query(30, ge=7, le=365), m: DataManager = Depends(manager)):
    return stock_mod.dead_stock(m.require(), days)


@router.get("/slow-movers", response_model=Insight)
def slow_movers(m: DataManager = Depends(manager)):
    return stock_mod.slow_movers(m.require(), m.settings.slow_mover_ratio)


@router.get("/margin", response_model=Insight)
def margin(period_days: int = Period,
           group_by: Literal["item", "category", "supplier"] = "item",
           m: DataManager = Depends(manager)):
    return margin_mod.margin_report(m.require(), period_days, group_by)


@router.get("/payments", response_model=Insight)
def payments(period_days: int = Query(90, ge=7, le=365), m: DataManager = Depends(manager)):
    return sales_mod.payment_mix(m.require(), period_days)


@router.get("/trends", response_model=Insight)
def trends(item: str | None = None, category: str | None = None,
           grain: Literal["day", "week", "month"] = "day",
           period_days: int = Query(90, ge=7, le=365), m: DataManager = Depends(manager)):
    iid = resolve_item(m, item) if item else None
    return sales_mod.sales_trend(m.require(), m.calendar, iid, category, grain, period_days)


@router.get("/top-items", response_model=Insight)
def top_items(by: Literal["revenue", "margin", "units", "margin_pct"] = "revenue",
              n: int = Query(10, ge=1, le=50), period_days: int = Period,
              m: DataManager = Depends(manager)):
    return sales_mod.top_items(m.require(), by, n, period_days)


@router.get("/expenses", response_model=Insight)
def expenses(m: DataManager = Depends(manager)):
    return sales_mod.expense_report(m.require())


@router.get("/money-flow", response_model=Insight)
def money_flow(period_days: int = Period, m: DataManager = Depends(manager)):
    k = kpis_mod.compute_kpis(m.require(), period_days)
    return insight(kind="money_flow", title=f"Where the money went - last {period_days} days",
                   as_of=k.as_of, headline=k.headline,
                   meta={"mermaid": mermaid.money_flow(k.headline)},
                   formula=k.formula)


# ------------------------------------------------------------------ graph
@router.get("/graph/supplier-priority", response_model=Insight)
def supplier_priority(m: DataManager = Depends(manager)):
    return g.supplier_priority(m.stock(), m.graph(), m.require())


@router.get("/graph/supplier-risk/{supplier}", response_model=Insight)
def supplier_risk(supplier: str, delay_days: int = Query(2, ge=0, le=30),
                  m: DataManager = Depends(manager)):
    return g.supplier_risk(m.stock(), m.graph(), m.require(), resolve_supplier(m, supplier),
                           delay_days)


@router.get("/graph/supplier-dependency", response_model=Insight)
def supplier_dependency(period_days: int = Period, m: DataManager = Depends(manager)):
    return g.supplier_dependency(m.graph(), m.require(), period_days)


@router.get("/graph/bought-together/{item}", response_model=Insight)
def bought_together(item: str, n: int = Query(5, ge=1, le=20), m: DataManager = Depends(manager)):
    return g.bought_together(m.graph(), m.require(), resolve_item(m, item), n)


@router.get("/graph/substitutes/{item}", response_model=Insight)
def substitutes(item: str, n: int = Query(5, ge=1, le=20), m: DataManager = Depends(manager)):
    return g.substitutes(m.stock(), m.graph(), m.require(), resolve_item(m, item), n,
                         m.settings.substitute_price_band)


@router.get("/graph/supply-path/{item}", response_model=Insight)
def supply_path(item: str, m: DataManager = Depends(manager)):
    return g.supply_path(m.stock(), m.graph(), m.require(), resolve_item(m, item))


@router.get("/graph/supply-flow", response_model=Insight)
def supply_flow(supplier: str | None = None, at_risk_only: bool = True,
                m: DataManager = Depends(manager)):
    sid = resolve_supplier(m, supplier) if supplier else None
    return g.supplier_flow(m.stock(), m.graph(), m.require(), sid, at_risk_only)


# ------------------------------------------------------------------ items
@router.get("/resolve")
def resolve(q: str = Query(..., min_length=1, max_length=80), m: DataManager = Depends(manager)):
    """Map free text (English / Hindi / Hinglish) to items, categories and suppliers."""
    ds = m.require()
    cats = sorted(ds.items["category"].unique())
    return {
        "query": q,
        "items": match_names(q, list(zip(ds.items["item_id"], ds.items["name"])), limit=5),
        "categories": match_names(q, [(c, c) for c in cats], limit=3),
        "suppliers": match_names(q, list(zip(ds.suppliers["supplier_id"], ds.suppliers["name"])),
                                 limit=3),
    }


@router.get("/items")
def list_items(q: str | None = None, category: str | None = None,
               m: DataManager = Depends(manager)):
    ds = m.require()
    st = m.stock().set_index("item_id")
    items = ds.items
    if category:
        items = items[items["category"].str.lower() == category.lower()]
    if q:
        hits = match_names(q, list(zip(items["item_id"], items["name"])), limit=20)
        items = items.set_index("item_id").loc[[h["id"] for h in hits]].reset_index()
    out = []
    for r in records(items):
        s = st.loc[r["item_id"]] if r["item_id"] in st.index else None
        out.append({**r, "stock": None if s is None else float(s["stock"]),
                    "tier": None if s is None else s["tier"],
                    "days_left": None if s is None or s["days_left"] != s["days_left"]
                    else float(s["days_left"])})
    from app.analytics.common import public
    return public(out)


@router.get("/items/{item}")
def item_360(item: str, m: DataManager = Depends(manager)):
    """Everything about one item in one call."""
    ds = m.require()
    iid = resolve_item(m, item)
    st = m.stock()
    margins = margin_mod.item_margins(ds, 30)
    mrow = margins[margins["item_id"] == iid]
    graph = m.graph()
    return {
        "item_id": iid,
        "stock": stock_mod.stock_status_insight(st, ds, [iid]),
        "margin_30d": insight(kind="item_margin", title="Margin (30 days)",
                              rows=records(mrow), formula=margin_mod.MARGIN_FORMULA,
                              confidence="ok" if len(mrow) else "no_data"),
        "trend": sales_mod.sales_trend(ds, m.calendar, iid, None, "week", 90),
        "bought_together": g.bought_together(graph, ds, iid, 5),
        "substitutes": g.substitutes(st, graph, ds, iid, 5, m.settings.substitute_price_band),
        "supply_path": g.supply_path(st, graph, ds, iid),
    }


# ------------------------------------------------------------------ alerts
@router.get("/alerts")
def alerts(m: DataManager = Depends(manager)):
    """Everything that needs the owner's attention today, in one call."""
    ds = m.require()
    st = m.stock()
    at_risk = st[st["tier"].isin(["CRITICAL", "SOON"])]
    fest = st[(st["festive_baseline_adjusted"]) & (st["naive_order_qty"].fillna(0) > st["order_qty"])]
    avoided = [{"item_id": r["item_id"], "name": r["name"], "naive_order_qty": r["naive_order_qty"],
                "order_qty": r["order_qty"],
                "avoided_paise": int((r["naive_order_qty"] - r["order_qty"]) * r["unit_cost_paise"])}
               for r in records(fest)]
    margin = margin_mod.margin_report(ds, 30, "item")
    flagged = [r for r in margin.rows if r.get("flag")]
    unknown = st[st["tier"] == "UNKNOWN"]
    from app.analytics.common import public
    return {
        "as_of": ds.as_of.isoformat(),
        "stock": public({"critical": records(at_risk[at_risk["tier"] == "CRITICAL"]),
                         "soon": records(at_risk[at_risk["tier"] == "SOON"])}),
        "supplier_priority": g.supplier_priority(st, m.graph(), ds),
        "dead_stock": stock_mod.dead_stock(ds, m.settings.dead_stock_days),
        "margin_flags": flagged,
        "expenses": sales_mod.expense_report(ds),
        "festival_adjustments": public({
            "items": avoided,
            "avoided_overorder_paise": sum(a["avoided_paise"] for a in avoided),
            "note": "Recent festival demand was not treated as normal demand"}),
        "insufficient_data": public(records(unknown[["item_id", "name", "history_days", "note"]])),
    }
