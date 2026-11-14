"""Mermaid diagrams generated from computed data (never written by the LLM)."""

from __future__ import annotations

import re

from app.core.money import fmt_inr

_UNSAFE = re.compile(r"[\"\[\]{}()<>|`#;]")
TIER_CLASS = {"CRITICAL": "crit", "SOON": "soon", "OK": "ok", "UNKNOWN": "unk"}
CLASS_DEFS = [
    "classDef crit fill:#fde2e1,stroke:#c0392b,color:#7b1d14",
    "classDef soon fill:#fff1d6,stroke:#d68910,color:#7a4b00",
    "classDef ok fill:#e3f4e8,stroke:#1e8449,color:#145a32",
    "classDef unk fill:#eceff1,stroke:#78909c,color:#37474f",
    "classDef sup fill:#e8eaf6,stroke:#3949ab,color:#1a237e",
]


def label(text, max_len: int = 40) -> str:
    return _UNSAFE.sub(" ", str(text))[:max_len].strip() or "-"


def _nid(prefix: str, raw: str) -> str:
    return prefix + re.sub(r"[^A-Za-z0-9_]", "_", str(raw))


def supplier_flow(chains: list[dict], stock_by_item: dict[str, dict]) -> str:
    """supplier --lead--> item [stock / days left] , coloured by tier."""
    lines = ["flowchart LR"]
    seen_sup = set()
    for c in chains:
        s = _nid("S_", c["supplier_id"])
        i = _nid("I_", c["item_id"])
        if s not in seen_sup:
            lines.append(f'  {s}["{label(c["supplier"])}"]:::sup')
            seen_sup.add(s)
        st = stock_by_item.get(c["item_id"], {})
        dl = st.get("days_left")
        info = f'stock {st.get("stock", 0):g}' + (f" · {dl:g}d left" if dl is not None else "")
        cls = TIER_CLASS.get(st.get("tier", "UNKNOWN"), "unk")
        lines.append(f'  {i}["{label(c["item"])}<br/>{info}"]:::{cls}')
        lines.append(f'  {s} -->|"{int(c.get("lead_time_days") or 0)} day lead"| {i}')
    lines += [f"  {d}" for d in CLASS_DEFS]
    return "\n".join(lines)


def item_flow(ctx: dict, stock: dict, together: list[dict]) -> str:
    it = ctx["item"]
    i = _nid("I_", it["item_id"])
    lines = ["flowchart LR"]
    sup = ctx.get("supplier")
    if sup:
        s = _nid("S_", sup["supplier_id"])
        lines.append(f'  {s}["{label(sup["name"])}"]:::sup')
        lines.append(f'  {s} -->|"{int(it.get("lead_time_days") or 0)} day lead"| {i}')
    dl = stock.get("days_left")
    info = f'stock {stock.get("stock", 0):g}' + (f" · {dl:g}d left" if dl is not None else "")
    cls = TIER_CLASS.get(stock.get("tier", "UNKNOWN"), "unk")
    lines.append(f'  {i}["{label(it["name"])}<br/>{info}"]:::{cls}')
    c = _nid("C_", ctx.get("category") or "cat")
    lines.append(f'  {i} --> {c}(("{label(ctx.get("category"))}"))')
    for t in together[:3]:
        o = _nid("I_", t["item_id"])
        lines.append(f'  {o}["{label(t.get("name", t["item_id"]))}"]')
        lines.append(f'  {i} -.->|"bought together {t["together"]}x"| {o}')
    lines += [f"  {d}" for d in CLASS_DEFS]
    return "\n".join(lines)


def money_flow(kpi_headline: dict) -> str:
    """Where this period's money went - a simple top-down flow."""
    h = kpi_headline
    return "\n".join([
        "flowchart TD",
        f'  R["Sales {fmt_inr(h.get("revenue_paise", 0))}"] --> G["GST collected '
        f'{fmt_inr(h.get("gst_collected_paise", 0))}"]',
        f'  R --> T["Taxable sales {fmt_inr(h.get("taxable_revenue_paise", 0))}"]',
        f'  T --> C["Cost of goods {fmt_inr(h.get("cogs_paise", 0))}"]',
        f'  T --> M["Gross margin {fmt_inr(h.get("gross_margin_paise", 0))}"]',
        f'  M --> O["Overheads {fmt_inr(h.get("overheads_paise", 0))}"]',
        f'  M --> N["Net estimate {fmt_inr(h.get("net_profit_estimate_paise", 0))}"]',
    ])
