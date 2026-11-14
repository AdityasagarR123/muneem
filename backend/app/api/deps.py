from __future__ import annotations

from fastapi import HTTPException, Request

from app.store.manager import DataManager, NoDataset


def manager(request: Request) -> DataManager:
    return request.app.state.manager


def dataset(request: Request):
    try:
        return request.app.state.manager.require()
    except NoDataset as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def resolve_item(m: DataManager, ref: str) -> str:
    """Accept an item_id or a name/Hinglish word ('aata 5kg') -> item_id."""
    from app.core.lexicon import match_names

    ds = m.require()
    if ref in ds.item_index.index:
        return ref
    hits = match_names(ref, list(zip(ds.items["item_id"], ds.items["name"])), limit=3)
    if not hits:
        raise HTTPException(status_code=404, detail=f"No item matches '{ref}'")
    return hits[0]["id"]


def resolve_supplier(m: DataManager, ref: str) -> str:
    from app.core.lexicon import match_names

    ds = m.require()
    if ref in ds.supplier_index.index:
        return ref
    hits = match_names(ref, list(zip(ds.suppliers["supplier_id"], ds.suppliers["name"])), limit=1)
    if not hits:
        raise HTTPException(status_code=404, detail=f"No supplier matches '{ref}'")
    return hits[0]["id"]
