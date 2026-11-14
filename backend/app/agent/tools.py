"""The fixed set of tools the agent may call. Each tool:
* has a Pydantic argument model (validated before anything runs),
* resolves item/supplier/category names in code (Hinglish-aware),
* returns an Insight (numbers + formula) plus chart/table specs and suggested actions.

The LLM only chooses a tool and its arguments. It never runs code or queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional, get_args, get_origin

from pydantic import BaseModel, Field, ValidationError

from app.agent import visuals as V
from app.agent.envelope import Action, Visual
from app.analytics import kpis as kpis_mod
from app.analytics import margin as margin_mod
from app.analytics import sales as sales_mod
from app.analytics import stock as stock_mod
from app.analytics.common import Insight, insight
from app.core.lexicon import find_mentions, match_names
from app.core.money import to_paise
from app.graph import insights as g
from app.graph import mermaid as mm
from app.store.manager import DataManager


class ToolError(Exception):
    def __init__(self, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.suggestions = suggestions or []


@dataclass
class ToolOutput:
    insight: Insight
    visuals: list[Visual] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    resolved: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ resolution
def _weights(m: DataManager) -> dict[str, float]:
    def build(ds):
        q = ds.sales.groupby("item_id")["qty"].sum()
        top = float(q.max()) if len(q) else 1.0
        return (q / top).to_dict()
    return m._cached("item_weights", build)


def resolve_item(m: DataManager, ref: str) -> tuple[str, str]:
    ds = m.require()
    ref = (ref or "").strip()
    if ref in ds.item_index.index:
        return ref, ds.item_index.at[ref, "name"]
    cands = list(zip(ds.items["item_id"], ds.items["name"]))
    hits = find_mentions(ref, cands, _weights(m)) or match_names(ref, cands, limit=3, min_score=0.5)
    if not hits:
        loose = match_names(ref, cands, limit=3, min_score=0.25)
        raise ToolError(f"No item matches '{ref}'.", [h["name"] for h in loose])
    # several close matches (e.g. 'atta' -> 5kg and 10kg): best-seller wins via sales weight
    return hits[0]["id"], hits[0]["name"]


def resolve_supplier(m: DataManager, ref: str) -> tuple[str, str]:
    ds = m.require()
    ref = (ref or "").strip()
    if ref in ds.supplier_index.index:
        return ref, ds.supplier_index.at[ref, "name"]
    cands = list(zip(ds.suppliers["supplier_id"], ds.suppliers["name"]))
    hits = find_mentions(ref, cands) or match_names(ref, cands, limit=1, min_score=0.5)
    if not hits:
        raise ToolError(f"No supplier matches '{ref}'.", ds.suppliers["name"].tolist()[:8])
    return hits[0]["id"], hits[0]["name"]


def resolve_category(m: DataManager, ref: str) -> str:
    ds = m.require()
    cats = sorted(ds.items["category"].unique())
    for c in cats:
        if c.lower() == (ref or "").strip().lower():
            return c
    hits = find_mentions(ref, [(c, c) for c in cats]) or match_names(ref, [(c, c) for c in cats],
                                                                     limit=1, min_score=0.5)
    if not hits:
        raise ToolError(f"No category matches '{ref}'.", cats)
    return hits[0]["id"]


# ------------------------------------------------------------------ arg models
Grain = Literal["day", "week", "month"]


class NoArgs(BaseModel):
    pass


class PeriodArgs(BaseModel):
    period_days: int = Field(30, ge=1, le=365, description="How many recent days to look at")


class StockArgs(BaseModel):
    item: Optional[str] = Field(None, description="Item name or id as the owner said it, e.g. 'aata 5kg'")
    tier: Optional[Literal["critical", "soon", "ok", "unknown"]] = Field(
        None, description="Only items in this stock tier")


class ReorderArgs(BaseModel):
    budget_rs: Optional[float] = Field(None, ge=0, description="Cash available for purchases, in rupees")
    supplier: Optional[str] = Field(None, description="Only items from this supplier")


class DeadArgs(BaseModel):
    days: int = Field(30, ge=7, le=365, description="No sale in this many days counts as dead")


class MarginArgs(BaseModel):
    group_by: Literal["item", "category", "supplier"] = "item"
    period_days: int = Field(30, ge=1, le=365)
    item: Optional[str] = Field(None, description="Show margin for just this item")


class PaymentArgs(BaseModel):
    period_days: int = Field(90, ge=7, le=365)


class TrendArgs(BaseModel):
    item: Optional[str] = Field(None, description="Item name/id; leave empty for whole shop")
    category: Optional[str] = Field(None, description="Category name, e.g. 'sweets', 'dairy'")
    grain: Grain = "day"
    period_days: int = Field(90, ge=7, le=365)


class TopArgs(BaseModel):
    by: Literal["revenue", "margin", "units", "margin_pct"] = "revenue"
    n: int = Field(10, ge=1, le=30)
    period_days: int = Field(30, ge=1, le=365)


class SupplierRiskArgs(BaseModel):
    supplier: str = Field(..., description="Supplier name or id, e.g. 'Sharma'")
    delay_days: int = Field(2, ge=0, le=30, description="How many days late the delivery is")


class ItemArgs(BaseModel):
    item: str = Field(..., description="Item name or id as the owner said it")
    n: int = Field(5, ge=1, le=10)


class ItemOnlyArgs(BaseModel):
    item: str = Field(..., description="Item name or id as the owner said it")


class FlowArgs(BaseModel):
    supplier: Optional[str] = Field(None, description="Only this supplier; empty = items at risk")


class DraftArgs(BaseModel):
    supplier: Optional[str] = Field(None, description="Only this supplier; empty = every supplier "
                                                      "with items to order")
    budget_rs: Optional[float] = Field(None, ge=0, description="Cash available, in rupees")
    language: Literal["en", "hinglish", "hi"] = Field(
        "hinglish", description="Language of the supplier message - match the owner's language")


class FindArgs(BaseModel):
    query: str = Field(..., description="Words describing an item, supplier or category")


# ------------------------------------------------------------------ tool fns
STOCK_COLS = [("name", "Item"), ("tier", "Status"), ("stock", "Stock"), ("days_left", "Days left"),
              ("order_qty", "Order qty"), ("supplier_id", "Supplier")]


def _restock_action(payload: dict | None = None, label="Make restock list") -> Action:
    return Action(type="restock_draft", label=label, payload=payload or {})


def t_kpis(m, a: PeriodArgs) -> ToolOutput:
    ins = kpis_mod.compute_kpis(m.require(), a.period_days)
    pay = [{"payment_mode": k, "value_rs": v["value_rs"]}
           for k, v in (ins.headline.get("payment_split") or {}).items()]
    return ToolOutput(ins, [V.kpi_cards(ins), V.payment_pie(pay, "Payment mix")],
                      suggestions=["Kal kya order karna chahiye?", "Which items are dead stock?"])


def t_stock(m, a: StockArgs) -> ToolOutput:
    df, resolved = m.stock(), {}
    if a.tier:
        df = df[df["tier"] == a.tier.upper()]
    ids = None
    if a.item:
        iid, name = resolve_item(m, a.item)
        ids, resolved = [iid], {"item": {"said": a.item, "id": iid, "name": name}}
    ins = stock_mod.stock_status_insight(df, m.require(), ids)
    risky = [r for r in ins.rows if r["tier"] in ("CRITICAL", "SOON")]
    acts = [_restock_action()] if risky else []
    return ToolOutput(ins, [V.table("Stock status", STOCK_COLS, ins.rows)], acts, resolved,
                      ["Which supplier should I call first?"])


def t_reorder(m, a: ReorderArgs) -> ToolOutput:
    sid, resolved = None, {}
    if a.supplier:
        sid, sname = resolve_supplier(m, a.supplier)
        resolved = {"supplier": {"said": a.supplier, "id": sid, "name": sname}}
    budget = to_paise(a.budget_rs) if a.budget_rs is not None else None
    ins = stock_mod.reorder_plan(m.stock(), m.require(), budget, sid)
    cols = [("name", "Item"), ("tier", "Status"), ("days_left", "Days left"),
            ("order_qty", "Order qty"), ("order_value_rs", "Value ₹"), ("supplier", "Supplier")]
    vis = [V.table("What to order", cols, ins.rows),
           V.chart("Order value by supplier", "bar", "supplier",
                   [("order_value_incl_gst_rs", "Cash needed ₹")], ins.meta.get("by_supplier", []), "₹")]
    acts = []
    if ins.rows:
        acts = [_restock_action({"budget_rs": a.budget_rs, "supplier_id": sid}),
                Action(type="download_csv", label="Download order list",
                       payload={"path": "/api/restock/export.csv"})]
    return ToolOutput(ins, vis, acts, resolved,
                      ["Sharma Traders late ho gaya to kya hoga?", "Budget ₹10,000 ho to kya order karu?"])


def t_dead(m, a: DeadArgs) -> ToolOutput:
    ins = stock_mod.dead_stock(m.require(), a.days)
    cols = [("name", "Item"), ("stock", "Stock"), ("days_since_last_sale", "Days unsold"),
            ("money_trapped_rs", "Money stuck ₹"), ("max_discount_pct", "Max discount %")]
    return ToolOutput(ins, [V.table(ins.title, cols, ins.rows),
                            V.chart("Money stuck", "bar", "name", [("money_trapped_rs", "₹")],
                                    ins.rows, "₹")],
                      suggestions=["Which items sell slowly?"])


def t_slow(m, a: NoArgs) -> ToolOutput:
    ins = stock_mod.slow_movers(m.require(), m.settings.slow_mover_ratio)
    cols = [("name", "Item"), ("velocity", "Units/day"), ("category_median", "Category median"),
            ("days_of_cover", "Days of stock"), ("stock_value_rs", "Stock value ₹")]
    return ToolOutput(ins, [V.table(ins.title, cols, ins.rows)])


def t_margin(m, a: MarginArgs) -> ToolOutput:
    ds = m.require()
    ins = margin_mod.margin_report(ds, a.period_days, a.group_by)
    resolved = {}
    if a.item:
        iid, name = resolve_item(m, a.item)
        resolved = {"item": {"said": a.item, "id": iid, "name": name}}
        ins = ins.model_copy(update={"rows": [r for r in ins.rows if r.get("item_id") == iid],
                                     "title": f"Margin - {name} (last {a.period_days} days)"})
    key = {"item": "name", "category": "category", "supplier": "supplier"}[a.group_by]
    if a.group_by == "supplier" and ins.rows and "supplier" not in ins.rows[0]:
        key = "supplier_id"
    vis = [V.chart(ins.title, "bar", key, [("margin_rs", "Margin ₹"), ("margin_pct", "Margin %")],
                   ins.rows[:12], "₹")]
    return ToolOutput(ins, vis, resolved=resolved,
                      suggestions=["Which items sell a lot but earn little?"])


def t_payments(m, a: PaymentArgs) -> ToolOutput:
    ins = sales_mod.payment_mix(m.require(), a.period_days)
    weekly = ins.meta.get("weekly_share_pct", [])
    return ToolOutput(ins, [V.payment_pie(ins.rows, ins.title),
                            V.chart("UPI share by week (%)", "line", "week", [("UPI", "UPI %")],
                                    weekly, "%")])


def t_trend(m, a: TrendArgs) -> ToolOutput:
    iid, cat, resolved = None, None, {}
    if a.item:
        iid, name = resolve_item(m, a.item)
        resolved["item"] = {"said": a.item, "id": iid, "name": name}
    elif a.category:
        cat = resolve_category(m, a.category)
        resolved["category"] = {"said": a.category, "name": cat}
    ins = sales_mod.sales_trend(m.require(), m.calendar, iid, cat, a.grain, a.period_days)
    if a.grain == "day":  # the LLM gets a weekly summary instead of 90 daily rows
        weekly = sales_mod.sales_trend(m.require(), m.calendar, iid, cat, "week", a.period_days)
        ins.meta["llm_rows"] = weekly.rows
    series = [("units", "Units"), ("units_ma7", "7-day avg")] if a.grain == "day" else \
        [("units_per_day", "Units per day")]
    vis = V.chart(ins.title, "line", "period", series, ins.rows)
    vis.spec["annotations"] = ins.meta.get("festivals", [])
    return ToolOutput(ins, [vis], resolved=resolved)


def t_top(m, a: TopArgs) -> ToolOutput:
    ins = sales_mod.top_items(m.require(), a.by, a.n, a.period_days)
    col = {"revenue": "revenue_rs", "margin": "margin_rs", "units": "units",
           "margin_pct": "margin_pct"}[a.by]
    return ToolOutput(ins, [V.chart(ins.title, "bar", "name", [(col, a.by)], ins.rows,
                                    "₹" if col.endswith("_rs") else "")])


def t_expenses(m, a: NoArgs) -> ToolOutput:
    ins = sales_mod.expense_report(m.require())
    return ToolOutput(ins, [V.chart(ins.title, "bar", "category",
                                    [("current_rs", "Last 30 days ₹"),
                                     ("prior_mean_rs", "Earlier average ₹")], ins.rows, "₹")])


def t_supplier_priority(m, a: NoArgs) -> ToolOutput:
    ins = g.supplier_priority(m.stock(), m.graph(), m.require())
    cols = [("rank", "#"), ("supplier", "Supplier"), ("critical", "Critical"), ("soon", "Soon"),
            ("first_stockout_days", "Runs out in (days)"), ("cash_needed_rs", "Cash needed ₹")]
    acts = []
    if ins.rows:
        top = ins.rows[0]
        acts.append(_restock_action({"supplier_id": top["supplier_id"]},
                                    f"Draft order for {top['supplier']}"))
    return ToolOutput(ins, [V.table(ins.title, cols, ins.rows)], acts,
                      suggestions=["Supply flow dikhao"])


def t_supplier_risk(m, a: SupplierRiskArgs) -> ToolOutput:
    sid, sname = resolve_supplier(m, a.supplier)
    ins = g.supplier_risk(m.stock(), m.graph(), m.require(), sid, a.delay_days)
    cols = [("name", "Item"), ("days_left", "Days left"), ("delayed_lead_days", "Delivery in"),
            ("runs_out_before_delivery", "Runs out?"), ("lost_revenue_rs", "Sales lost ₹")]
    return ToolOutput(ins, [V.table(ins.title, cols, ins.rows)],
                      [_restock_action({"supplier_id": sid}, f"Draft order for {sname}")],
                      {"supplier": {"said": a.supplier, "id": sid, "name": sname}})


def t_dependency(m, a: PeriodArgs) -> ToolOutput:
    ins = g.supplier_dependency(m.graph(), m.require(), a.period_days)
    return ToolOutput(ins, [V.chart(ins.title, "pie", "supplier", [("revenue_rs", "Sales ₹")],
                                    ins.rows, "₹")])


def t_bought_together(m, a: ItemArgs) -> ToolOutput:
    iid, name = resolve_item(m, a.item)
    ins = g.bought_together(m.graph(), m.require(), iid, a.n)
    return ToolOutput(ins, [V.chart(ins.title, "bar", "name", [("bills_together", "Bills together")],
                                    ins.rows)], resolved={"item": {"said": a.item, "id": iid,
                                                                   "name": name}})


def t_substitutes(m, a: ItemArgs) -> ToolOutput:
    iid, name = resolve_item(m, a.item)
    ins = g.substitutes(m.stock(), m.graph(), m.require(), iid, a.n,
                        m.settings.substitute_price_band)
    cols = [("name", "Item"), ("price_diff_pct", "Price diff %"), ("stock", "Stock"),
            ("tier", "Status"), ("available", "Available")]
    return ToolOutput(ins, [V.table(ins.title, cols, ins.rows)],
                      resolved={"item": {"said": a.item, "id": iid, "name": name}})


def t_supply_path(m, a: ItemOnlyArgs) -> ToolOutput:
    iid, name = resolve_item(m, a.item)
    ins = g.supply_path(m.stock(), m.graph(), m.require(), iid)
    return ToolOutput(ins, [V.mermaid(ins.title, ins.meta.get("mermaid", ""))],
                      [Action(type="open_item", label=f"Open {name}", payload={"item_id": iid})],
                      {"item": {"said": a.item, "id": iid, "name": name}})


def t_supply_flow(m, a: FlowArgs) -> ToolOutput:
    sid, resolved = None, {}
    if a.supplier:
        sid, sname = resolve_supplier(m, a.supplier)
        resolved = {"supplier": {"said": a.supplier, "id": sid, "name": sname}}
    ins = g.supplier_flow(m.stock(), m.graph(), m.require(), sid, True)
    return ToolOutput(ins, [V.mermaid(ins.title, ins.meta.get("mermaid", ""))], resolved=resolved)


def t_money_flow(m, a: PeriodArgs) -> ToolOutput:
    k = kpis_mod.compute_kpis(m.require(), a.period_days)
    ins = insight(kind="money_flow", title=f"Where the money went - last {a.period_days} days",
                  as_of=k.as_of, headline=k.headline, formula=k.formula,
                  meta={"mermaid": mm.money_flow(k.headline)})
    return ToolOutput(ins, [V.mermaid(ins.title, ins.meta["mermaid"])])


def t_restock_draft(m, a: DraftArgs) -> ToolOutput:
    sid, resolved = None, {}
    if a.supplier:
        sid, sname = resolve_supplier(m, a.supplier)
        resolved = {"supplier": {"said": a.supplier, "id": sid, "name": sname}}
    d = m.restock.create_draft(a.budget_rs, sid, a.language)
    rows = [{"po_id": p.po_id, "supplier": p.supplier, "items": len(p.lines),
             "total_paise": p.total_paise, "total_incl_gst_paise": p.total_incl_gst_paise,
             "expected_delivery": p.expected_delivery, "message": p.message} for p in d.orders]
    ins = insight(kind="restock_draft", title="Draft purchase orders (not sent)",
                  as_of=d.as_of, rows=rows, formula=d.formula,
                  headline={"draft_id": d.draft_id, "orders": len(d.orders),
                            "total_incl_gst_paise": d.total_incl_gst_paise,
                            "deferred_items": len(d.deferred)},
                  assumptions=["Drafts only - the owner sends the message and confirms the order"],
                  confidence="ok" if d.orders else "no_data")
    lines = [{"supplier": p.supplier, "name": l.name, "qty": l.qty, "tier": l.tier,
              "value_incl_gst_rs": l.value_incl_gst_paise / 100} for p in d.orders for l in p.lines]
    vis = [V.table("Purchase orders", [("supplier", "Supplier"), ("name", "Item"), ("qty", "Qty"),
                                       ("tier", "Status"), ("value_incl_gst_rs", "₹ incl. GST")],
                   lines, limit=30)]
    acts = [Action(type="open_whatsapp", label=f"Send to {p.supplier} on WhatsApp",
                   payload={"url": p.whatsapp_url, "po_id": p.po_id}) for p in d.orders[:3]]
    if d.orders:
        acts.append(Action(type="download_csv", label="Download order list",
                           payload={"path": f"/api/restock/export.csv?draft_id={d.draft_id}"}))
    return ToolOutput(ins, vis, acts, resolved, ["Morning brief dikhao"])


class BriefArgs(BaseModel):
    language: Literal["en", "hinglish", "hi"] = Field("hinglish", description="Match the owner's language")


def t_brief(m, a: BriefArgs) -> ToolOutput:
    from app.actions.brief import morning_brief

    b = morning_brief(m, a.language)
    ins = insight(kind="morning_brief", title="Morning brief", as_of=b["as_of"],
                  headline={"text": b["text"]}, rows=b["lines"],
                  formula="Built from the reorder plan, supplier priority, yesterday's sales, dead "
                          "stock, expense spikes and festival adjustments")
    return ToolOutput(ins, actions=[Action(type="open_whatsapp", label="Share brief on WhatsApp",
                                           payload={"url": b["whatsapp_url"]})])


def t_find(m, a: FindArgs) -> ToolOutput:
    ds = m.require()
    items = find_mentions(a.query, list(zip(ds.items["item_id"], ds.items["name"])), _weights(m)) \
        or match_names(a.query, list(zip(ds.items["item_id"], ds.items["name"])), limit=5)
    cats = sorted(ds.items["category"].unique())
    rows = ([{"type": "item", **h} for h in items] +
            [{"type": "category", **h} for h in match_names(a.query, [(c, c) for c in cats], limit=2)] +
            [{"type": "supplier", **h} for h in
             match_names(a.query, list(zip(ds.suppliers["supplier_id"], ds.suppliers["name"])), limit=2)])
    ins = insight(kind="find", title=f"Matches for '{a.query}'", rows=rows,
                  formula="Hindi/Hinglish-aware name matching",
                  confidence="ok" if rows else "no_data")
    return ToolOutput(ins)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[[DataManager, Any], ToolOutput]


TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("get_kpis", "Business summary: sales, GST collected, margin after GST, bills, average "
             "bill, payment split, net profit estimate, stock value, change vs previous period.",
             PeriodArgs, t_kpis),
    ToolSpec("stock_status", "Stock left, days of stock left and status (CRITICAL/SOON/OK) for one "
             "item or all items.", StockArgs, t_stock),
    ToolSpec("reorder_plan", "What to order now: items, quantities (pack-rounded), cost and cash "
             "needed, grouped by supplier. Optional budget in rupees or a single supplier.",
             ReorderArgs, t_reorder),
    ToolSpec("dead_stock", "Items with stock but no sale for N days, money stuck in them and the "
             "maximum clearance discount that still breaks even.", DeadArgs, t_dead),
    ToolSpec("slow_movers", "Items selling far slower than others in their category.", NoArgs, t_slow),
    ToolSpec("margin_report", "Profit margin after backing GST out of MRP, by item, category or "
             "supplier; flags high-revenue-low-profit items. Can focus on one item.",
             MarginArgs, t_margin),
    ToolSpec("payment_mix", "How customers pay (UPI, cash, credit/udhaar, card) and the UPI trend.",
             PaymentArgs, t_payments),
    ToolSpec("sales_trend", "Units and sales over time for the shop, one item or one category, with "
             "festival windows marked.", TrendArgs, t_trend),
    ToolSpec("top_items", "Best items ranked by revenue, margin, units or margin %.", TopArgs, t_top),
    ToolSpec("expense_report", "Expenses (rent, salary, electricity...) in the last 30 days vs "
             "earlier, with spikes flagged.", NoArgs, t_expenses),
    ToolSpec("supplier_priority", "Which supplier to call first today, ranked by critical items and "
             "sales that would be lost.", NoArgs, t_supplier_priority),
    ToolSpec("supplier_risk", "What runs out and how much sale is lost if a supplier delivers N days "
             "late, with in-stock items that can cover.", SupplierRiskArgs, t_supplier_risk),
    ToolSpec("supplier_dependency", "Share of sales that depends on each supplier.", PeriodArgs,
             t_dependency),
    ToolSpec("bought_together", "Items customers buy together with a given item (lift, "
             "significance).", ItemArgs, t_bought_together),
    ToolSpec("substitutes", "Similar items (same category, similar price) that can replace an "
             "item, with their stock.", ItemArgs, t_substitutes),
    ToolSpec("supply_path", "Diagram of one item's supplier, stock and items bought with it.",
             ItemOnlyArgs, t_supply_path),
    ToolSpec("supply_flow", "Diagram of suppliers and the items at risk (or one supplier's items).",
             FlowArgs, t_supply_flow),
    ToolSpec("money_flow", "Diagram of where the money went: sales -> GST, cost, margin, overheads, "
             "net.", PeriodArgs, t_money_flow),
    ToolSpec("restock_draft", "Prepare purchase-order drafts per supplier with a ready-to-send "
             "WhatsApp message (owner sends it). Use when the owner asks to make/write/send an "
             "order or message to a supplier.", DraftArgs, t_restock_draft),
    ToolSpec("morning_brief", "Only when the owner asks for the daily brief / morning report / "
             "today's summary: 5 lines covering orders, yesterday's sales, dead stock, expense "
             "spikes, festival adjustments. For 'what should I order' use reorder_plan instead.",
             BriefArgs, t_brief),
    ToolSpec("find_item", "Look up which items, categories or suppliers match some words (use when "
             "a name is unclear).", FindArgs, t_find),
]}


# ------------------------------------------------------------------ execution
def run_tool(m: DataManager, name: str, raw_args: dict | None) -> ToolOutput:
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"Unknown tool '{name}'.", list(TOOLS))
    try:
        args = spec.args(**{k: v for k, v in (raw_args or {}).items() if v is not None})
    except ValidationError as exc:
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ToolError(f"Invalid arguments for {name}: {msg}") from exc
    out = spec.fn(m, args)
    out.insight.meta.setdefault("args", args.model_dump(exclude_none=True))
    return out


def json_schema(model: type[BaseModel]) -> dict:
    """Plain JSON schema (types, enums, descriptions) accepted by LLM function-calling APIs."""
    props, required = {}, []
    for name, f in model.model_fields.items():
        ann = f.annotation
        optional = get_origin(ann) is not None and type(None) in get_args(ann)
        if optional:
            ann = next(a for a in get_args(ann) if a is not type(None))
        if get_origin(ann) is Literal:
            p = {"type": "string", "enum": list(get_args(ann))}
        elif ann is int:
            p = {"type": "integer"}
        elif ann is float:
            p = {"type": "number"}
        elif ann is bool:
            p = {"type": "boolean"}
        else:
            p = {"type": "string"}
        desc = f.description or ""
        if f.default is not None and not f.is_required():
            desc = (desc + f" (default {f.default})").strip()
        if desc:
            p["description"] = desc
        props[name] = p
        if f.is_required():
            required.append(name)
    schema = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def llm_view(out: ToolOutput, max_rows: int = 12) -> dict:
    """Compact, rupee-only view of a tool result for the LLM (no *_paise duplicates)."""
    def clean(v, depth=0):
        if isinstance(v, dict):
            return {k: clean(x, depth + 1) for k, x in v.items()
                    if not k.endswith("_paise") and k not in ("inputs", "windows", "context")}
        if isinstance(v, list):
            return [clean(x, depth + 1) for x in v[: (max_rows if depth <= 1 else 8)]]
        return v
    ins = out.insight
    rows = ins.meta.get("llm_rows", ins.rows)
    view = {
        "title": ins.title, "as_of": ins.as_of, "confidence": ins.confidence,
        "headline": clean(ins.headline), "rows": clean(rows[-20:] if "llm_rows" in ins.meta else rows),
        "rows_total": len(rows), "formula": ins.formula,
    }
    if ins.meta.get("args"):
        view["args"] = ins.meta["args"]
    if "llm_rows" in ins.meta:
        view["rows_note"] = "weekly totals (week start date); the chart shows daily data"
    if out.resolved:
        view["resolved_names"] = out.resolved
    if ins.assumptions:
        view["notes"] = ins.assumptions
    if "by_supplier" in ins.meta:
        view["by_supplier"] = clean(ins.meta["by_supplier"])
    return view


# CRM + metrics tools register themselves into TOOLS (kept in their own module)
from app.agent import crm_tools as _crm_tools  # noqa: E402,F401
