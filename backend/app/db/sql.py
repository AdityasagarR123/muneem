"""PostgreSQL system of record (SQLAlchemy Core).

    DATABASE_URL=postgresql://user:pass@host/db?sslmode=require   (Neon / Supabase / local)

The same code runs on SQLite, which the tests use, so the schema and the save/load path
are covered without a database server. All analytics still run in-process on the loaded
frames; the database is the durable, consistent copy of the canonical business model.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, Float, ForeignKey, Index, Integer, MetaData, String, Table,
    Text, create_engine, delete, insert, select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.store.dataset import Dataset

log = logging.getLogger("muneem.sql")
md = MetaData()

meta_t = Table("meta", md, Column("key", String(64), primary_key=True), Column("value", Text))
suppliers_t = Table(
    "suppliers", md, Column("supplier_id", String(64), primary_key=True), Column("name", String(200)),
    Column("whatsapp_placeholder", String(64)), Column("lead_time_days", Integer))
items_t = Table(
    "items", md, Column("item_id", String(64), primary_key=True), Column("name", String(200)),
    Column("category", String(100)), Column("hsn", String(20)), Column("mrp_paise", BigInteger),
    Column("cost_paise", BigInteger), Column("gst_rate", Integer), Column("pack_size", Integer),
    Column("supplier_id", String(64)), Column("lead_time_days", Integer))
customers_t = Table(
    "customers", md, Column("customer_id", String(64), primary_key=True), Column("name", String(200)),
    Column("phone", String(32)), Column("area", String(120)), Column("khata", Boolean))
sales_t = Table(
    "sales", md, Column("id", Integer, primary_key=True, autoincrement=True),
    Column("invoice_id", String(64), index=True), Column("date", Date, index=True),
    Column("item_id", String(64), ForeignKey("items.item_id"), index=True),
    Column("qty", Float), Column("unit_price_paise", BigInteger), Column("payment_mode", String(16)),
    Column("customer_id", String(64), index=True))
inventory_t = Table(
    "inventory", md, Column("item_id", String(64), ForeignKey("items.item_id"), primary_key=True),
    Column("stock_qty", Float), Column("as_of_date", Date))
expenses_t = Table(
    "expenses", md, Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", Date, index=True), Column("category", String(64)),
    Column("amount_paise", BigInteger), Column("payment_mode", String(16)))
repayments_t = Table(
    "repayments", md, Column("id", Integer, primary_key=True, autoincrement=True),
    Column("date", Date, index=True), Column("customer_id", String(64), index=True),
    Column("amount_paise", BigInteger), Column("mode", String(16)))
purchase_orders_t = Table(
    "purchase_orders", md, Column("po_id", String(40), primary_key=True),
    Column("status", String(16), index=True), Column("created_at", String(40), index=True),
    Column("data", Text))
activity_t = Table(
    "activity_log", md, Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(32), index=True), Column("created_at", String(40), index=True),
    Column("data", Text))
Index("ix_sales_customer_date", sales_t.c.customer_id, sales_t.c.date)

# replaced on every dataset load, in an order that respects foreign keys
DATA_TABLES = [sales_t, inventory_t, repayments_t, expenses_t, items_t, customers_t, suppliers_t]


def normalize_url(url: str) -> str:
    """Neon/Supabase hand out postgres:// URLs; SQLAlchemy needs the psycopg driver named."""
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class SqlStore:
    def __init__(self, url: str):
        url = normalize_url(url)
        if url.startswith("sqlite") and ":memory:" in url:
            self.engine: Engine = create_engine(url, poolclass=StaticPool,
                                                connect_args={"check_same_thread": False})
        else:
            self.engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)
        self.dialect = self.engine.dialect.name
        md.create_all(self.engine)

    # ---------------------------------------------------------------- dataset
    def save_dataset(self, ds: Dataset) -> dict:
        frames = {
            "suppliers": ds.suppliers, "items": ds.items, "customers": ds.customers,
            "sales": ds.sales, "inventory": ds.inventory, "expenses": ds.expenses,
            "repayments": ds.repayments,
        }
        with self.engine.begin() as conn:  # one transaction: all or nothing
            for t in DATA_TABLES:
                conn.execute(delete(t))
            for t in reversed(DATA_TABLES):
                df = frames[t.name]
                if len(df):
                    conn.execute(insert(t), _rows(df, t))
            info = {"status": "ready", "version": ds.version, "source": ds.source,
                    "as_of": ds.as_of.isoformat(), "loaded_at": ds.loaded_at.isoformat(),
                    "counts": ds.summary()["counts"]}
            conn.execute(delete(meta_t).where(meta_t.c.key == "dataset"))
            conn.execute(insert(meta_t), [{"key": "dataset", "value": json.dumps(info)}])
        return info

    def load_dataset(self) -> Dataset | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(meta_t.c.value).where(meta_t.c.key == "dataset")).first()
            if not row:
                return None
            info = json.loads(row[0])
            f = {t.name: pd.read_sql(select(t), conn) for t in DATA_TABLES}
        if f["items"].empty or f["sales"].empty:
            return None
        return Dataset(
            items=f["items"], sales=f["sales"].drop(columns="id"), inventory=f["inventory"],
            expenses=f["expenses"].drop(columns="id"), suppliers=f["suppliers"],
            customers=f["customers"], repayments=f["repayments"].drop(columns="id"),
            as_of=date.fromisoformat(info["as_of"]), source=info.get("source", "postgres"),
            version=info.get("version", "sql"))

    # ---------------------------------------------------------------- documents
    def upsert_purchase_order(self, po: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(purchase_orders_t).where(purchase_orders_t.c.po_id == po["po_id"]))
            conn.execute(insert(purchase_orders_t), [{"po_id": po["po_id"], "status": po["status"],
                                                      "created_at": po["created_at"],
                                                      "data": json.dumps(po, default=str)}])

    def purchase_orders(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(purchase_orders_t.c.data)
                                .order_by(purchase_orders_t.c.created_at)).all()
        return [json.loads(r[0]) for r in rows]

    def record(self, kind: str, doc: dict) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(activity_t), [{
                    "kind": kind, "created_at": datetime.now(timezone.utc).isoformat(),
                    "data": json.dumps(doc, default=str, ensure_ascii=False)}])
        except Exception as exc:  # pragma: no cover - logging only
            log.warning("could not record %s: %s", kind, exc)

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            conn.execute(select(1))
        return True


def _rows(df: pd.DataFrame, table: Table) -> list[dict]:
    cols = [c.name for c in table.columns if c.name in df.columns]
    out = df[cols].copy()
    for c in table.columns:
        if c.name not in out:
            continue
        if isinstance(c.type, Date):
            out[c.name] = pd.to_datetime(out[c.name]).dt.date
        elif isinstance(c.type, (BigInteger, Integer)):
            out[c.name] = out[c.name].astype("int64")
        elif isinstance(c.type, Boolean):
            out[c.name] = out[c.name].astype(bool)
    recs = out.to_dict("records")
    # numpy scalars -> python (psycopg is strict)
    return [{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()} for r in recs]
