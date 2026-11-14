import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def client(settings):
    with TestClient(create_app()) as c:
        yield c


def test_health(client):
    r = client.get("/api/health").json()
    assert r["api"] == "ok" and r["dataset_loaded"] and r["arango"] == "disabled"


@pytest.mark.parametrize("path", [
    "/api/kpis", "/api/stock", "/api/reorder", "/api/dead-stock", "/api/slow-movers",
    "/api/margin?group_by=category", "/api/payments", "/api/trends?category=Festive%20%26%20Sweets",
    "/api/top-items?by=margin", "/api/expenses", "/api/money-flow",
    "/api/graph/supplier-priority", "/api/graph/supplier-risk/sharma?delay_days=3",
    "/api/graph/supplier-dependency", "/api/graph/bought-together/aata",
    "/api/graph/substitutes/I016", "/api/graph/supply-path/I001", "/api/graph/supply-flow",
])
def test_insight_endpoints(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["formula"] and "kind" in body


def test_alerts_and_item_360(client):
    a = client.get("/api/alerts").json()
    assert a["supplier_priority"]["headline"]["call_first"] == "Sharma Traders"
    assert a["dead_stock"]["headline"]["items"] == 3
    assert a["festival_adjustments"]["avoided_overorder_paise"] > 0
    it = client.get("/api/items/aata 5kg").json()
    assert it["item_id"] == "I001"
    assert it["bought_together"]["rows"][0]["item_id"] == "I015"


def test_resolve_hinglish(client):
    r = client.get("/api/resolve", params={"q": "cheeni"}).json()
    assert r["items"][0]["id"] == "I009"


def test_unknown_item_404(client):
    assert client.get("/api/graph/supply-path/xyzzy").status_code == 404


def test_upload_roundtrip(client, settings):
    files = {t: (f"{t}.csv", (settings.data_dir / f"{t}.csv").read_bytes(), "text/csv")
             for t in ("items", "sales", "inventory", "expenses", "suppliers")}
    r = client.post("/api/upload", files=files).json()
    assert r["report"]["ok"], r["report"]["error_summary"]
    assert r["dataset"]["source"] == "upload"
    assert client.get("/api/kpis").status_code == 200


def test_upload_bad_file_reports_errors(client):
    files = {"items": ("items.csv", b"item_id,name\nA1,Atta\n", "text/csv"),
             "sales": ("sales.csv", b"invoice_id,date,item_id,qty\nB1,2026-11-01,A1,1\n", "text/csv")}
    r = client.post("/api/upload", files=files).json()
    assert r["report"]["ok"] is False and r["dataset"] is None
    # previous dataset still served
    assert client.get("/api/kpis").status_code == 200


def test_upload_rejects_wrong_extension(client):
    r = client.post("/api/upload", files={"items": ("x.pdf", b"%PDF", "application/pdf")}).json()
    assert r["report"]["ok"] is False
