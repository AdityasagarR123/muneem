"""Campaign / reminder endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.actions.campaigns import GOALS, Campaign, summary
from app.agent.tools import ToolError, resolve_item
from app.api.deps import dataset, manager
from app.store.manager import DataManager

router = APIRouter(prefix="/api", tags=["campaigns"], dependencies=[Depends(dataset)])
Lang = Literal["en", "hinglish", "hi"]


class CampaignRequest(BaseModel):
    goal: Literal[tuple(GOALS)]  # type: ignore[valid-type]
    segment: dict | None = Field(None, description="Override the goal's default segment, e.g. "
                                 '{"type": "segment", "name": "Loyal"}')
    language: Lang = "hinglish"
    offer: str = Field("", max_length=200)
    item_a: str | None = Field(None, description="cross_sell: item they buy (name or id)")
    item_b: str | None = Field(None, description="cross_sell: item to suggest (name or id)")
    name: str | None = Field(None, max_length=80)
    holdout_pct: int | None = Field(None, ge=0, le=50)
    measure_after_days: int = Field(14, ge=3, le=60)


class EditRequest(BaseModel):
    language: Lang | None = None
    offer: str | None = Field(None, max_length=200)
    template: str | None = Field(None, max_length=600)
    exclude: list[str] = Field(default_factory=list)


class StatusRequest(BaseModel):
    status: Literal["sent", "cancelled"]


def _kwargs(m: DataManager, req: CampaignRequest) -> dict:
    params = {}
    try:
        if req.item_a:
            params["item_a"] = resolve_item(m, req.item_a)[0]
        if req.item_b:
            params["item_b"] = resolve_item(m, req.item_b)[0]
    except ToolError as exc:
        raise HTTPException(404, str(exc)) from exc
    return dict(goal=req.goal, segment=req.segment, language=req.language, offer=req.offer,
                params=params, name=req.name, holdout_pct=req.holdout_pct,
                measure_after_days=req.measure_after_days)


def _get(m: DataManager, cid: str) -> Campaign:
    try:
        return m.campaigns.get(cid)
    except KeyError as exc:
        raise HTTPException(404, f"No campaign {cid}") from exc


@router.get("/campaigns/goals")
def goals(m: DataManager = Depends(manager)):
    return m.campaigns.goal_defaults()


@router.post("/campaigns/preview")
def preview(req: CampaignRequest, m: DataManager = Depends(manager)):
    try:
        c = m.campaigns.build(**_kwargs(m, req))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**summary(c), "examples": [mm.message for mm in c.targets[:3]]}


@router.post("/campaigns", response_model=Campaign)
def create(req: CampaignRequest, m: DataManager = Depends(manager)):
    try:
        return m.campaigns.create(**_kwargs(m, req))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/campaigns")
def list_campaigns(status: Literal["draft", "sent", "cancelled"] | None = None,
                   m: DataManager = Depends(manager)):
    return [summary(c) for c in m.campaigns.list(status)]


@router.get("/campaigns/{cid}", response_model=Campaign)
def get_campaign(cid: str, m: DataManager = Depends(manager)):
    return _get(m, cid)


@router.patch("/campaigns/{cid}", response_model=Campaign)
def edit(cid: str, req: EditRequest, m: DataManager = Depends(manager)):
    _get(m, cid)
    try:
        return m.campaigns.edit(cid, req.language, req.offer, req.template, req.exclude)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/campaigns/{cid}/status", response_model=Campaign)
def set_status(cid: str, req: StatusRequest, m: DataManager = Depends(manager)):
    _get(m, cid)
    try:
        return m.campaigns.set_status(cid, req.status)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/campaigns/{cid}/result")
def result(cid: str, m: DataManager = Depends(manager)):
    _get(m, cid)
    return m.campaigns.result(cid)


@router.get("/campaigns/{cid}/export.csv")
def export(cid: str, m: DataManager = Depends(manager)):
    c = _get(m, cid)
    return Response(m.campaigns.to_csv(c), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{cid}.csv"'})
