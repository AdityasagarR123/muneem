"""Restock / purchase-order endpoints and the morning brief."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.actions.brief import morning_brief
from app.actions.restock import PurchaseOrder, RestockDraft
from app.api.deps import dataset, manager, resolve_supplier
from app.store.manager import DataManager

router = APIRouter(prefix="/api", tags=["restock"], dependencies=[Depends(dataset)])
Lang = Literal["en", "hinglish", "hi"]


class DraftRequest(BaseModel):
    budget_rs: float | None = Field(None, ge=0)
    supplier: str | None = Field(None, description="Supplier id or name; empty = all suppliers")
    language: Lang = "hinglish"
    show_prices: bool = True
    overrides: dict[str, int] = Field(default_factory=dict, description="item_id -> quantity")
    exclude: list[str] = Field(default_factory=list, description="item_ids to leave out")


class EditRequest(BaseModel):
    quantities: dict[str, int] = Field(default_factory=dict)
    language: Lang | None = None
    show_prices: bool | None = None


class StatusRequest(BaseModel):
    status: Literal["sent", "received", "cancelled"]
    received_qty: dict[str, int] | None = Field(
        None, description="Only for 'received': actual quantities if different from ordered")


def _get(m: DataManager, po_id: str) -> PurchaseOrder:
    try:
        return m.restock.get(po_id)
    except KeyError as exc:
        raise HTTPException(404, f"No purchase order {po_id}") from exc


@router.post("/restock/draft", response_model=RestockDraft)
def create_draft(req: DraftRequest, m: DataManager = Depends(manager)):
    sid = resolve_supplier(m, req.supplier) if req.supplier else None
    return m.restock.create_draft(req.budget_rs, sid, req.language, req.show_prices,
                                  req.overrides, req.exclude)


@router.get("/restock/orders", response_model=list[PurchaseOrder])
def list_orders(status: Literal["draft", "sent", "received", "cancelled"] | None = None,
                draft_id: str | None = None, m: DataManager = Depends(manager)):
    return m.restock.list(status, draft_id)


@router.get("/restock/orders/{po_id}", response_model=PurchaseOrder)
def get_order(po_id: str, m: DataManager = Depends(manager)):
    return _get(m, po_id)


@router.patch("/restock/orders/{po_id}", response_model=PurchaseOrder)
def edit_order(po_id: str, req: EditRequest, m: DataManager = Depends(manager)):
    _get(m, po_id)
    try:
        return m.restock.edit(po_id, req.quantities, req.language, req.show_prices)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/restock/orders/{po_id}/status", response_model=PurchaseOrder)
def set_status(po_id: str, req: StatusRequest, m: DataManager = Depends(manager)):
    _get(m, po_id)
    try:
        return m.restock.set_status(po_id, req.status, req.received_qty)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/restock/export.csv")
def export_csv(po_id: str | None = None, draft_id: str | None = None,
               budget_rs: float | None = Query(None, ge=0), m: DataManager = Depends(manager)):
    if po_id:
        orders = [_get(m, po_id)]
    elif draft_id:
        orders = m.restock.list(draft_id=draft_id)
        if not orders:
            raise HTTPException(404, f"No draft {draft_id}")
    else:  # fresh plan, not saved as a draft
        orders = m.restock.create_draft(budget_rs, save=False).orders
    name = po_id or draft_id or f"order-list-{m.require().as_of:%Y%m%d}"
    return Response(m.restock.to_csv(orders), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.get("/brief")
def brief(lang: Lang = "hinglish", m: DataManager = Depends(manager)):
    return morning_brief(m, lang)
