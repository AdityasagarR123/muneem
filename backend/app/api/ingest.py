"""Smart import: analyze -> owner confirms uncertain mappings -> commit. Plus read-only SQL import
and a signed webhook for live records."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.data import _read_limited
from app.api.deps import manager
from app.ingest import smart
from app.ingest.connectors import records_frame, sql_read, sql_tables, verify_signature
from app.ingest.readers import ReadError, read_upload
from app.ingest.schema import TABLES
from app.ingest.validate import validate_tables
from app.store.manager import DataManager

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
Entity = Literal[tuple(TABLES)]  # type: ignore[valid-type]


class TableChoice(BaseModel):
    key: str
    entity: Entity
    mapping: dict[str, str | None] = Field(default_factory=dict, description="field -> source column")
    include: bool = True


class CommitRequest(BaseModel):
    tables: list[TableChoice]
    allow_partial: bool = False
    remember: bool = True
    source_name: str = Field("My billing export", max_length=120)


class SqlRequest(BaseModel):
    url: str = Field(..., min_length=10, max_length=600, description="postgresql://... or mysql://...")
    tables: list[str] = Field(default_factory=list)


def _analysis(m: DataManager, frames: dict, source: str, llm=None, use_ai: bool = False) -> dict:
    sessions, profiles = m.ingest
    remembered = profiles.by_signature()
    a = smart.analyze(frames, remembered)
    if use_ai and llm is not None:
        for t in a["tables"]:
            unsure = [x["field"] for x in t["mapping"] if x["status"] in ("check", "missing")]
            for field, col in smart.ai_suggest(llm, t["entity"], unsure, t["columns"]).items():
                for x in t["mapping"]:
                    if x["field"] == field and x["source"] != col:
                        x.update(source=col, confidence=max(x["confidence"], 0.6), status="check",
                                 reason="suggested by AI - please confirm")
    a["ingest_id"] = sessions.add(frames, source)
    a["source"] = source
    return a


@router.post("/analyze")
async def analyze(request: Request, files: list[UploadFile] = File(...), use_ai: bool = Form(False),
                  m: DataManager = Depends(manager)):
    s = m.settings
    frames = {}
    try:
        for f in files[:10]:
            parsed = read_upload(f.filename or "file.csv", await _read_limited(f, s.max_upload_mb))
            stem = (f.filename or "file").rsplit(".", 1)[0]
            if isinstance(parsed, dict):
                for sheet, df in parsed.items():
                    frames[f"{stem}:{sheet}" if len(parsed) > 1 else stem] = df
            else:
                frames[stem] = parsed
    except ReadError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not frames:
        raise HTTPException(400, "No readable files")
    for k, df in frames.items():
        if len(df) > s.max_rows:
            raise HTTPException(413, f"{k}: too many rows ({len(df)}); limit is {s.max_rows}")
    llm = getattr(request.app.state, "agent", None)
    return _analysis(m, frames, "upload", llm.llm if llm else None, use_ai)


@router.post("/{ingest_id}/commit")
def commit(ingest_id: str, req: CommitRequest, m: DataManager = Depends(manager)):
    sessions, profiles = m.ingest
    sess = sessions.get(ingest_id)
    if sess is None:
        raise HTTPException(404, "This import expired - analyze the files again")
    frames = sess["frames"]
    chosen = [t.model_dump() for t in req.tables if t.include]
    for t in chosen:
        if t["key"] not in frames:
            raise HTTPException(422, f"Unknown table '{t['key']}'")
    try:
        raw = smart.build_raw(frames, chosen)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    report, ds = validate_tables(raw, allow_partial=req.allow_partial, max_rows=m.settings.max_rows,
                                 max_text=m.settings.max_text_len, source=sess["source"])
    saved = []
    if ds is not None:
        m.replace(ds)
        sessions.drop(ingest_id)
        if req.remember:
            for t in chosen:
                sig = smart.signature(list(frames[t["key"]].columns))
                saved.append(profiles.save(sig, f"{req.source_name} - {t['key']}", t["entity"], t["mapping"])["name"])
    m.record("uploads", {"ok": report.ok, "via": f"smart-{sess['source']}", "rows_in": report.rows_in,
                         "rows_loaded": report.rows_loaded, "error_count": report.error_count})
    return {"report": report, "dataset": ds.summary() if ds is not None else None, "remembered": saved}


@router.get("/profiles")
def saved_profiles(m: DataManager = Depends(manager)):
    return sorted(m.ingest[1].by_signature().values(), key=lambda p: p.get("saved_at", ""), reverse=True)


# ------------------------------------------------------------------ SQL
@router.post("/sql/tables")
def list_sql_tables(req: SqlRequest, m: DataManager = Depends(manager)):
    try:
        return sql_tables(req.url, m.settings.allow_sqlite_connector)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not connect: {type(exc).__name__}") from exc


@router.post("/sql/analyze")
def analyze_sql(req: SqlRequest, request: Request, m: DataManager = Depends(manager)):
    if not req.tables:
        raise HTTPException(422, "Choose at least one table")
    try:
        frames = sql_read(req.url, req.tables[:10], m.settings.max_rows, m.settings.allow_sqlite_connector)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not read: {type(exc).__name__}") from exc
    return _analysis(m, frames, "sql")


# ------------------------------------------------------------------ webhook
@router.post("/webhook/{entity}")
async def webhook(entity: Entity, request: Request, x_muneem_signature: str | None = Header(None),
                  m: DataManager = Depends(manager)):
    """POST {"records": [...]} signed with HMAC-SHA256(WEBHOOK_SECRET, raw body) in the
    X-Muneem-Signature header. Bills/expenses/repayments are appended; items, stock, suppliers
    and customers are updated by id. The whole dataset is re-validated before it replaces the
    current one."""
    secret = m.settings.webhook_secret
    if not secret:
        raise HTTPException(503, "Webhook is off - set WEBHOOK_SECRET in .env")
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(413, "Body larger than 1 MB")
    if not verify_signature(secret, body, x_muneem_signature):
        raise HTTPException(401, "Bad or missing signature")
    try:
        payload = json.loads(body)
        records = payload["records"] if isinstance(payload, dict) else payload
        assert isinstance(records, list) and records and all(isinstance(r, dict) for r in records)
    except Exception as exc:
        raise HTTPException(422, 'Send {"records": [ {...}, ... ]}') from exc
    if len(records) > 5000:
        raise HTTPException(413, "At most 5000 records per call")
    new = records_frame(records)
    from app.ingest.profile import profile_table

    matches = smart.map_fields(entity, new, profile_table(new))
    missing = [x.field for x in matches if x.source is None and any(
        c.name == x.field and c.required for c in TABLES[entity])]
    if missing:
        raise HTTPException(422, f"Records lack required fields: {', '.join(missing)}")
    mapping = {x.field: x.source for x in matches if x.source}
    canonical = smart.build_raw({"new": new}, [{"key": "new", "entity": entity, "mapping": mapping}])[entity]
    ds_now = m.require()
    merged = smart.merge_records(smart.dataset_to_raw(ds_now), entity, canonical)
    report, ds = validate_tables(merged, allow_partial=False, max_rows=m.settings.max_rows,
                                 max_text=m.settings.max_text_len, source=ds_now.source)
    if ds is None:
        raise HTTPException(422, {"message": "Records failed validation - nothing changed",
                                  "errors": [e.model_dump() for e in report.errors[:20]]})
    m.replace(ds)
    m.record("uploads", {"ok": True, "via": "webhook", "entity": entity, "records": len(records)})
    return {"ok": True, "entity": entity, "records": len(records), "dataset": ds.summary()}
