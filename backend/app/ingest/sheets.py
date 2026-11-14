"""Live Google Sheets: link a sheet shared as "Anyone with the link can view", map its columns once,
and Muneem re-reads it every few minutes.

* Only docs.google.com is ever fetched (the link is turned into Google's CSV export URL).
* Each linked sheet feeds one table (products, bills, stock ...). Several sheets may feed the same
  table; their rows are combined. The sheet is the source of truth for that table.
* A sync only replaces the data when the sheet's content changed AND the whole dataset still passes
  validation - a broken edit in the sheet never wipes the shop's data.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pandas as pd

from app.ingest import smart
from app.ingest.readers import ReadError, read_csv_bytes
from app.ingest.schema import TABLES
from app.ingest.validate import validate_tables

log = logging.getLogger("muneem.sheets")
META_KEY = "google_sheets"
_ID = re.compile(r"/spreadsheets/d/(e/)?([A-Za-z0-9_-]{20,})")


class SheetError(ValueError):
    pass


def export_url(url: str) -> str:
    """Share / edit / published link -> CSV export link on docs.google.com."""
    p = urlparse((url or "").strip())
    if p.scheme != "https" or p.hostname != "docs.google.com":
        raise SheetError("Paste a Google Sheets link (https://docs.google.com/spreadsheets/...)")
    m = _ID.search(p.path)
    if not m:
        raise SheetError("That link has no spreadsheet id in it")
    gid = (parse_qs(p.query).get("gid") or parse_qs(p.fragment).get("gid") or ["0"])[0]
    if not gid.isdigit():
        raise SheetError("The tab id (gid) in the link is not valid")
    if m.group(1):  # "Publish to web" link: /spreadsheets/d/e/<pubid>/pub
        return f"https://docs.google.com/spreadsheets/d/e/{m.group(2)}/pub?output=csv&gid={gid}"
    return f"https://docs.google.com/spreadsheets/d/{m.group(2)}/export?format=csv&gid={gid}"


def fetch(url: str, http: httpx.Client, max_bytes: int) -> tuple[pd.DataFrame, str]:
    """Download one sheet tab as CSV -> (frame, content hash)."""
    target = export_url(url)
    try:
        with http.stream("GET", target, follow_redirects=True, timeout=20) as r:
            final = urlparse(str(r.url)).hostname or ""
            if not (final == "docs.google.com" or final.endswith(".googleusercontent.com")):
                raise SheetError("Google redirected to a sign-in page - share the sheet as "
                                 "'Anyone with the link can view'")
            if r.status_code in (401, 403, 404):
                raise SheetError("Google refused the sheet - check the link and that it is shared as "
                                 "'Anyone with the link can view'")
            if r.status_code != 200:
                raise SheetError(f"Google returned {r.status_code}")
            if "text/html" in r.headers.get("content-type", ""):
                raise SheetError("Got a web page instead of data - share the sheet as "
                                 "'Anyone with the link can view'")
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise SheetError(f"Sheet is larger than {max_bytes // 1_000_000} MB")
    except httpx.HTTPError as exc:
        raise SheetError(f"Could not reach Google: {type(exc).__name__}") from exc
    try:
        df = read_csv_bytes(bytes(body))
    except ReadError as exc:
        raise SheetError(str(exc)) from exc
    if df.empty:
        raise SheetError("The sheet has no rows")
    return df, hashlib.sha256(bytes(body)).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SheetSync:
    def __init__(self, manager, http: httpx.Client | None = None):
        self.m = manager
        self.http = http or httpx.Client()
        self._lock = threading.RLock()
        self._sheets: dict[str, dict] | None = None
        self._frames: dict[str, pd.DataFrame] = {}  # last good canonical rows per sheet
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ storage
    def _load(self) -> dict[str, dict]:
        if self._sheets is None:
            self._sheets = {}
            if self.m.sql is not None:
                try:
                    self._sheets = {s["sheet_id"]: s for s in (self.m.sql.get_meta(META_KEY) or [])}
                except Exception as exc:  # pragma: no cover
                    log.warning("could not load linked sheets: %s", exc)
        return self._sheets

    def _save(self) -> None:
        if self.m.sql is not None:
            try:
                self.m.sql.set_meta(META_KEY, list(self._load().values()))
            except Exception as exc:  # pragma: no cover
                log.warning("could not save linked sheets: %s", exc)

    def list(self) -> list[dict]:
        with self._lock:
            return sorted(self._load().values(), key=lambda s: s["linked_at"])

    # ------------------------------------------------------------------ link / unlink
    def peek(self, url: str) -> pd.DataFrame:
        return fetch(url, self.http, self.m.settings.max_upload_mb * 1_000_000)[0]

    def link(self, url: str, name: str, entity: str, mapping: dict[str, str | None]) -> dict:
        if entity not in TABLES:
            raise SheetError(f"Unknown table type '{entity}'")
        export_url(url)
        with self._lock:
            sid = "GS-" + uuid4().hex[:8]
            self._load()[sid] = {
                "sheet_id": sid, "name": name[:120] or "Google Sheet", "url": url.strip(), "entity": entity,
                "mapping": {k: v for k, v in mapping.items() if v}, "linked_at": _now(),
                "last_checked": None, "last_changed": None, "hash": None, "rows": 0,
                "status": "linked", "error": None,
            }
            self._save()
        res = self.sync(sid, force=True)
        if res["status"] == "error":  # never keep a sheet that cannot load the first time
            self.unlink(sid)
            raise SheetError(res["error"])
        return res

    def unlink(self, sid: str) -> bool:
        with self._lock:
            gone = self._load().pop(sid, None) is not None
            self._frames.pop(sid, None)
            self._save()
            return gone

    # ------------------------------------------------------------------ sync
    def _canonical(self, s: dict, df: pd.DataFrame) -> pd.DataFrame:
        missing = [c for c in s["mapping"].values() if c not in df.columns]
        if missing:
            raise SheetError(f"Column(s) {', '.join(missing)} were removed or renamed in the sheet")
        return smart.build_raw({"s": df}, [{"key": "s", "entity": s["entity"], "mapping": s["mapping"]}])[s["entity"]]

    def sync(self, sid: str | None = None, force: bool = False) -> dict | list[dict]:
        """Re-read one sheet (or all). Rebuilds the dataset once if anything changed."""
        with self._lock:
            sheets = self._load()
            targets = [sheets[sid]] if sid else list(sheets.values())
            if sid and sid not in sheets:
                raise SheetError("No such linked sheet")
            changed = []
            for s in targets:
                s["last_checked"] = _now()
                try:
                    df, h = fetch(s["url"], self.http, self.m.settings.max_upload_mb * 1_000_000)
                    if h == s.get("hash") and not force and s["sheet_id"] in self._frames:
                        s.update(status="ok", error=None)
                        continue
                    self._frames[s["sheet_id"]] = self._canonical(s, df)
                    s.update(hash=h, rows=int(len(df)), status="pending", error=None)
                    changed.append(s)
                except (SheetError, ValueError) as exc:
                    s.update(status="error", error=str(exc))
            if changed:
                err = self._apply()
                for s in changed:
                    if err:
                        s.update(status="error", error=err, hash=None)
                        self._frames.pop(s["sheet_id"], None)
                    else:
                        s.update(status="ok", last_changed=_now())
            self._save()
            out = [dict(x) for x in targets]
            return out[0] if sid else out

    def _apply(self) -> str | None:
        """Current dataset with every sheet-owned table replaced by the sheets' rows."""
        ds = self.m.ds
        raw = smart.dataset_to_raw(ds) if ds is not None else {}
        owned: dict[str, list[pd.DataFrame]] = {}
        for s in self._load().values():
            if s["sheet_id"] in self._frames:
                owned.setdefault(s["entity"], []).append(self._frames[s["sheet_id"]])
        for entity, parts in owned.items():
            raw[entity] = pd.concat(parts, ignore_index=True)
        st = self.m.settings
        report, new = validate_tables(raw, allow_partial=False, max_rows=st.max_rows,
                                      max_text=st.max_text_len, source="google-sheets")
        if new is None:
            first = report.errors[0] if report.errors else None
            detail = f" (e.g. {first.table} row {first.row}: {first.message})" if first else ""
            return f"Sheet data failed checks - nothing changed{detail}"
        self.m.replace(new)
        self.m.record("uploads", {"ok": True, "via": "google-sheets", "rows_in": report.rows_in,
                                  "rows_loaded": report.rows_loaded, "error_count": report.error_count})
        return None

    # ------------------------------------------------------------------ background poller
    def start(self, every_minutes: float) -> None:
        if every_minutes <= 0 or self._thread is not None:
            return

        def loop():
            while not self._stop.wait(every_minutes * 60):
                if self._load():
                    try:
                        self.sync()
                    except Exception:  # pragma: no cover - never kill the poller
                        log.exception("sheet sync failed")

        self._thread = threading.Thread(target=loop, name="sheet-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
