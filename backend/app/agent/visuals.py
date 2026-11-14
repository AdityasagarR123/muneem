"""Chart / table / diagram specs built from tool results by code (never by the LLM).

chart  spec: {kind: line|bar|pie, x, series: [{key, label}], data: [...], unit: "₹"|""}
table  spec: {columns: [{key, label}], rows: [...]}
mermaid spec: {code}
kpis   spec: {cards: [{label, value_rs | value, change_pct}]}
"""

from __future__ import annotations

from app.agent.envelope import Visual
from app.analytics.common import Insight


def chart(title: str, kind: str, x: str, series: list[tuple[str, str]], data: list[dict],
          unit: str = "") -> Visual:
    keys = {x, *(k for k, _ in series)}
    return Visual(type="chart", title=title, spec={
        "kind": kind, "x": x, "unit": unit,
        "series": [{"key": k, "label": lbl} for k, lbl in series],
        "data": [{k: v for k, v in r.items() if k in keys} for r in data],
    })


def table(title: str, columns: list[tuple[str, str]], rows: list[dict], limit: int = 15) -> Visual:
    keys = [k for k, _ in columns]
    return Visual(type="table", title=title, spec={
        "columns": [{"key": k, "label": lbl} for k, lbl in columns],
        "rows": [{k: r.get(k) for k in keys} for r in rows[:limit]],
        "total_rows": len(rows),
    })


def mermaid(title: str, code: str) -> Visual:
    return Visual(type="mermaid", title=title, spec={"code": code})


def kpi_cards(ins: Insight) -> Visual:
    h = ins.headline
    cards = [
        ("Sales", "revenue_rs", "revenue_change_pct"),
        ("Margin after GST", "gross_margin_rs", "gross_margin_change_pct"),
        ("Net estimate", "net_profit_estimate_rs", "net_profit_estimate_change_pct"),
        ("Bills", "bills", "bills_change_pct"),
        ("Avg bill", "avg_bill_rs", "avg_bill_change_pct"),
        ("GST collected", "gst_collected_rs", None),
        ("Stock value (cost)", "stock_value_cost_rs", None),
    ]
    return Visual(type="kpis", title=ins.title, spec={"cards": [
        {"label": lbl, "key": key, "value": h.get(key),
         "change_pct": h.get(ch) if ch else None,
         "unit": "₹" if key.endswith("_rs") else ""}
        for lbl, key, ch in cards
    ] + [{"label": "Margin %", "key": "gross_margin_pct", "value": h.get("gross_margin_pct"),
          "change_pct": None, "unit": "%"}]})


def payment_pie(rows: list[dict], title: str) -> Visual:
    return chart(title, "pie", "payment_mode", [("value_rs", "Value")], rows, "₹")
