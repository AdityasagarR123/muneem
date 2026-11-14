import pytest
from fastapi.testclient import TestClient

from app.agent import charts
from app.agent.charts import ChartArgs, ChartError, build_chart, render_png
from app.agent.core import Agent
from app.agent.llm import LLMReply, ToolCall
from app.agent.tools import run_tool
from app.main import create_app
from app.store.manager import DataManager

DS = {"title": "Sales by category", "headline": {},
      "rows": [{"group": f"C{i}", "value_rs": 100.0 * (10 - i), "share_pct": 10.0, "bills": 5}
               for i in range(10)]}


@pytest.fixture(scope="module")
def manager(settings):
    m = DataManager(settings)
    m.startup()
    return m


def args(**kw):
    return ChartArgs(**{"dataset_id": "d1", "x": "group", "y": ["value_rs"], **kw})


def test_columns_typed():
    assert charts.columns(DS["rows"]) == {"group": "text", "value_rs": "number", "share_pct": "number",
                                          "bills": "number"}


@pytest.mark.parametrize("kw,msg", [
    ({"kind": "bar", "y": ["nope"]}, "not in dataset"),
    ({"kind": "bar", "y": ["group"]}, "is text"),
    ({"kind": "bar", "y": ["value_rs", "share_pct"]}, "Don't mix"),
    ({"kind": "scatter"}, "numeric x"),
    ({"kind": "cohort_heatmap"}, "column_key, value_key"),
])
def test_bad_charts_are_refused(kw, msg):
    with pytest.raises(ChartError, match=msg):
        build_chart(DS, args(**kw))


def test_funnel_must_decrease_and_pie_needs_positive():
    up = {"rows": [{"stage": "a", "n": 1}, {"stage": "b", "n": 5}]}
    with pytest.raises(ChartError, match="not decreasing"):
        build_chart(up, ChartArgs(dataset_id="d1", kind="funnel", x="stage", y=["n"]))
    neg = {"rows": [{"k": "a", "v_rs": -1.0}, {"k": "b", "v_rs": 2.0}]}
    with pytest.raises(ChartError, match="negative"):
        build_chart(neg, ChartArgs(dataset_id="d1", kind="pie", x="k", y=["v_rs"]))


def test_big_pie_groups_the_rest_as_other():
    v = build_chart(DS, args(kind="pie"))
    data = v.spec["data"]
    assert len(data) == 8 and data[-1]["group"] == "Other (3)"
    assert sum(r["value_rs"] for r in data) == sum(r["value_rs"] for r in DS["rows"])


def test_top_n_pie_ok():
    v = build_chart(DS, args(kind="pie", top_n=6))
    assert v.spec["kind"] == "pie" and len(v.spec["data"]) == 6 and v.spec["unit"] == "₹"


@pytest.mark.parametrize("tool,targs", [
    ("explain_change", {"period": "this_week"}), ("lifecycle_funnel", {}), ("cohort_retention", {}),
    ("customer_segments", {}), ("payment_mix", {}),
    ("query_metric", {"metric": "revenue", "group_by": "day", "period": "last_90_days", "compare": "none"}),
    ("query_metric", {"metric": "revenue", "group_by": "category"}),
])
def test_every_default_chart_renders_png(manager, tool, targs):
    for v in run_tool(manager, tool, targs).visuals:
        if v.type == "chart":
            png = render_png(v.title, v.spec)
            assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 5000


def test_default_chart_kinds(manager):
    kinds = lambda t, a={}: [v.spec.get("kind") for v in run_tool(manager, t, a).visuals if v.type == "chart"]  # noqa: E731
    assert kinds("explain_change", {"period": "this_week"}) == ["waterfall"]
    assert kinds("lifecycle_funnel") == ["funnel"]
    assert kinds("cohort_retention") == ["cohort_heatmap"]
    assert "scatter" in kinds("customer_segments")


class FakeLLM:
    provider, models = "fake", ["fake-1"]

    def __init__(self, script):
        self.script, self.seen = list(script), []

    def start(self, history, text):
        return []

    def add_model_turn(self, c, r):
        pass

    def add_user_text(self, c, t):
        pass

    def add_tool_results(self, c, results):
        self.seen.append(results)

    def generate(self, *a, **k):
        return self.script.pop(0)

    def status(self):
        return {}


def call(name, a):
    return LLMReply(text="", calls=[ToolCall("x", name, a)], model="fake-1")


def test_make_chart_replaces_default_chart_and_png_served(manager, settings):
    llm = FakeLLM([
        call("query_metric", {"metric": "revenue", "group_by": "category"}),
        call("make_chart", {"dataset_id": "d1", "kind": "pie", "x": "group", "y": ["value_rs"], "top_n": 6}),
        LLMReply(text="Staples bring the biggest share.", calls=[], model="fake-1"),
    ])
    env = Agent(manager, llm).ask("What share of sales comes from each category?")
    view = llm.seen[0][0][1]["result"]
    assert view["dataset_id"] == "d1" and view["columns"]["value_rs"] == "number"
    assert llm.seen[1][0][1] == {"result": {"chart": "drawn", "kind": "pie", "points": 6}}  # no numbers
    charts_ = [v for v in env.visuals if v.type == "chart"]
    assert [v.spec["kind"] for v in charts_] == ["pie"]  # default bar replaced
    url = charts_[0].spec["png_url"]
    with TestClient(create_app()) as c:
        r = c.get(url)
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert c.get("/api/charts/nope.png").status_code == 404


def test_make_chart_errors_go_back_to_the_model(manager):
    llm = FakeLLM([
        call("query_metric", {"metric": "revenue", "group_by": "category"}),
        call("make_chart", {"dataset_id": "d9", "kind": "pie", "x": "group", "y": ["value_rs"]}),
        call("make_chart", {"dataset_id": "d1", "kind": "line", "x": "group", "y": ["colour"]}),
        LLMReply(text="Here is the breakdown.", calls=[], model="fake-1"),
    ])
    env = Agent(manager, llm).ask("chart it")
    assert "Unknown dataset_id 'd9'" in llm.seen[1][0][1]["error"]
    assert "not in dataset" in llm.seen[2][0][1]["error"]
    assert [v.spec["kind"] for v in env.visuals if v.type == "chart"] == ["bar"]  # default kept


def test_explain_offers_extra_datasets(manager):
    llm = FakeLLM([call("explain_change", {"period": "this_week"}),
                   LLMReply(text="Down because of fewer weekend days.", calls=[], model="fake-1")])
    Agent(manager, llm).ask("why")
    view = llm.seen[0][0][1]["result"]
    assert "d1.bridge" in view["more_datasets"] and "d1.category_movers" in view["more_datasets"]
