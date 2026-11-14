"""Live ArangoDB tests - skipped unless RUN_ARANGO_TESTS=1.

They load the sample into the database configured in the root .env, then check
that every AQL graph query returns exactly what the in-memory engine returns.
Use a throwaway database: the data collections are replaced.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_ARANGO_TESTS") != "1",
                                reason="set RUN_ARANGO_TESTS=1 to run against ArangoDB")


@pytest.fixture(scope="module")
def db(settings, ds):
    from dotenv import dotenv_values

    from app.config import ROOT_DIR, Settings
    from app.db.arango import connect, save_dataset

    env = {k.lower(): v for k, v in dotenv_values(ROOT_DIR / ".env").items() if v is not None}
    s = Settings(**{k: v for k, v in env.items() if k.startswith("arango_")})
    database = connect(s)
    save_dataset(database, ds)
    return database


def test_roundtrip(db, ds):
    from app.db.arango import load_dataset

    back = load_dataset(db)
    assert len(back.items) == len(ds.items)
    assert len(back.sales) == len(ds.sales)
    assert int(back.sales_x["line_total_paise"].sum()) == int(ds.sales_x["line_total_paise"].sum())


def test_graph_parity(db, ds):
    from app.graph.backends import ArangoGraph, MemoryGraph

    a, m = ArangoGraph(db), MemoryGraph(ds)
    assert a.suppliers_with_items() == m.suppliers_with_items()
    assert a.supplier_items("S01") == m.supplier_items("S01")
    assert a.co_purchase("I001", 10) == m.co_purchase("I001", 10)
    assert a.supply_chain("S01") == m.supply_chain("S01")
    ca, cm = a.item_context("I001"), m.item_context("I001")
    assert ca["peers"] == cm["peers"] and ca["category"] == cm["category"]
    assert ca["supplier"]["supplier_id"] == cm["supplier"]["supplier_id"]
