from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.agent.core import Agent
from app.analytics.explain import explain_change
from app.main import create_app
from app.store.manager import DataManager


@pytest.fixture(scope="module")
def manager(settings):
    m = DataManager(settings)
    m.startup()
    return m


@pytest.fixture(scope="module")
def risk_ids(manager):
    st = manager.customer_stats()
    return set(st.loc[st["at_risk"], "customer_id"])


# ------------------------------------------------------------------ explain_change
@pytest.mark.parametrize("period,compare", [
    ("this_week", "previous_period"), ("last_7_days", "previous_period"),
    ("this_month", "same_period_last_month"), ("last_30_days", "previous_period"),
])
def test_parts_add_up_to_the_paisa(ds, cal, risk_ids, period, compare):
    ins = explain_change(ds, cal, period, compare, risk_ids)
    h = ins.headline
    assert h["now_paise"] - h["before_paise"] == h["change_paise"]
    assert sum(r["effect_paise"] for r in ins.rows) == h["change_paise"]
    assert sum(b["effect_paise"] for b in ins.meta["bridge"]) == h["change_paise"]
    assert ins.rows[-1]["kind"] == "remainder"


def test_this_week_drop_is_calendar_and_diwali(ds, cal, risk_ids):
    """Mon-Fri this week vs the 5 days before (a weekend + Diwali's peak): the drop must be
    attributed to those two causes, not left unexplained."""
    ins = explain_change(ds, cal, "this_week", "previous_period", risk_ids)
    h, rows = ins.headline, {r["kind"]: r for r in ins.rows}
    assert h["direction"] == "down" and h["change_pct"] < -10
    assert rows["calendar"]["effect_paise"] < 0 and rows["calendar"]["driver"].startswith("Fewer weekend")
    assert rows["festival"]["effect_paise"] < 0 and "Diwali" in rows["festival"]["driver"]
    assert [rows["calendar"]["driver"], rows["festival"]["driver"]] == h["main_reasons"]
    assert abs(rows["remainder"]["effect_paise"]) < 0.25 * abs(h["change_paise"])
    assert "stockout" not in rows  # diyas stopping after Diwali is not a stock-out


def test_lapsed_customers_show_up_over_a_month(ds, cal, risk_ids):
    rows = {r["kind"]: r for r in explain_change(ds, cal, "last_30_days", "previous_period", risk_ids).rows}
    assert rows["lapsed_customers"]["effect_paise"] < 0


def test_no_history_is_reported_not_guessed(ds, cal):
    ins = explain_change(ds, cal, "all", "previous_period", set())
    assert ins.confidence == "no_data"


# ------------------------------------------------------------------ findings
def test_findings_cover_planted_stories(manager, expected):
    fs = manager.findings()
    kinds = [f.kind for f in fs]
    for k in ("stockout", "churn", "udhaar_overdue", "dead_stock", "low_margin", "expense_spike",
              "sales_change", "new_customers", "cross_sell", "festival_adjusted"):
        assert k in kinds, k
    assert fs[0].kind == "stockout" and fs[0].urgent  # the only thing that can't wait
    dead = next(f for f in fs if f.kind == "dead_stock")
    assert dead.impact_paise == expected["dead_stock_total_paise"]
    sc = next(f for f in fs if f.kind == "sales_change")
    assert sc.category == "info" and sc.severity == "low"  # expected drop, not an alarm
    for f in fs:
        assert f.what and f.why and f.what_hi and f.why_hi and f.action and f.evidence["tool"]
        assert f.id.endswith("@" + manager.require().as_of.isoformat())


def test_finding_evidence_tools_rerun(manager):
    from app.agent.tools import run_tool

    for f in manager.findings():
        out = run_tool(manager, f.evidence["tool"], f.evidence.get("args", {}))
        assert out.insight.formula, f.kind


def test_status_hides_dismissed(manager):
    fid = manager.findings()[-1].id
    manager.set_insight_status(fid, "dismissed")
    assert next(f for f in manager.findings() if f.id == fid).status == "dismissed"
    manager.set_insight_status(fid, "new")


def test_status_persists_in_sql(ds):
    from app.db.sql import SqlStore

    s = SqlStore("sqlite+pysqlite:///:memory:")
    s.set_insight_status("churn@2026-11-13", "acted")
    s.set_insight_status("churn@2026-11-13", "dismissed")
    assert s.insight_statuses() == {"churn@2026-11-13": "dismissed"}


# ------------------------------------------------------------------ chat + API
def test_rules_chat_why_and_focus(manager):
    a = Agent(manager, None)
    why = a.ask("Is hafte bikri kyun kam hui?")
    assert why.evidence[0].tool == "explain_change" and why.evidence[0].args["period"] == "this_week"
    assert "Fewer weekend days" in why.answer_md and why.grounding.ungrounded == []
    focus = a.ask("What should I focus on today?")
    assert focus.evidence[0].tool == "top_insights" and focus.grounding.ungrounded == []
    assert focus.actions[0].type == "restock_draft"
    assert a.ask("Aaj kya karun?").answer_md.startswith("Aaj in par dhyan dijiye")


def test_findings_api(settings):
    with TestClient(create_app()) as c:
        fs = c.get("/api/findings").json()
        assert fs and fs[0]["kind"] == "stockout"
        fid = fs[1]["id"]
        assert c.post(f"/api/findings/{fid}/status", json={"status": "dismissed"}).json()["status"] == "dismissed"
        assert fid not in [f["id"] for f in c.get("/api/findings").json()]
        assert fid in [f["id"] for f in c.get("/api/findings", params={"status": "dismissed"}).json()]
        assert c.post("/api/findings/nope/status", json={"status": "seen"}).status_code == 404
        ex = c.get("/api/explain", params={"period": "this_week"}).json()
        assert ex["headline"]["direction"] == "down"
        assert c.get("/api/explain", params={"period": "next_year"}).status_code == 422


def test_home_and_chat_history_api(settings):
    with TestClient(create_app()) as c:
        h = c.get("/api/home").json()
        assert h["briefing"][0]["kind"] == "stockout" and len(h["health"]) == 5
        assert {x["key"] for x in h["health"]} == {"sales", "margin", "repeat", "at_risk", "udhaar"}
        r = c.post("/api/chat", json={"message": "Udhaar kitna baaki hai?", "mode": "rules"}).json()
        c.post("/api/chat", json={"message": "Dead stock dikhao", "mode": "rules",
                                  "conversation_id": r["conversation_id"]})
        chats = c.get("/api/chats").json()
        assert chats[0]["conversation_id"] == r["conversation_id"] and chats[0]["turns"] == 2
        t = c.get(f"/api/chat/{r['conversation_id']}").json()
        assert t["title"] == "Udhaar kitna baaki hai?"
        assert [x["envelope"]["evidence"][0]["tool"] for x in t["turns"]] == ["udhaar_report", "dead_stock"]
