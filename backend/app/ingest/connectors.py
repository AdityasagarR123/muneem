"""Connectors: smart-import sessions, saved source mappings, read-only SQL, signed webhooks."""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import pandas as pd

from app.core.text import normalize_header

log = logging.getLogger("muneem.ingest")
SESSION_TTL_S = 1800
MAX_SESSIONS = 10


class IngestSessions:
    """Uploaded-but-not-yet-loaded tables, kept in memory for the review step."""

    def __init__(self):
        self._d: dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(self, frames: dict[str, pd.DataFrame], source: str) -> str:
        sid = uuid4().hex[:12]
        now = time.time()
        with self._lock:
            for k in [k for k, v in self._d.items() if now - v["at"] > SESSION_TTL_S]:
                self._d.pop(k)
            while len(self._d) >= MAX_SESSIONS:
                self._d.pop(min(self._d, key=lambda k: self._d[k]["at"]))
            self._d[sid] = {"frames": frames, "source": source, "at": now}
        return sid

    def get(self, sid: str) -> dict | None:
        with self._lock:
            s = self._d.get(sid)
            return s if s and time.time() - s["at"] <= SESSION_TTL_S else None

    def drop(self, sid: str) -> None:
        with self._lock:
            self._d.pop(sid, None)


class SourceProfiles:
    """Confirmed mappings, keyed by the file's header signature - the next export from the same
    billing app maps itself."""

    def __init__(self, manager):
        self.m = manager
        self._d: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._d is None:
            self._d = {}
            if self.m.sql is not None:
                try:
                    self._d = {p["signature"]: p for p in self.m.sql.source_profiles()}
                except Exception as exc:  # pragma: no cover
                    log.warning("could not load source profiles: %s", exc)
        return self._d

    def by_signature(self) -> dict[str, dict]:
        return dict(self._load())

    def save(self, signature: str, name: str, entity: str, mapping: dict[str, str]) -> dict:
        p = {"signature": signature, "name": name[:120], "entity": entity,
             "mapping": {k: v for k, v in mapping.items() if v},
             "saved_at": datetime.now(timezone.utc).isoformat()}
        self._load()[signature] = p
        if self.m.sql is not None:
            try:
                self.m.sql.save_source_profile(p)
            except Exception as exc:  # pragma: no cover
                log.warning("could not save source profile: %s", exc)
        return p


# ------------------------------------------------------------------ SQL (read-only)
ALLOWED_DIALECTS = ("postgresql", "postgres", "mysql")


def _engine(url: str, allow_sqlite: bool):
    from sqlalchemy import create_engine

    from app.db.sql import normalize_url

    url = normalize_url(url)
    dialect = url.split(":", 1)[0].split("+", 1)[0]
    if dialect == "sqlite" and not allow_sqlite:
        raise ValueError("SQLite files on this server can't be read - connect a PostgreSQL or MySQL database")
    if dialect not in ALLOWED_DIALECTS + (("sqlite",) if allow_sqlite else ()):
        raise ValueError("Only PostgreSQL and MySQL connections are supported")
    args = {"connect_args": {"options": "-c default_transaction_read_only=on"}} \
        if dialect.startswith("postgres") else {}
    return create_engine(url, pool_pre_ping=True, **args)


def sql_tables(url: str, allow_sqlite: bool = False) -> list[dict]:
    from sqlalchemy import func, inspect, select, table

    eng = _engine(url, allow_sqlite)
    try:
        insp = inspect(eng)
        out = []
        with eng.connect() as conn:
            for name in insp.get_table_names()[:100]:
                n = conn.execute(select(func.count()).select_from(table(name))).scalar()
                out.append({"table": name, "rows": int(n),
                            "columns": [c["name"] for c in insp.get_columns(name)]})
        return out
    finally:
        eng.dispose()


def sql_read(url: str, tables: list[str], max_rows: int, allow_sqlite: bool = False) -> dict[str, pd.DataFrame]:
    """SELECT * FROM each chosen table (reflected, so names are quoted safely; read-only session)."""
    from sqlalchemy import MetaData, Table, inspect, select

    eng = _engine(url, allow_sqlite)
    try:
        existing = set(inspect(eng).get_table_names())
        frames = {}
        with eng.connect() as conn:
            for name in tables:
                if name not in existing:
                    raise ValueError(f"No table '{name}' in that database")
                t = Table(name, MetaData(), autoload_with=eng)
                df = pd.read_sql(select(t).limit(max_rows + 1), conn)
                if len(df) > max_rows:
                    raise ValueError(f"Table '{name}' has more than {max_rows} rows")
                frames[name] = df.astype(str).replace({"None": "", "nan": "", "NaT": ""})
        return frames
    finally:
        eng.dispose()


# ------------------------------------------------------------------ webhook
def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    given = signature.split("=", 1)[1] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(expected, given.strip())


def records_frame(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records).astype(str).replace({"None": "", "nan": ""})
    df.columns = [normalize_header(c) for c in df.columns]
    return df
