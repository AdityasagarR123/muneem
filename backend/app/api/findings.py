"""Insight feed ("what happened / why it matters / what to do") and change explanations."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.analytics.common import Insight
from app.analytics.explain import explain_change
from app.analytics.findings import Finding
from app.api.deps import dataset, manager
from app.store.manager import DataManager

router = APIRouter(prefix="/api", tags=["insights-feed"], dependencies=[Depends(dataset)])


class StatusBody(BaseModel):
    status: Literal["new", "seen", "acted", "dismissed"]


@router.get("/findings", response_model=list[Finding])
def list_findings(status: str | None = Query(None, description="new|seen|acted|dismissed|all"),
                  category: Literal["risk", "opportunity", "info"] | None = None,
                  limit: int = Query(20, ge=1, le=100), m: DataManager = Depends(manager)):
    out = m.findings()
    if status is None:
        out = [f for f in out if f.status != "dismissed"]
    elif status != "all":
        out = [f for f in out if f.status == status]
    if category:
        out = [f for f in out if f.category == category]
    return out[:limit]


@router.post("/findings/{finding_id}/status", response_model=Finding)
def set_status(finding_id: str, body: StatusBody, m: DataManager = Depends(manager)):
    found = next((f for f in m.findings() if f.id == finding_id), None)
    if found is None:
        raise HTTPException(404, f"No current finding {finding_id}")
    m.set_insight_status(finding_id, body.status)
    return found.model_copy(update={"status": body.status})


@router.get("/explain", response_model=Insight)
def explain(period: str = "last_7_days",
            compare: Literal["previous_period", "same_period_last_month"] = "previous_period",
            m: DataManager = Depends(manager)):
    st = m.customer_stats()
    risk = set(st.loc[st["at_risk"], "customer_id"]) if len(st) else set()
    try:
        return explain_change(m.require(), m.calendar, period, compare, risk)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
