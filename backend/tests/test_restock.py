import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.actions.brief import morning_brief
from app.actions.restock import whatsapp_url
from app.agent.core import Agent
from app.core.text import csv_safe
from app.main import create_app
from app.store.manager import DataManager


@pytest.fixture()
def m(settings):
    """Fresh manager per test - receiving stock changes the dataset."""
    mgr = DataManager(settings)
    mgr.startup()
    return mgr


def test_draft_one_order_per_supplier(m, expected):
    d = m.restock.create_draft()
    assert [p.supplier_id for p in d.orders] == [expected["top_risk_supplier"]]
    po = d.orders[0]
    assert {l.item_id for l in po.lines} >= set(expected["critical_items"])
    assert po.total_paise == sum(l.value_paise for l in po.lines)
    assert d.total_incl_gst_paise == po.total_incl_gst_paise
    assert po.message.startswith("Namaste Sharma Traders ji")
    assert "Chakki Atta 5kg - 50 pcs (10 packet × 5)" in po.message
    assert po.whatsapp_url.startswith("https://wa.me/?text=")  # placeholder phone not used
    assert po.status == "draft" and po.history[0]["status"] == "draft"


@pytest.mark.parametrize("lang,start", [("en", "Hello Sharma Traders"), ("hi", "नमस्ते Sharma Traders जी")])
def test_message_languages(m, lang, start):
    po = m.restock.create_draft(language=lang).orders[0]
    assert po.message.startswith(start)


def test_whatsapp_number_only_when_real():
    assert whatsapp_url("hi", "+91 98765 43210").startswith("https://wa.me/919876543210?text=hi")
    assert whatsapp_url("hi", "98765 43210").startswith("https://wa.me/919876543210?")
    assert whatsapp_url("hi", "+91-XXXXX-00001").startswith("https://wa.me/?text=")


def test_overrides_exclude_and_budget(m):
    d = m.restock.create_draft(overrides={"I001": 25}, exclude=["I009"], show_prices=False)
    lines = {l.item_id: l for l in d.orders[0].lines}
    assert lines["I001"].qty == 25 and lines["I001"].edited and "I009" not in lines
    assert "Anumaanit total" not in d.orders[0].message
    small = m.restock.create_draft(budget_rs=5000)
    assert small.total_incl_gst_paise <= 500_000 and small.deferred


def test_edit_only_drafts(m):
    po = m.restock.create_draft().orders[0]
    po = m.restock.edit(po.po_id, {"I005": 10}, language="en")
    line = next(l for l in po.lines if l.item_id == "I005")
    assert line.qty == 10 and line.value_paise == 10 * line.unit_cost_paise
    assert po.message.startswith("Hello") and "Toor Dal 1kg - 10 pcs" in po.message
    with pytest.raises(ValueError):
        m.restock.edit(po.po_id, {"I999": 1})
    m.restock.set_status(po.po_id, "sent")
    with pytest.raises(ValueError):
        m.restock.edit(po.po_id, {"I005": 5})


def test_lifecycle_receiving_updates_stock(m):
    before = m.stock().set_index("item_id")
    v0 = m.ds.version
    po = m.restock.create_draft().orders[0]
    with pytest.raises(ValueError):
        m.restock.set_status(po.po_id, "received")  # must be sent first
    m.restock.set_status(po.po_id, "sent")
    po = m.restock.set_status(po.po_id, "received", {"I005": 30})
    after = m.stock().set_index("item_id")
    atta = next(l for l in po.lines if l.item_id == "I001")
    assert after.at["I001", "stock"] == before.at["I001", "stock"] + atta.qty
    assert after.at["I005", "stock"] == before.at["I005", "stock"] + 30  # partial delivery
    assert after.at["I001", "tier"] != "CRITICAL"
    assert m.ds.version != v0
    assert [h["status"] for h in po.history] == ["draft", "sent", "received"]
    with pytest.raises(ValueError):
        m.restock.set_status(po.po_id, "received")  # cannot receive twice


def test_csv_export(m):
    d = m.restock.create_draft()
    rows = list(csv.DictReader(io.StringIO(m.restock.to_csv(d.orders))))
    assert len(rows) == len(d.orders[0].lines)
    assert rows[0]["supplier"] == "Sharma Traders" and float(rows[0]["value_incl_gst"]) > 0
    assert csv_safe("=HYPERLINK(1)") == "'=HYPERLINK(1)"


def test_morning_brief(m):
    b = morning_brief(m, "hinglish")
    kinds = [l["kind"] for l in b["lines"]]
    assert kinds[:2] == ["order", "sales"] and "dead_stock" in kinds and "expense" in kinds
    assert "Sharma Traders" in b["lines"][0]["text"]
    assert morning_brief(m, "hi")["text"].startswith("मुनीम")
    assert b["whatsapp_url"].startswith("https://wa.me/?text=")


def test_chat_drafts_message_in_rules_mode(m):
    env = Agent(m, None).ask("Sharma Traders ko order ka message bana do")
    assert env.evidence[0].tool == "restock_draft"
    assert env.evidence[0].resolved["supplier"]["id"] == "S01"
    assert "Namaste Sharma Traders ji" in env.answer_md
    assert any(a.type == "open_whatsapp" for a in env.actions)
    assert env.grounding.ungrounded == []
    brief = Agent(m, None).ask("Aaj ki subah ki report dikhao")
    assert brief.evidence[0].tool == "morning_brief"


def test_restock_api(settings):
    with TestClient(create_app()) as c:
        d = c.post("/api/restock/draft", json={"language": "en", "supplier": "sharma"}).json()
        po_id = d["orders"][0]["po_id"]
        r = c.patch(f"/api/restock/orders/{po_id}", json={"quantities": {"I001": 20}}).json()
        assert next(l for l in r["lines"] if l["item_id"] == "I001")["qty"] == 20
        assert c.post(f"/api/restock/orders/{po_id}/status",
                      json={"status": "received"}).status_code == 409
        assert c.post(f"/api/restock/orders/{po_id}/status", json={"status": "sent"}).json()[
            "status"] == "sent"
        assert c.get("/api/restock/orders", params={"status": "sent"}).json()[0]["po_id"] == po_id
        csv_r = c.get("/api/restock/export.csv", params={"po_id": po_id})
        assert csv_r.headers["content-type"].startswith("text/csv")
        assert "Chakki Atta 5kg" in csv_r.text
        assert c.get("/api/restock/export.csv").status_code == 200
        assert c.get("/api/restock/orders/PO-nope").status_code == 404
        assert c.get("/api/brief", params={"lang": "en"}).json()["text"].startswith("Muneem")
