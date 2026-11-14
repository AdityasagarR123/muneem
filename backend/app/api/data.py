"""Health, dataset, sample and upload endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import manager
from app.ingest.readers import ReadError, match_sheet, read_upload
from app.ingest.validate import Issue, ValidationReport, validate_tables
from app.store.manager import DataManager

router = APIRouter(prefix="/api", tags=["data"])
TABLE_FIELDS = ("items", "sales", "inventory", "expenses", "suppliers")


@router.get("/health")
def health(m: DataManager = Depends(manager)):
    st = m.status()
    return {"api": "ok", "arango": st["arango"]["status"], "graph_engine": st["graph_engine"],
            "dataset_loaded": st["dataset"] is not None}


@router.get("/status")
def status(m: DataManager = Depends(manager)):
    return m.status()


@router.post("/sample")
def load_sample(regenerate: bool = False, m: DataManager = Depends(manager)):
    report = m.load_sample(regenerate=regenerate)
    return {"report": report, "dataset": m.ds.summary() if report.ok and m.ds else None,
            "note": "Sample data is synthetic."}


@router.post("/arango/reconnect")
def arango_reconnect(m: DataManager = Depends(manager)):
    if not m.settings.arango_enabled:
        raise HTTPException(400, "ARANGO_URL is not set in .env")
    ok = m.connect_arango()
    if ok and m.ds is not None:
        m.sync_now()
    return m.status()["arango"]


async def _read_limited(f: UploadFile, limit_mb: int) -> bytes:
    limit = limit_mb * 1024 * 1024
    data = await f.read(limit + 1)
    if len(data) > limit:
        raise ReadError(f"{f.filename}: file is larger than {limit_mb} MB")
    return data


@router.post("/upload")
async def upload(
    items: UploadFile | None = File(None),
    sales: UploadFile | None = File(None),
    inventory: UploadFile | None = File(None),
    expenses: UploadFile | None = File(None),
    suppliers: UploadFile | None = File(None),
    workbook: UploadFile | None = File(None, description="One .xlsx with a sheet per table"),
    allow_partial: bool = Form(False),
    m: DataManager = Depends(manager),
):
    s = m.settings
    raw, notes = {}, []
    try:
        files = {"items": items, "sales": sales, "inventory": inventory, "expenses": expenses,
                 "suppliers": suppliers}
        for name, f in files.items():
            if f is None or not f.filename:
                continue
            parsed = read_upload(f.filename, await _read_limited(f, s.max_upload_mb))
            if isinstance(parsed, dict):  # xlsx given for a single table: use first sheet
                parsed = next(iter(parsed.values()))
            raw[name] = parsed
        if workbook is not None and workbook.filename:
            parsed = read_upload(workbook.filename, await _read_limited(workbook, s.max_upload_mb))
            sheets = parsed if isinstance(parsed, dict) else {"sales": parsed}
            for sheet, df in sheets.items():
                t = match_sheet(sheet)
                if t and t not in raw:
                    raw[t] = df
                    notes.append(f"sheet '{sheet}' read as {t}")
                else:
                    notes.append(f"sheet '{sheet}' ignored")
    except ReadError as exc:
        report = ValidationReport(errors=[Issue(table="upload", message=str(exc))], error_count=1)
        return {"report": report, "dataset": None}
    if not raw:
        raise HTTPException(400, "No files received. Send items + sales (and ideally inventory).")
    report, ds = validate_tables(raw, allow_partial=allow_partial, max_rows=s.max_rows,
                                 max_text=s.max_text_len, source="upload")
    report.notes = notes + report.notes
    if ds is not None:
        m.replace(ds)
    m.record("uploads", {"ok": report.ok, "rows_in": report.rows_in,
                         "rows_loaded": report.rows_loaded, "error_count": report.error_count,
                         "warning_count": report.warning_count})
    return {"report": report, "dataset": ds.summary() if ds is not None else None}


@router.get("/dataset")
def dataset_summary(m: DataManager = Depends(manager)):
    if m.ds is None:
        raise HTTPException(409, "No data loaded")
    return {**m.ds.summary(), "festivals": m.calendar.as_dict()}
