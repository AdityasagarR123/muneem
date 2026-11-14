"""PostgreSQL store, exercised on in-memory SQLite (same SQLAlchemy code path).
A live Postgres check runs only with RUN_PG_TESTS=1 and DATABASE_URL set in .env."""

import os

import pytest

from app.db.sql import SqlStore, normalize_url


@pytest.fixture()
def store():
    return SqlStore("sqlite+pysqlite:///:memory:")


def test_url_normalisation():
    assert normalize_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("postgresql://u:p@h/db?sslmode=require").startswith("postgresql+psycopg://")
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"


def test_dataset_roundtrip(store, ds):
    store.save_dataset(ds)
    back = store.load_dataset()
    assert back.version == ds.version and back.as_of == ds.as_of
    for name in ("items", "sales", "customers", "repayments", "inventory", "expenses", "suppliers"):
        assert len(getattr(back, name)) == len(getattr(ds, name)), name
    assert int(back.sales_x["line_total_paise"].sum()) == int(ds.sales_x["line_total_paise"].sum())
    assert int(back.repayments["amount_paise"].sum()) == int(ds.repayments["amount_paise"].sum())
    assert back.customers["khata"].sum() == ds.customers["khata"].sum()
    assert (back.sales["customer_id"] != "").sum() == (ds.sales["customer_id"] != "").sum()


def test_save_replaces_previous_dataset(store, ds):
    store.save_dataset(ds)
    store.save_dataset(ds)
    assert len(store.load_dataset().sales) == len(ds.sales)


def test_purchase_orders_and_activity(store):
    po = {"po_id": "PO-1", "status": "draft", "created_at": "2026-11-14T00:00:00", "lines": []}
    store.upsert_purchase_order(po)
    store.upsert_purchase_order({**po, "status": "sent"})
    assert [p["status"] for p in store.purchase_orders()] == ["sent"]
    store.record("chat_history", {"q": "hi"})


@pytest.mark.skipif(os.environ.get("RUN_PG_TESTS") != "1", reason="set RUN_PG_TESTS=1 for live Postgres")
def test_live_postgres(ds):
    from dotenv import dotenv_values

    from app.config import ROOT_DIR

    url = dotenv_values(ROOT_DIR / ".env").get("DATABASE_URL")
    assert url, "DATABASE_URL missing in .env"
    s = SqlStore(url)
    s.save_dataset(ds)
    assert len(s.load_dataset().sales) == len(ds.sales)
