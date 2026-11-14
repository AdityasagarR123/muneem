from app.graph import insights as g
from app.graph.backends import FallbackGraph, MemoryGraph


class Broken:
    engine = "arangodb"

    def __getattr__(self, name):
        def fail(*a, **k):
            raise ConnectionError("cloud down")
        return fail


def test_fallback_graph_uses_memory_when_arango_fails(ds):
    fg = FallbackGraph(Broken(), MemoryGraph(ds))
    assert fg.supplier_items("S01")
    assert fg.engine == "memory-graph"
    assert "cloud down" in fg.last_error


def test_supplier_risk_with_delay(stock_df, ds, expected):
    res = g.supplier_risk(stock_df, MemoryGraph(ds), ds, "S01", delay_days=2)
    hit = {r["item_id"] for r in res.rows if r["runs_out_before_delivery"]}
    assert set(expected["critical_items"]) <= hit
    assert res.headline["lost_revenue_paise"] > 0


def test_substitutes_same_category_and_price_band(stock_df, ds):
    res = g.substitutes(stock_df, MemoryGraph(ds), ds, "I016", 5, 0.25)  # mustard oil
    cats = set(ds.items.set_index("item_id").loc[[r["item_id"] for r in res.rows], "category"])
    assert cats == {"Edible Oil & Ghee"}
    assert all(abs(r["price_diff_pct"]) <= 25 for r in res.rows)


def test_supplier_dependency_shares_sum_to_100(ds):
    res = g.supplier_dependency(MemoryGraph(ds), ds, 30)
    assert abs(sum(r["share_pct"] for r in res.rows) - 100) < 0.1


def test_mermaid_diagrams_are_generated(stock_df, ds):
    p = g.supply_path(stock_df, MemoryGraph(ds), ds, "I001")
    mm = p.meta["mermaid"]
    assert mm.startswith("flowchart LR") and "Sharma Traders" in mm and ":::crit" in mm
    f = g.supplier_flow(stock_df, MemoryGraph(ds), ds)
    assert "classDef crit" in f.meta["mermaid"]
    assert all(r["supplier_id"] == "S01" for r in f.rows)
