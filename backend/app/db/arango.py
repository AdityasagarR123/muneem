"""ArangoDB connection, schema bootstrap, and dataset save/load."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

import pandas as pd

from app.config import Settings
from app.core.text import safe_key, slug
from app.db import aql
from app.db.schema import DATA_COLLECTIONS, DOC_COLLECTIONS, EDGE_COLLECTIONS, GRAPH_NAME, INDEXES
from app.store.dataset import Dataset

log = logging.getLogger("muneem.arango")
BATCH = 5000


class ArangoUnavailable(RuntimeError):
    pass


def connect(settings: Settings):
    """Return a python-arango StandardDatabase with the schema in place."""
    try:
        from arango import ArangoClient
    except ImportError as exc:  # pragma: no cover
        raise ArangoUnavailable("python-arango not installed") from exc
    try:
        client = ArangoClient(hosts=settings.arango_url.strip(),
                              verify_override=settings.arango_verify_tls,
                              request_timeout=settings.arango_timeout_s)
        if settings.arango_create_db:
            sys_db = client.db("_system", username=settings.arango_user,
                               password=settings.arango_password)
            if not sys_db.has_database(settings.arango_db):
                sys_db.create_database(settings.arango_db)
        db = client.db(settings.arango_db, username=settings.arango_user,
                       password=settings.arango_password, verify=True)
        ensure_schema(db)
        return db
    except ArangoUnavailable:
        raise
    except Exception as exc:
        raise ArangoUnavailable(f"{type(exc).__name__}: {exc}") from exc


def ensure_schema(db) -> None:
    for name in DOC_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name)
    for name in EDGE_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name, edge=True)
    if not db.has_graph(GRAPH_NAME):
        db.create_graph(GRAPH_NAME, edge_definitions=[
            {"edge_collection": e, "from_vertex_collections": f, "to_vertex_collections": t}
            for e, (f, t) in EDGE_COLLECTIONS.items()
        ])
    for coll, idx in INDEXES.items():
        c = db.collection(coll)
        existing = {(tuple(i.get("fields", [])), bool(i.get("unique"))) for i in c.indexes()}
        for fields, unique in idx:
            if (tuple(fields), unique) in existing:
                continue
            spec = {"type": "persistent", "fields": fields, "unique": unique}
            if hasattr(c, "add_index"):
                c.add_index(spec)
            else:  # older python-arango
                c.add_persistent_index(fields=fields, unique=unique)


# ------------------------------------------------------------------ save
class _Keys:
    """Stable, unique, Arango-safe _key per original id."""

    def __init__(self):
        self.map: dict[str, str] = {}
        self.used: set[str] = set()

    def __call__(self, original: str, maker=safe_key) -> str:
        if original in self.map:
            return self.map[original]
        base = maker(original)
        k, n = base, 1
        while k in self.used:
            n += 1
            k = f"{base}-{n}"
        self.map[original] = k
        self.used.add(k)
        return k


def _records(df: pd.DataFrame) -> list[dict]:
    """numpy-free JSON-able records (dates must already be strings)."""
    return json.loads(df.to_json(orient="records", force_ascii=False))


def _iso(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y-%m-%d")


def _import(db, coll: str, docs: list[dict]) -> None:
    c = db.collection(coll)
    for i in range(0, len(docs), BATCH):
        res = c.import_bulk(docs[i:i + BATCH], on_duplicate="replace", halt_on_error=True)
        if res.get("errors"):
            raise ArangoUnavailable(f"import into {coll} failed: {res}")


def save_dataset(db, ds: Dataset) -> dict:
    meta = db.collection("meta")
    meta.insert({"_key": "dataset", "status": "loading",
                 "started_at": datetime.now(timezone.utc).isoformat()}, overwrite=True)
    for name in DATA_COLLECTIONS:
        db.collection(name).truncate()

    sk, ik, ck, bk = _Keys(), _Keys(), _Keys(), _Keys()

    sup = ds.suppliers.copy()
    sup["_key"] = sup["supplier_id"].map(sk)
    _import(db, "suppliers", _records(sup))

    cats = sorted(ds.items["category"].unique())
    _import(db, "categories", [{"_key": ck(c, slug), "name": c} for c in cats])

    items = ds.items.copy()
    items["_key"] = items["item_id"].map(ik)
    _import(db, "items", _records(items))

    _import(db, "supplies", [
        {"_from": f"suppliers/{sk(r.supplier_id)}", "_to": f"items/{ik(r.item_id)}",
         "lead_time_days": int(r.lead_time_days)}
        for r in items.itertuples() if r.supplier_id in sk.map
    ])
    _import(db, "belongs_to", [
        {"_from": f"items/{ik(r.item_id)}", "_to": f"categories/{ck(r.category, slug)}"}
        for r in items.itertuples()
    ])

    sx = ds.sales_x
    inv = (sx.groupby("invoice_id")
             .agg(date=("date", "min"), payment_mode=("payment_mode", "first"),
                  total_paise=("line_total_paise", "sum"), lines=("item_id", "size"))
             .reset_index())
    inv["date"] = _iso(inv["date"])
    inv["_key"] = inv["invoice_id"].map(bk)
    _import(db, "invoices", _records(inv))

    pairs = (sx.groupby(["invoice_id", "item_id"])
               .agg(qty=("qty", "sum"), amount_paise=("line_total_paise", "sum"))
               .reset_index())
    _import(db, "contains", [
        {"_from": f"invoices/{bk(r.invoice_id)}", "_to": f"items/{ik(r.item_id)}",
         "qty": float(r.qty), "amount_paise": int(r.amount_paise)}
        for r in pairs.itertuples()
    ])

    sales = ds.sales.copy()
    sales["date"] = _iso(sales["date"])
    _import(db, "sales", _records(sales))

    invn = ds.inventory.copy()
    invn["as_of_date"] = _iso(invn["as_of_date"]) if len(invn) else invn["as_of_date"]
    invn["_key"] = invn["item_id"].map(ik)
    _import(db, "inventory", _records(invn))

    if len(ds.expenses):
        exp = ds.expenses.copy()
        exp["date"] = _iso(exp["date"])
        _import(db, "expenses", _records(exp))

    ck2 = _Keys()
    if len(ds.customers):
        cust = ds.customers.copy()
        cust["_key"] = cust["customer_id"].map(ck2)
        _import(db, "customers", _records(cust))
        linked = sx[sx["customer_id"] != ""]
        bought = (linked.groupby(["customer_id", "item_id"])
                        .agg(bills=("invoice_id", "nunique"), qty=("qty", "sum"),
                             amount_paise=("line_total_paise", "sum"))
                        .reset_index())
        _import(db, "purchased", [
            {"_from": f"customers/{ck2(r.customer_id)}", "_to": f"items/{ik(r.item_id)}",
             "bills": int(r.bills), "qty": float(r.qty), "amount_paise": int(r.amount_paise)}
            for r in bought.itertuples() if r.customer_id in ck2.map
        ])
    if len(ds.repayments):
        rp = ds.repayments.copy()
        rp["date"] = _iso(rp["date"])
        _import(db, "repayments", _records(rp))

    info = {
        "_key": "dataset", "status": "ready", "version": ds.version, "source": ds.source,
        "as_of": ds.as_of.isoformat(), "loaded_at": ds.loaded_at.isoformat(),
        "counts": ds.summary()["counts"],
    }
    meta.insert(info, overwrite=True)
    return info


# ------------------------------------------------------------------ load
def _fetch(db, coll: str) -> list[dict]:
    return list(db.aql.execute(aql.ALL_DOCS, bind_vars={"@coll": coll}, batch_size=10000))


def load_dataset(db) -> Dataset | None:
    meta = db.collection("meta").get("dataset")
    if not meta or meta.get("status") != "ready":
        return None
    frames = {c: pd.DataFrame(_fetch(db, c)) for c in
              ("items", "sales", "inventory", "expenses", "suppliers", "customers", "repayments")}
    if frames["items"].empty or frames["sales"].empty:
        return None
    if frames["inventory"].empty:
        frames["inventory"] = pd.DataFrame(columns=["item_id", "stock_qty", "as_of_date"])
    if frames["expenses"].empty:
        frames["expenses"] = pd.DataFrame(columns=["date", "category", "amount_paise",
                                                   "payment_mode"])
    if "hsn" not in frames["items"]:
        frames["items"]["hsn"] = ""
    frames["items"]["hsn"] = frames["items"]["hsn"].fillna("")
    return Dataset(
        items=frames["items"], sales=frames["sales"], inventory=frames["inventory"],
        expenses=frames["expenses"], suppliers=frames["suppliers"],
        customers=frames["customers"] if len(frames["customers"]) else None,
        repayments=frames["repayments"] if len(frames["repayments"]) else None,
        as_of=date.fromisoformat(meta["as_of"]), source="arangodb",
        version=meta.get("version", "arango"),
    )


def record(db, collection: str, doc: dict) -> None:
    """Append a log document (uploads, chat history). Best-effort."""
    try:
        db.collection(collection).insert(
            {**doc, "created_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:  # pragma: no cover - logging only
        log.warning("could not record into %s: %s", collection, exc)
