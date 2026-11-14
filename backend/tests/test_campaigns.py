import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.actions.campaigns import Campaign, Member, _p_value, holdout_ids, measure, render
from app.agent.core import Agent
from app.agent.tools import ToolError, run_tool
from app.analytics import customers as C
from app.main import create_app
from app.store.manager import DataManager


@pytest.fixture()
def m(settings):
    mgr = DataManager(settings)
    mgr.startup()
    return mgr


# ------------------------------------------------------------------ building
def test_holdout_is_exact_stable_and_never_empty():
    ids = [f"C{i}" for i in range(200)]
    h = holdout_ids("CMP-1", ids, 10)
    assert len(h) == 20 and h == holdout_ids("CMP-1", list(reversed(ids)), 10)  # order-independent
    assert h != holdout_ids("CMP-2", ids, 10)  # each campaign gets its own split
    assert len(holdout_ids("X", ids[:6], 10)) == 1  # small group still measurable
    assert holdout_ids("X", ids, 0) == set()


def test_segments_match_analytics(m, expected):
    st = m.customer_stats()
    wb = m.campaigns.build(goal="win_back")
    assert {mm.customer_id for mm in wb.members} == set(st.loc[st["at_risk"], "customer_id"])
    rem = m.campaigns.build(goal="udhaar_reminder")
    led = C.ledger(m.require()).set_index("customer_id")
    assert rem.holdout_pct == 0 and all(mm.group == "target" for mm in rem.members)
    for mm in rem.members:
        assert mm.amount_paise == led.at[mm.customer_id, "outstanding_paise"]
        assert f"₹{mm.amount_paise // 100:,}"[:3] in mm.message  # amount is in the message
    ids = {mm.customer_id for mm in rem.members}
    assert {r["customer_id"] for r in expected["crm"]["overdue_customers"]} <= ids


def test_cross_sell_targets_buyers_of_a_who_never_bought_b(m):
    c = m.campaigns.build(goal="cross_sell", params={"item_a": "I001", "item_b": "I016"})
    s = m.require().sales
    never_b = set(s["customer_id"]) - set(s.loc[s["item_id"] == "I016", "customer_id"])
    assert c.members and {mm.customer_id for mm in c.members} <= never_b
    assert "Chakki Atta 5kg" in c.members[0].message and "Mustard Oil" in c.members[0].message
    with pytest.raises(ValueError):
        m.campaigns.build(goal="cross_sell")


def test_messages_in_three_languages_and_no_invented_offer(m):
    for lang, start in [("en", "Hello"), ("hinglish", "Namaste"), ("hi", "नमस्ते")]:
        c = m.campaigns.build(goal="welcome_new", language=lang)
        assert c.members[0].message.startswith(start)
        assert "%" not in c.members[0].message  # no discount unless the owner adds one
    c = m.campaigns.build(goal="reward_champions", offer="Free delivery this week.")
    assert c.members[0].message.endswith("Free delivery this week.")


def test_render_handles_missing_name():
    msg = render("Namaste {name} ji, {shop}.{offer}", "Shop", Member(customer_id="C1"), {}, "")
    assert msg == "Namaste ji, Shop."


# ------------------------------------------------------------------ lifecycle
def test_lifecycle_and_edits(m):
    c = m.campaigns.create(goal="win_back")
    c = m.campaigns.edit(c.campaign_id, language="en", offer="Your usual items are in stock.",
                         exclude=[c.members[0].customer_id])
    assert c.members[0].message.startswith("Hello") and c.members[0].message.endswith("in stock.")
    with pytest.raises(ValueError):
        m.campaigns.edit(c.campaign_id, template="Hi {phone}")  # unknown placeholder
    assert m.campaigns.result(c.campaign_id)["status"] == "not_sent"
    m.campaigns.set_status(c.campaign_id, "sent")
    with pytest.raises(ValueError):
        m.campaigns.edit(c.campaign_id, offer="x")
    with pytest.raises(ValueError):
        m.campaigns.set_status(c.campaign_id, "sent")
    assert m.campaigns.result(c.campaign_id)["status"] == "waiting"  # no bills after today yet


def test_csv_has_only_targets(m):
    c = m.campaigns.create(goal="reward_champions")
    rows = list(csv.DictReader(io.StringIO(m.campaigns.to_csv(c))))
    assert len(rows) == len(c.targets) and len(c.targets) < len(c.members)


# ------------------------------------------------------------------ measurement
def test_sample_campaign_measured_against_holdout(m, expected):
    r = m.campaigns.result(expected["campaign"]["campaign_id"])
    t, h = r["groups"]["target"], r["groups"]["holdout"]
    assert t["members"] + h["members"] == expected["campaign"]["members"]
    assert h["members"] == expected["campaign"]["holdout"]
    assert r["status"] == "measured" and r["days_measured"] == 14
    assert t["rate_pct"] > h["rate_pct"] and r["p_value"] < 0.05 and r["verdict"] == "worked"
    assert r["incremental_value_paise"] == round((t["value_per_member_paise"] - h["value_per_member_paise"])
                                                 * t["members"])


def test_measurement_counts_from_bills(m, expected):
    """Recompute the target conversion straight from the sales table."""
    c = m.campaigns.get(expected["campaign"]["campaign_id"])
    r = measure(c, m.require())
    s = m.require().sales
    win = s[(s["date"] >= r["window"][0]) & (s["date"] <= r["window"][1])]
    targets = {mm.customer_id for mm in c.targets}
    assert win.loc[win["customer_id"].isin(targets), "customer_id"].nunique() == r["groups"]["target"]["converted"]


def test_p_value():
    assert _p_value(50, 100, 50, 100) == 1.0
    assert _p_value(90, 100, 50, 100) < 0.001
    assert _p_value(1, 0, 1, 10) is None


# ------------------------------------------------------------------ chat + API + findings
def test_chat_routes_customer_messages_vs_supplier_orders(m):
    a = Agent(m, None)
    e = a.ask("Jo customers aana band kar diye unko message bhejo")
    assert e.evidence[0].tool == "draft_campaign" and e.evidence[0].args["goal"] == "win_back"
    assert e.language == "hinglish" and e.grounding.ungrounded == []
    assert a.ask("Udhaar wale customers ko reminder bhejo").evidence[0].args["goal"] == "udhaar_reminder"
    assert a.ask("Sharma ji ko order ka WhatsApp message likh do").evidence[0].tool == "restock_draft"
    res = a.ask("Did the campaign work?")
    assert res.evidence[0].tool == "campaign_results" and "p = " in res.answer_md
    with pytest.raises(ToolError):
        run_tool(m, "draft_campaign", {"goal": "cross_sell"})


def test_findings_offer_campaigns(m):
    acts = {f.kind: f.action for f in m.findings()}
    assert acts["churn"]["type"] == "campaign_draft" and acts["churn"]["payload"]["goal"] == "win_back"
    assert acts["udhaar_overdue"]["payload"]["goal"] == "udhaar_reminder"
    cs = acts["cross_sell"]["payload"]
    c = m.campaigns.build(goal="cross_sell", params={"item_a": cs["item_a"], "item_b": cs["item_b"]})
    assert c.members


def test_campaign_api(settings):
    with TestClient(create_app()) as c:
        assert {g["goal"] for g in c.get("/api/campaigns/goals").json()} >= {"win_back", "udhaar_reminder"}
        pv = c.post("/api/campaigns/preview", json={"goal": "cross_sell", "item_a": "aata 5kg",
                                                    "item_b": "sarson ka tel"}).json()
        assert pv["members"] > 0 and len(pv["examples"]) <= 3
        made = c.post("/api/campaigns", json={"goal": "win_back", "language": "en"}).json()
        cid = made["campaign_id"]
        assert c.patch(f"/api/campaigns/{cid}", json={"offer": "See you soon!"}).json()["offer"] == "See you soon!"
        assert c.post(f"/api/campaigns/{cid}/status", json={"status": "sent"}).json()["status"] == "sent"
        assert c.patch(f"/api/campaigns/{cid}", json={"offer": "x"}).status_code == 409
        assert c.get(f"/api/campaigns/{cid}/export.csv").headers["content-type"].startswith("text/csv")
        listed = c.get("/api/campaigns").json()
        assert any(x["campaign_id"] == "CMP-SAMPLE-WINBACK" for x in listed)
        assert c.get("/api/campaigns/CMP-SAMPLE-WINBACK/result").json()["verdict"] == "worked"
        assert c.get("/api/campaigns/nope").status_code == 404
        assert c.post("/api/campaigns", json={"goal": "cross_sell"}).status_code == 422


def test_campaign_persists_in_sql():
    from app.db.sql import SqlStore

    s = SqlStore("sqlite+pysqlite:///:memory:")
    c = Campaign(campaign_id="CMP-X", name="t", goal="win_back", segment={}, created_at="2026-11-14")
    s.upsert_campaign(c.model_dump())
    s.upsert_campaign({**c.model_dump(), "status": "sent"})
    assert [x["status"] for x in s.campaigns()] == ["sent"]
