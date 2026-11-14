"""Web research (Phase 7): privacy scrub, caching, budgets, cited sources - all with mocked HTTP."""

import json

import httpx
import pytest

from app.agent import grounding
from app.agent.core import Agent
from app.agent.tools import ToolError, run_tool
from app.research.web import ResearchError, WebResearch, public_url, scrub
from app.store.manager import DataManager

from tests.test_agent import FakeLLM, reply

TAVILY_OK = {"results": [
    {"title": "Sugar prices rise ahead of festive season", "url": "https://news.example.in/sugar",
     "content": "Wholesale sugar rose to Rs 4,150 per quintal this week, traders said.",
     "score": 0.91, "published_date": "Wed, 11 Nov 2026 08:00:00 GMT"},
    {"title": "Commodity watch", "url": "https://markets.example.com/c", "content": "Edible oil steady.",
     "score": 0.6},
]}


class Recorder:
    def __init__(self, status=200, body=None):
        self.calls: list[httpx.Request] = []
        self.status, self.body = status, body if body is not None else TAVILY_OK

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(self.status, json=self.body)


def research(settings, rec, **over):
    s = settings.model_copy(update={"tavily_api_key": "tvly-test", "tinyfish_api_key": "tf-test", **over})
    return WebResearch(s, http=httpx.Client(transport=httpx.MockTransport(rec)))


@pytest.fixture()
def manager(settings):
    m = DataManager(settings)
    m.startup()
    return m


# ------------------------------------------------------------------ privacy
def test_scrub_removes_private_details():
    q, removed = scrub("Ramesh Kumar 9876543210 ramesh@okicici paid ₹12,400 for C0012 sugar price",
                       ["Ramesh Kumar", "Muneem Kirana Store"])
    assert "9876543210" not in q and "Ramesh" not in q and "12,400" not in q and "C0012" not in q
    assert "okicici" not in q
    assert "sugar price" in q
    assert {"phone number", "amount", "customer or shop name", "UPI id", "internal id"} <= set(removed)


def test_scrub_keeps_plain_market_question():
    q, removed = scrub("sugar wholesale price India this week", ["Ramesh Kumar"])
    assert q == "sugar wholesale price India this week" and removed == []


@pytest.mark.parametrize("url", ["http://example.com", "https://localhost/x", "https://127.0.0.1/",
                                 "https://10.0.0.5/admin", "https://printer.local/", "ftp://x.com"])
def test_public_url_rejects_non_public(url):
    with pytest.raises(ResearchError):
        public_url(url)


# ------------------------------------------------------------------ client
def test_search_shapes_request_and_caches(settings):
    rec = Recorder()
    r = research(settings, rec)
    a = r.search("sugar price", time_range="week")
    b = r.search("sugar price", time_range="week")
    assert len(rec.calls) == 1, "second identical search must come from cache"
    assert a["cached"] is False and b["cached"] is True
    sent = json.loads(rec.calls[0].content)
    assert sent["search_depth"] == "basic" and sent["include_published_date"] is True
    assert sent["country"] == "india" and sent["time_range"] == "week"
    assert rec.calls[0].headers["authorization"] == "Bearer tvly-test"
    assert a["results"][0]["domain"] == "news.example.in" and a["retrieved_at"]


def test_daily_budget_caps_calls(settings):
    r = research(settings, Recorder(), tavily_daily_limit=2)
    r.search("a1 query")
    r.search("a2 query")
    with pytest.raises(ResearchError, match="limit"):
        r.search("a3 query")


@pytest.mark.parametrize("status,msg", [(401, "rejected"), (429, "busy"), (432, "plan limit")])
def test_provider_errors_are_friendly(settings, status, msg):
    r = research(settings, Recorder(status=status, body={"detail": {"error": "x"}}))
    with pytest.raises(ResearchError, match=msg):
        r.search("sugar price")


def test_disabled_without_key(settings):
    r = WebResearch(settings)  # conftest blanks both keys
    assert not r.search_ready and not r.extract_ready
    with pytest.raises(ResearchError, match="not set up"):
        r.search("sugar")


def test_extract_parses_tinyfish(settings):
    rec = Recorder(body={"run_id": "r1", "status": "COMPLETED", "num_of_steps": 3, "error": None,
                         "result": {"status": "found", "summary": "Atta 5kg listed at ₹285",
                                    "facts": [{"label": "Aashirvaad Atta 5kg", "value": "₹285"}]}})
    r = research(settings, rec)
    out = r.extract("https://shop.example.com/atta", "price of atta 5kg")
    sent = json.loads(rec.calls[0].content)
    assert rec.calls[0].headers["x-api-key"] == "tf-test"
    assert sent["agent_config"]["max_steps"] == settings.tinyfish_max_steps
    assert sent["browser_profile"] == "lite" and "output_schema" in sent
    assert out["facts"][0]["value"] == "₹285" and out["steps"] == 3


def test_extract_failed_run(settings):
    rec = Recorder(body={"status": "FAILED", "error": {"message": "blocked"}})
    with pytest.raises(ResearchError, match="blocked"):
        research(settings, rec).extract("https://shop.example.com/", "price")


# ------------------------------------------------------------------ agent tool + envelope
def test_web_tool_scrubs_customer_names(manager, settings):
    rec = Recorder()
    manager._research = research(settings, rec)
    full = next(n for n in manager.ds.customers["name"] if " " in n)
    out = run_tool(manager, "web_search", {"query": f"{full} 9876543210 sugar price trend"})
    sent = json.loads(rec.calls[0].content)["query"]
    assert full.split()[0] not in sent and "9876543210" not in sent and "sugar price trend" in sent
    assert out.insight.meta["external"] is True
    assert any("Removed before searching" in a for a in out.insight.assumptions)


def test_web_tool_errors_become_tool_errors(manager, settings):
    manager._research = research(settings, Recorder(status=401, body={}))
    with pytest.raises(ToolError, match="rejected"):
        run_tool(manager, "web_search", {"query": "sugar price"})


def test_llm_answer_gets_separate_cited_sources(manager, settings):
    manager._research = research(settings, Recorder())
    llm = FakeLLM([
        reply(calls=[("web_search", {"query": "sugar wholesale price", "time_range": "week"})]),
        reply(text="From the web: news.example.in (11 Nov 2026) reports wholesale sugar at ₹4,150 "
                   "per quintal; not verified by Muneem."),
    ])
    env = Agent(manager, llm).ask("what is the sugar market price this week?")
    assert env.sources and env.sources[0].url == "https://news.example.in/sugar"
    assert env.sources[0].published_date and env.sources[0].retrieved_at
    assert env.sources[0].provider == "Tavily"
    assert env.evidence[0].external is True
    # the web figure is traced to a cited source, but is not counted as verified shop data
    assert env.grounding.ungrounded == [] and env.grounding.from_web >= 1


def test_grounding_separates_web_numbers():
    views = [{"title": "kpis", "headline": {"revenue_rs": 52000}},
             {"title": "web", "external": True, "rows": [{"snippet": "sugar at Rs 4,150"}]}]
    g = grounding.check("Sales ₹52,000; sugar ₹4,150 on the web; oil ₹999", views)
    assert g.from_web == 1 and g.ungrounded == ["999"]


def test_rules_mode_web_intent(manager, settings):
    manager._research = research(settings, Recorder())
    env = Agent(manager, None).ask("latest news on sugar market price")
    assert env.mode == "rules" and env.sources
    assert env.answer_md.startswith("From the web")
    assert "date unknown" in env.answer_md  # the second result has no date


def test_rules_mode_skips_web_without_key(manager):
    manager._research = WebResearch(manager.settings)
    env = Agent(manager, None).ask("latest news on sugar market price")
    assert not env.sources


def test_web_tools_hidden_from_llm_without_keys(manager):
    manager._research = WebResearch(manager.settings)
    names = {d["name"] for d in Agent(manager, FakeLLM([])).tool_decls}
    assert "web_search" not in names and "web_extract" not in names


def test_extract_falls_back_when_step_limit_not_allowed(settings):
    sent = []

    def handler(req):
        body = json.loads(req.content)
        sent.append(body)
        if "agent_config" in body:
            return httpx.Response(403, json={"error": {"code": "FORBIDDEN", "message":
                                  "Your account is not enabled for custom max steps yet."}})
        return httpx.Response(200, json={"status": "COMPLETED", "num_of_steps": 2,
                                         "result": {"status": "found", "summary": "ok"}})

    r = research(settings, handler)
    assert r.extract("https://example.com/", "heading")["summary"] == "ok"
    assert "agent_config" in sent[0] and "agent_config" not in sent[1]
    r.extract("https://example.com/b", "heading")
    assert len(sent) == 3 and "agent_config" not in sent[2]  # remembered, no wasted call


@pytest.mark.parametrize("q,window", [("sugar price trend India this week", "week"),
                                      ("latest GST rate on biscuits", "week"),
                                      ("aaj cheeni ka bhav", "day"),
                                      ("edible oil prices this month", "month"),
                                      ("GST rate on biscuits", None)])
def test_recency_window_inferred(manager, settings, q, window):
    rec = Recorder()
    manager._research = research(settings, rec)
    run_tool(manager, "web_search", {"query": q, "topic": "finance"})
    assert json.loads(rec.calls[0].content).get("time_range") == window


def test_trivial_numbers_are_not_counted_as_verified():
    views = [{"title": "web", "external": True, "rows": [{"snippet": "sugar at Rs 4,716 on 05 Aug",
                                                       "published_date": "Wed, 05 Aug 2026"}]}]
    g = grounding.check("From the web (05 Aug 2026): sugar ₹4,716.", views)
    assert g.verified == 0 and g.from_web >= 1 and g.ungrounded == []
