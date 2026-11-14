"""Live Google Sheets: analyze a shared link -> confirm the mapping -> linked and re-read on a timer."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import manager
from app.api.ingest import _analysis
from app.ingest.schema import TABLES
from app.ingest.sheets import SheetError
from app.store.manager import DataManager

router = APIRouter(prefix="/api/sheets", tags=["google-sheets"])
Entity = Literal[tuple(TABLES)]  # type: ignore[valid-type]


class SheetLink(BaseModel):
    url: str = Field(..., min_length=20, max_length=600)
    name: str = Field("Google Sheet", max_length=120)


class LinkRequest(SheetLink):
    entity: Entity
    mapping: dict[str, str | None] = Field(default_factory=dict)


@router.get("")
def list_sheets(m: DataManager = Depends(manager)):
    return {"sheets": m.sheets.list(), "poll_minutes": m.settings.sheets_poll_minutes}


@router.post("/analyze")
def analyze(req: SheetLink, m: DataManager = Depends(manager)):
    """Read the sheet once and propose which table it is and how its columns map."""
    try:
        df = m.sheets.peek(req.url)
    except SheetError as exc:
        raise HTTPException(422, str(exc)) from exc
    a = _analysis(m, {req.name or "sheet": df}, "google-sheets")
    a["sheet"] = {"url": req.url, "name": req.name}
    return a


@router.post("")
def link(req: LinkRequest, m: DataManager = Depends(manager)):
    try:
        s = m.sheets.link(req.url, req.name, req.entity, req.mapping)
    except SheetError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"sheet": s, "dataset": m.ds.summary() if m.ds is not None else None}


@router.post("/sync")
def sync_all(m: DataManager = Depends(manager)):
    return {"sheets": m.sheets.sync(), "dataset": m.ds.summary() if m.ds is not None else None}


@router.post("/{sheet_id}/sync")
def sync_one(sheet_id: str, m: DataManager = Depends(manager)):
    try:
        return {"sheet": m.sheets.sync(sheet_id, force=True)}
    except SheetError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/{sheet_id}")
def unlink(sheet_id: str, m: DataManager = Depends(manager)):
    if not m.sheets.unlink(sheet_id):
        raise HTTPException(404, "No such linked sheet")
    return {"ok": True, "note": "The data already loaded stays; it just stops updating from this sheet."}
