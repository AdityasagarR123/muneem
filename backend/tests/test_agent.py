import json

import pytest
from fastapi.testclient import TestClient

from app.agent import eval as agent_eval
from app.agent import grounding
from app.agent.core import Agent
from app.agent.language import detect_language
from app.agent.llm import LLMReply, LLMUnavailable, ToolCall
from app.agent.tools import TOOLS, ToolError, json_schema, run_tool
from app.core.lexicon import find_mentions
from app.main import create_app
from app.store.manager import DataManager


@pytest.fixture(scope="module")
def manager(settings):
    m = DataManager(settings)
    m.startup()
    return m


class FakeLLM:
    """Scripted stand-in for Gemini: replies are LLMReply objects or exceptions."""
    provider = "fake"
    models = ["fake-1"]

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def start(self, history, user_text):
        return [("user", user_text)]

    def add_model_turn(self, contents, reply):
        contents.append(("model", reply))

    def add_user_text(self, contents, text):
        contents.append(("user", text))

    def add_tool_results(self, contents, results):
        self.seen.append(results)
        contents.append(("tool", results))

    def generate(self, system, contents, tools, allow_tools=True):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def status(self):
        return {"provider": "fake"}


def reply(text="", calls=()):
    return LLMReply(text=text, calls=[ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(calls)],
                    model="fake-1")


# ------------------------------------------------------------------ building blocks
@pytest.mark.parametrize("text,lang", [
    ("Kal kya order karna chahiye?", "hinglish"),
    ("What should I order tomorrow?", "en"),
    ("मेरी दुकान का हिसाब बताओ", "hi"),
    ("UPI vs cash split dikhao", "hinglish"),
])
def test_language(text, lang):
    assert detect_language(text) == lang


def test_find_mentions_in_sentences(ds):
    c = list(zip(ds.items["item_id"], ds.items["name"]))
    assert find_mentions("Toor dal ka stock kitna bacha hai?", c)[0]["id"] == "I005"
    assert find_mentions("sarson ka tel ka margin", c)[0]["id"] == "I016"
    assert find_mentions("kal kya order karna chahiye", c) == []
    assert find_mentions("dal ka stock", c) == []  # 4 dals: ambiguous, not guessed


def test_every_tool_schema_is_plain_json():
    for t in TOOLS.values():
        s = json_schema(t.args)
        assert s["type"] == "object"
        assert "anyOf" not in json.dumps(s)


def test_tool_validation_and_resolution(manager):
    with pytest.raises(ToolError):
        run_tool(manager, "dead_stock", {"days": 2})  # below minimum
    with pytest.raises(ToolError) as e:
        run_tool(manager, "bought_together", {"item": "xyzzy"})
    assert "No item matches" in str(e.value)
    out = run_tool(manager, "margin_report", {"item": "aata 5kg"})
    assert out.resolved["item"]["id"] == "I001" and len(out.insight.rows) == 1


def test_grounding_check():
    ev = [{"headline": {"cash_needed_rs": 20735.98, "items": 4}, "rows": [{"days_left": 1.25}]}]
    ok = grounding.check("Order 4 items, cash ₹20,736, atta lasts 1.3 days", ev)
    assert ok.ungrounded == []
    bad = grounding.check("You will make ₹55,000 extra profit", ev)
    assert bad.ungrounded == ["55,000"]
    lakh = grounding.check("Sales were ₹9.79 lakh", [{"revenue_rs": 978949.5}])
    assert lakh.ungrounded == []


# ------------------------------------------------------------------ agent loop
def test_llm_tool_loop_produces_grounded_envelope(manager):
    llm = FakeLLM([
        reply(calls=[("reorder_plan", {})]),
        reply(text="Aaj 4 items order karo - kul ₹20,736. Sabse zaroori Chakki Atta 5kg × 50."),
    ])
    env = Agent(manager, llm).ask("Kal kya order karna chahiye?")
    assert env.mode == "llm" and env.model == "fake-1"
    assert env.evidence[0].tool == "reorder_plan" and env.evidence[0].formula
    assert env.grounding.ungrounded == []
    assert any(v.type == "table" for v in env.visuals)
    assert any(a.type == "restock_draft" for a in env.actions)
    assert llm.seen[0][0][1]["result"]["headline"]["cash_needed_rs"] == 20735.98


def test_llm_invented_number_is_flagged(manager):
    llm = FakeLLM([reply(calls=[("get_kpis", {})]),
                   reply(text="Your profit will grow to ₹12,34,567 next month.")])
    env = Agent(manager, llm).ask("How is business?")
    assert env.grounding.ungrounded and env.confidence == "partial"
    assert any("could not be traced" in w for w in env.warnings)


def test_tool_error_is_returned_to_llm(manager):
    llm = FakeLLM([reply(calls=[("bought_together", {"item": "xyzzy"})]),
                   reply(text="Which item do you mean?")])
    env = Agent(manager, llm).ask("xyzzy ke saath kya bikta hai")
    assert "error" in llm.seen[0][0][1]
    assert env.evidence[0].error


def test_llm_answering_without_tools_is_nudged(manager):
    llm = FakeLLM([reply(text="You should order rice."),
                   reply(calls=[("reorder_plan", {})]),
                   reply(text="Order 4 items.")])
    env = Agent(manager, llm).ask("What should I order tomorrow?")
    assert [e.tool for e in env.evidence] == ["reorder_plan"]


def test_llm_outage_falls_back_to_rules(manager):
    env = Agent(manager, FakeLLM([LLMUnavailable("429 everywhere")])).ask("Kal kya order karna chahiye?")
    assert env.mode == "rules"
    assert env.evidence[0].tool == "reorder_plan"
    assert any("unavailable" in w for w in env.warnings)


def test_conversation_memory(manager):
    a = Agent(manager, None)
    e1 = a.ask("Kis supplier ko pehle call karu?")
    a.ask("Supply flow dikhao", conversation_id=e1.conversation_id)
    assert len(a.conversation(e1.conversation_id)) == 4


# ------------------------------------------------------------------ rules mode + eval
def test_rules_answers_in_users_language(manager):
    a = Agent(manager, None)
    hi = a.ask("Kal kya order karna chahiye?")
    en = a.ask("What should I order tomorrow?")
    assert "order karne hain" in hi.answer_md and "Order 4 items" in en.answer_md
    assert hi.grounding.ungrounded == [] and en.grounding.ungrounded == []


def test_unknown_question_gets_help(manager):
    env = Agent(manager, None).ask("tell me a joke")
    assert env.confidence == "no_tool" and "order" in env.answer_md


def test_eval_set_rules_mode(manager):
    rep = agent_eval.run(Agent(manager, None), "rules")
    failed = [(r["q"], r["why"]) for r in rep["results"] if not r["ok"]]
    assert rep["passed"] == rep["total"], failed


# ------------------------------------------------------------------ API
def test_chat_api_json_and_stream(settings):
    with TestClient(create_app()) as c:
        r = c.post("/api/chat", json={"message": "Atta ke saath kya bikta hai?"}).json()
        assert r["evidence"][0]["tool"] == "bought_together" and r["mode"] == "rules"
        with c.stream("POST", "/api/chat",
                      json={"message": "Dead stock dikhao", "stream": True}) as s:
            body = "".join(s.iter_text())
        events = [l[7:] for l in body.splitlines() if l.startswith("event: ")]
        assert events[0] == "meta" and "tool_call" in events and events[-1] == "final"
        assert c.get(f"/api/chat/{r['conversation_id']}").status_code == 200
        assert c.get("/api/agent/status").json()["llm_configured"] is False
        assert c.post("/api/chat", json={"message": ""}).status_code == 422
