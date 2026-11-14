"""Smart import against an export it has never seen: generic file names, billing-app headers,
dd-mm-yyyy dates, 'Paytm'/'Udhaar' payment words, phones instead of customer ids, junk columns."""

import hashlib
import hmac
import io
import json
import sqlite3

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.ingest import smart
from app.ingest.connectors import sql_read, sql_tables, verify_signature
from app.ingest.profile import profile_column
from app.main import create_app


@pytest.fixture(scope="module")
def export(ds):
    raw = smart.dataset_to_raw(ds)
    it, s, inv = raw["items"], raw["sales"], raw["inventory"]
    phone = ds.customers.set_index("customer_id")["phone"]
    pay = {"UPI": "Paytm", "cash": "Cash", "credit": "Udhaar", "card": "Card"}
    products = pd.DataFrame({
        "Code": it["item_id"], "Item Description": it["name"], "Group": it["category"], "HSN/SAC": it["hsn"],
        "MRP": it["mrp_incl_gst"], "Purchase Rate": it["cost_ex_gst"], "Tax %": it["gst_rate"] + "%",
        "Case Qty": it["pack_size"], "Vendor Code": it["supplier_id"], "Remarks": "ok"})
    bills = pd.DataFrame({
        "Bill No.": s["invoice_id"], "Bill Dt": pd.to_datetime(s["date"]).dt.strftime("%d-%m-%Y"),
        "Product Code": s["item_id"], "Qty Sold": s["qty"], "Rate (incl. GST)": s["unit_price_incl_gst"],
        "Pay Mode": s["payment_mode"].map(pay), "Cust Mobile": s["customer_id"].map(phone).fillna(""),
        "Disc %": "0"})
    stock = pd.DataFrame({"Product Code": inv["item_id"], "Closing Qty": inv["stock_qty"],
                          "As On": pd.to_datetime(inv["as_of_date"]).dt.strftime("%d/%m/%Y")})
    return {"export_1": products.astype(str), "export_2": bills.astype(str), "export_3": stock.astype(str)}


def csv_bytes(df):
    b = io.StringIO()
    df.to_csv(b, index=False)
    return b.getvalue().encode()


# ------------------------------------------------------------------ profiler
@pytest.mark.parametrize("values,kind", [
    (["9876543210", "+91 98765 43210", "09876543210"], "phone"),
    (["a@b.in", "x.y@shop.com", "z@q.co"], "email"),
    (["27AAPFU0939F1ZV", "29ABCDE1234F1Z5", "07AAACR5055K1Z3"], "gstin"),
    (["ramesh@okicici", "sunita@ybl", "amit@paytm"], "upi"),
    (["03-11-2026", "14/11/2026", "2026-11-01"], "date"),
    (["₹1,250.00", "Rs. 99.50", "1,000.00"], "money"),
    (["5%", "18%", "0%"], "percent"),
    (["5", "18", "0", "5", "12"], "percent"),
    (["yes", "no", "yes"], "boolean"),
])
def test_profiler_types(values, kind):
    assert profile_column("c", pd.Series(values * 4))["type"] == kind


# ------------------------------------------------------------------ the unseen export
def test_unseen_export_is_understood(export):
    a = smart.analyze(export, {})
    t = {x["key"]: x for x in a["tables"]}
    assert t["export_1"]["entity"] == "items"
    assert t["export_2"]["entity"] == "sales"
    assert t["export_3"]["entity"] == "inventory"
    want = {
        "export_1": {"item_id": "Code", "name": "Item Description", "category": "Group", "hsn": "HSN/SAC",
                     "mrp_incl_gst": "MRP", "cost_ex_gst": "Purchase Rate", "gst_rate": "Tax %",
                     "pack_size": "Case Qty", "supplier_id": "Vendor Code"},
        "export_2": {"invoice_id": "Bill No.", "date": "Bill Dt", "item_id": "Product Code",
                     "qty": "Qty Sold", "unit_price_incl_gst": "Rate (incl. GST)",
                     "payment_mode": "Pay Mode", "customer_phone": "Cust Mobile"},
        "export_3": {"item_id": "Product Code", "stock_qty": "Closing Qty", "as_of_date": "As On"},
    }
    for key, fields in want.items():
        got = {m["field"]: m["source"] for m in t[key]["mapping"]}
        for f, col in fields.items():
            assert got[f] == col, (key, f, got[f])
    asked = [(k, m["field"]) for k, x in t.items() for m in x["mapping"]
             if m["status"] in ("check", "missing") and (m["required"] or m["source"])]
    assert len(asked) <= 3, asked  # only genuinely uncertain fields need the owner
    for x in t.values():
        assert "Remarks" not in {m["source"] for m in x["mapping"]}
    links = {(l["from"], l["to"]): l for l in a["links"]}
    assert links[("sales.Product Code", "items.Code")]["status"] == "ok"


def test_commit_loads_same_business_and_remembers(export, ds, settings):
    with TestClient(create_app()) as c:
        files = [("files", (f"{k}.csv", csv_bytes(df), "text/csv")) for k, df in export.items()]
        a = c.post("/api/ingest/analyze", files=files).json()
        choices = [{"key": t["key"], "entity": t["entity"],
                    "mapping": {m["field"]: m["source"] for m in t["mapping"] if m["source"]}} for t in a["tables"]]
        r = c.post(f"/api/ingest/{a['ingest_id']}/commit",
                   json={"tables": choices, "source_name": "Test POS"}).json()
        assert r["report"]["ok"], r["report"]["error_summary"]
        assert len(r["remembered"]) == 3
        assert c.get("/api/kpis").json()["headline"]["revenue_paise"] == \
            int(ds.sales_window(30)["line_total_paise"].sum())
        st = c.get("/api/status").json()["dataset"]
        assert st["counts"]["sales_lines"] == len(ds.sales) and st["customer_linked_bills_pct"] > 50
        # same headers again -> mapped from memory, nothing to confirm
        again = c.post("/api/ingest/analyze", files=files).json()
        assert all(t["remembered"] and t["needs_confirmation"] == 0 for t in again["tables"])
        assert c.post(f"/api/ingest/{a['ingest_id']}/commit", json={"tables": choices}).status_code == 404
        assert c.post("/api/sample").json()["report"]["ok"]  # restore sample for other tests


def test_commit_rejects_bad_choices(export):
    with TestClient(create_app()) as c:
        a = c.post("/api/ingest/analyze", files=[("files", ("export_3.csv", csv_bytes(export["export_3"]), "text/csv"))]).json()
        bad = {"tables": [{"key": "export_3", "entity": "inventory", "mapping": {"stock_qty": "Nope"}}]}
        assert c.post(f"/api/ingest/{a['ingest_id']}/commit", json=bad).status_code == 422


def test_ai_suggestion_is_validated_and_needs_confirmation():
    class FakeLLM:
        def start(self, h, t):
            return []

        def generate(self, *a, **k):
            return type("R", (), {"text": '```json\n{"qty": "Pieces", "date": "Not a column"}\n```'})()

    prof = [{"column": "Pieces", "type": "integer", "examples": ["1"]}, {"column": "When", "type": "text", "examples": ["x"]}]
    assert smart.ai_suggest(FakeLLM(), "sales", ["qty", "date"], prof) == {"qty": "Pieces"}
    assert smart.ai_suggest(None, "sales", ["qty"], prof) == {}


# ------------------------------------------------------------------ connectors
def test_sql_connector_reads_tables(export, tmp_path):
    db = tmp_path / "pos.db"
    con = sqlite3.connect(db)
    export["export_1"].to_sql("Products", con, index=False)
    export["export_3"].to_sql("Stock", con, index=False)
    con.close()
    url = f"sqlite:///{db}"
    with pytest.raises(ValueError, match="SQLite"):
        sql_tables(url)  # server-local files are refused by default
    tabs = {t["table"]: t for t in sql_tables(url, allow_sqlite=True)}
    assert tabs["Products"]["rows"] == len(export["export_1"])
    frames = sql_read(url, ["Products", "Stock"], 10_000, allow_sqlite=True)
    a = smart.analyze(frames, {})
    assert {t["key"]: t["entity"] for t in a["tables"]} == {"Products": "items", "Stock": "inventory"}
    with pytest.raises(ValueError):
        sql_read(url, ["Products; DROP TABLE Stock"], 10, allow_sqlite=True)
    with pytest.raises(ValueError, match="PostgreSQL"):
        sql_tables("mssql://x/y")


def test_webhook_signed_append(settings):
    body = json.dumps({"records": [
        {"Bill No": "WH-1", "date": "2026-11-13", "item_id": "I001", "qty": "2", "rate": "265", "payment": "upi"},
        {"Bill No": "WH-1", "date": "2026-11-13", "item_id": "I015", "qty": "1", "rate": "165", "payment": "upi"}]}).encode()
    s = get_settings()
    old = s.webhook_secret
    try:
        with TestClient(create_app()) as c:
            assert c.post("/api/ingest/webhook/sales", content=body).status_code == 503  # off by default
            s.webhook_secret = "test-secret"
            sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
            assert c.post("/api/ingest/webhook/sales", content=body, headers={"X-Muneem-Signature": "sha256=bad"}).status_code == 401
            before = c.get("/api/status").json()["dataset"]["counts"]["sales_lines"]
            r = c.post("/api/ingest/webhook/sales", content=body, headers={"X-Muneem-Signature": sig}).json()
            assert r["ok"] and r["dataset"]["counts"]["sales_lines"] == before + 2
            bad = json.dumps({"records": [{"Bill No": "WH-2", "date": "2026-11-13", "item_id": "NOPE", "qty": "1"}]}).encode()
            sig2 = "sha256=" + hmac.new(b"test-secret", bad, hashlib.sha256).hexdigest()
            assert c.post("/api/ingest/webhook/sales", content=bad, headers={"X-Muneem-Signature": sig2}).status_code == 422
            assert c.get("/api/status").json()["dataset"]["counts"]["sales_lines"] == before + 2  # unchanged
            c.post("/api/sample")
    finally:
        s.webhook_secret = old
    assert verify_signature("k", b"x", hmac.new(b"k", b"x", hashlib.sha256).hexdigest())
    assert not verify_signature("", b"x", "anything")
