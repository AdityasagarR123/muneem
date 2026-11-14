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


@router.get("/home")
def home(m: DataManager = Depends(manager)):
    """Everything the home screen needs in one call: briefing + health strip."""
    from app.analytics import kpis as kpis_mod
    from app.analytics import metrics as metrics_mod
    from app.analytics.common import public
    from app.analytics.customers import ledger

    ds = m.require()
    k = kpis_mod.compute_kpis(ds, 30).headline
    rep = metrics_mod.query_metric(ds, "repeat_rate_pct", None, "last_30_days").headline
    weekly = metrics_mod.query_metric(ds, "revenue", "week", "last_90_days", "none").rows
    st = m.customer_stats()
    risk = st[st["at_risk"]] if len(st) else st
    led = ledger(ds)
    findings = [f for f in m.findings() if f.status != "dismissed"]
    return {
        "shop_name": m.settings.shop_name, "as_of": ds.as_of.isoformat(),
        "briefing": [f.model_dump() for f in findings[:5]],
        "findings_total": len(findings),
        "health": public([
            {"key": "sales", "label": "Sales · 30 days", "value_paise": k["revenue_paise"],
             "change_pct": k.get("revenue_change_pct"),
             "spark": [r["value_rs"] for r in weekly][1:-1] or [r["value_rs"] for r in weekly]},
            {"key": "margin", "label": "Margin after GST", "value_paise": k["gross_margin_paise"],
             "change_pct": k.get("gross_margin_change_pct"), "sub": f"{k['gross_margin_pct']}%"},
            {"key": "repeat", "label": "Returning customers", "value": rep.get("value"),
             "unit": "%", "change_pct": rep.get("change_pct")},
            {"key": "at_risk", "label": "Customers at risk", "value": int(len(risk)),
             "sub_paise": int(risk["monthly_value_paise"].sum()) if len(risk) else 0,
             "sub_label": "monthly spend"},
            {"key": "udhaar", "label": "Udhaar outstanding",
             "value_paise": int(led["outstanding_paise"].clip(lower=0).sum()) if len(led) else 0,
             "sub": f"{int(led['overdue'].sum()) if len(led) else 0} overdue"},
        ]),
        "suggestions": ["What should I focus on today?", "Why did sales drop this week?",
                        "Kaun se customers ne aana band kar diya?", "Kal kya order karna chahiye?"],
    }


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
