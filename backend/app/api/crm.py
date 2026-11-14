"""Metrics layer and customer (CRM) endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent.crm_tools import resolve_customer
from app.agent.tools import ToolError, resolve_category, resolve_item, resolve_supplier
from app.analytics import customers as cust_mod
from app.analytics import metrics as metrics_mod
from app.analytics.common import Insight, public, records
from app.api.deps import dataset, manager
from app.store.manager import DataManager

router = APIRouter(prefix="/api", tags=["crm"], dependencies=[Depends(dataset)])


@router.get("/metrics")
def list_metrics():
    return {
        "metrics": [{"name": k, "label": v.label, "unit": v.unit, "formula": v.formula,
                     "customer_based": v.customer} for k, v in metrics_mod.METRICS.items()],
        "dimensions": list(metrics_mod.DIMENSIONS), "periods": list(metrics_mod.PERIODS),
        "compare": list(metrics_mod.COMPARES),
    }


@router.get("/metrics/query", response_model=Insight)
def query(metric: str = "revenue", group_by: str | None = None, period: str = "last_30_days",
          compare: Literal["previous_period", "same_period_last_month", "none"] = "previous_period",
          category: str | None = None, item: str | None = None, supplier: str | None = None,
          payment_mode: str | None = None, customer_segment: str | None = None,
          limit: int = Query(25, ge=1, le=100), m: DataManager = Depends(manager)):
    filters = {}
    try:
        if category:
            filters["category"] = resolve_category(m, category)
        if item:
            filters["item_id"] = resolve_item(m, item)[0]
        if supplier:
            filters["supplier_id"] = resolve_supplier(m, supplier)[0]
    except ToolError as exc:
        raise HTTPException(404, str(exc)) from exc
    if payment_mode:
        filters["payment_mode"] = payment_mode
    if customer_segment:
        filters["customer_segment"] = customer_segment
    try:
        return metrics_mod.query_metric(m.require(), metric, group_by, period, compare, filters,
                                        m.segments(), limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/customers")
def list_customers(segment: str | None = None, q: str | None = None, at_risk: bool | None = None,
                   limit: int = Query(100, ge=1, le=1000), m: DataManager = Depends(manager)):
    st = m.customer_stats()
    if segment:
        st = st[st["segment"] == segment]
    if at_risk is not None:
        st = st[st["at_risk"] == at_risk]
    if q:
        ql = q.lower()
        st = st[st["name"].str.lower().str.contains(ql, regex=False) | st["phone"].str.contains(q, regex=False)
                | st["customer_id"].str.lower().eq(ql)]
    keep = ["customer_id", "name", "phone", "area", "segment", "bills", "recency_days",
            "usual_gap_days", "revenue_90d_paise", "monthly_value_paise", "at_risk", "khata",
            "udhaar_outstanding_paise", "udhaar_overdue", "R", "F", "M"]
    return {"total": int(len(st)), "customers": public(records(st[keep].head(limit)))}


@router.get("/customers/segments", response_model=Insight)
def segments(m: DataManager = Depends(manager)):
    return cust_mod.segments_insight(m.require(), m.customer_stats())


@router.get("/customers/at-risk", response_model=Insight)
def at_risk(limit: int = Query(50, ge=1, le=500), m: DataManager = Depends(manager)):
    return cust_mod.at_risk_insight(m.require(), m.customer_stats(), limit)


@router.get("/customers/udhaar", response_model=Insight)
def udhaar(m: DataManager = Depends(manager)):
    return cust_mod.udhaar_insight(m.require())


@router.get("/customers/cohorts", response_model=Insight)
def cohorts(m: DataManager = Depends(manager)):
    return cust_mod.cohorts_insight(m.require())


@router.get("/customers/funnel", response_model=Insight)
def funnel(m: DataManager = Depends(manager)):
    return cust_mod.lifecycle_insight(m.require(), m.customer_stats())


@router.get("/customers/{ref}", response_model=Insight)
def customer(ref: str, m: DataManager = Depends(manager)):
    try:
        cid, _ = resolve_customer(m, ref)
    except ToolError as exc:
        raise HTTPException(404, {"message": str(exc), "suggestions": exc.suggestions}) from exc
    return cust_mod.profile_insight(m.require(), m.customer_stats(), cid)
