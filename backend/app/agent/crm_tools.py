"""CRM + metrics tools for the agent (registered into tools.TOOLS)."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.agent import visuals as V
from app.agent.tools import (
    TOOLS, ToolError, ToolOutput, ToolSpec, resolve_category, resolve_item, resolve_supplier,
)
from app.analytics import customers as cust_mod
from app.analytics import metrics as metrics_mod
from app.analytics.common import insight
from app.core.lexicon import find_mentions, match_names
from app.ingest.validate import phone_key

MetricName = Literal[tuple(metrics_mod.METRICS)]  # type: ignore[valid-type]
Dimension = Literal[metrics_mod.DIMENSIONS]  # type: ignore[valid-type]
Segment = Literal[tuple(cust_mod.SEGMENTS)]  # type: ignore[valid-type]


def resolve_customer(m, ref: str) -> tuple[str, str]:
    ds = m.require()
    ref = (ref or "").strip()
    c = ds.customers
    if ref in set(c["customer_id"]):
        return ref, c.set_index("customer_id").at[ref, "name"] or ref
    key = phone_key(ref)
    if len(re.sub(r"\D", "", ref)) >= 5:
        hit = c[c["phone"].map(phone_key).str.endswith(key[-5:])] if key else c.iloc[0:0]
        if len(hit) == 1:
            r = hit.iloc[0]
            return r["customer_id"], r["name"] or r["customer_id"]
    cands = list(zip(c["customer_id"], c["name"]))
    hits = [h for h in find_mentions(ref, cands, min_score=0.99, limit=10)] or \
        match_names(ref, cands, limit=5, min_score=0.8)
    if not hits:
        raise ToolError(f"No customer matches '{ref}'.",
                        [h["name"] for h in match_names(ref, cands, limit=3, min_score=0.3)])
    top = [h for h in hits if h["score"] >= hits[0]["score"] - 1e-9]
    if len(top) > 1:
        area = c.set_index("customer_id")["area"]
        raise ToolError(f"{len(top)} customers are called '{hits[0]['name']}'. Which one?",
                        [f"{h['name']} ({h['id']}, {area.get(h['id'], '')})" for h in top[:5]])
    return hits[0]["id"], hits[0]["name"]


# ------------------------------------------------------------------ args
class MetricArgs(BaseModel):
    metric: MetricName = Field("revenue", description="Which number: " + ", ".join(
        f"{k} ({v.label})" for k, v in metrics_mod.METRICS.items()))
    group_by: Optional[Dimension] = Field(None, description="Split the number by this dimension")
    period: str = Field("last_30_days", description="last_7_days, last_30_days, last_90_days, "
                        "last_N_days, this_week, last_week, this_month, last_month or all")
    compare: Literal["previous_period", "same_period_last_month", "none"] = Field(
        "previous_period", description="What to compare with; for 'this month vs last month' use "
                                       "period=this_month and compare=same_period_last_month")
    category: Optional[str] = Field(None, description="Only this category")
    item: Optional[str] = Field(None, description="Only this item (name as the owner said it)")
    supplier: Optional[str] = Field(None, description="Only items from this supplier")
    payment_mode: Optional[Literal["UPI", "cash", "credit", "card"]] = None
    customer_segment: Optional[Segment] = Field(None, description="Only customers in this segment")
    limit: int = Field(15, ge=1, le=50)


class NoArgs(BaseModel):
    pass


class RiskArgs(BaseModel):
    limit: int = Field(15, ge=1, le=50)


class CustomerArgs(BaseModel):
    customer: str = Field(..., description="Customer name, phone or id as the owner said it")


# ------------------------------------------------------------------ tools
def t_query_metric(m, a: MetricArgs) -> ToolOutput:
    filters, resolved = {}, {}
    if a.category:
        filters["category"] = resolve_category(m, a.category)
        resolved["category"] = {"said": a.category, "name": filters["category"]}
    if a.item:
        iid, name = resolve_item(m, a.item)
        filters["item_id"] = iid
        resolved["item"] = {"said": a.item, "id": iid, "name": name}
    if a.supplier:
        sid, sname = resolve_supplier(m, a.supplier)
        filters["supplier_id"] = sid
        resolved["supplier"] = {"said": a.supplier, "id": sid, "name": sname}
    if a.payment_mode:
        filters["payment_mode"] = a.payment_mode
    if a.customer_segment:
        filters["customer_segment"] = a.customer_segment
    try:
        ins = metrics_mod.query_metric(m.require(), a.metric, a.group_by, a.period, a.compare,
                                       filters, m.segments(), a.limit)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    h = ins.headline
    money = h["unit"] == "paise"
    vk, ck = ("value_rs", "compare_value_rs") if money else ("value", "compare_value")
    unit = "₹" if money else "%" if h["unit"] == "pct" else ""
    vis = []
    if a.group_by in metrics_mod.TIME_DIMS:
        vis.append(V.chart(ins.title, "line", "group", [(vk, h["label"])], ins.rows, unit))
    elif a.group_by:
        series = [(vk, h["period_label"])] + ([(ck, h.get("compare_label", "before"))]
                                               if ins.rows and ck in ins.rows[0] else [])
        vis.append(V.chart(ins.title, "bar", "group", series, ins.rows[:12], unit))
    else:
        vis.append(V.Visual(type="kpis", title=ins.title, spec={"cards": [
            {"label": f"{h['label']} - {h['period_label']}", "key": vk, "value": h.get(vk),
             "change_pct": h.get("change_pct"), "unit": unit},
            {"label": h.get("compare_label", "before").capitalize(), "key": ck,
             "value": h.get(ck), "change_pct": None, "unit": unit}]}))
    return ToolOutput(ins, vis, resolved=resolved,
                      suggestions=["Kaun se customers ne aana band kar diya?", "Udhaar kitna baaki hai?"])


def t_segments(m, a: NoArgs) -> ToolOutput:
    ins = cust_mod.segments_insight(m.require(), m.customer_stats())
    st = m.customer_stats()
    pts = [{"recency_days": int(r.recency_days), "monthly_value_rs": round(r.monthly_value_paise / 100, 2),
            "segment": r.segment, "name": r.name or r.customer_id} for r in st.head(400).itertuples()]
    scatter = V.Visual(type="chart", title="Customers: days since last bill vs monthly spend", spec={
        "kind": "scatter", "x": "recency_days", "unit": "₹", "color": "segment",
        "series": [{"key": "monthly_value_rs", "label": "Spend per month ₹"}], "data": pts})
    return ToolOutput(ins, [
        V.chart("Sales by customer segment (90 days)", "bar", "segment",
                [("revenue_90d_rs", "Sales ₹")], [r for r in ins.rows if r["customers"]], "₹"),
        scatter,
        V.table(ins.title, [("segment", "Segment"), ("customers", "Customers"),
                            ("revenue_share_pct", "Share of sales %"), ("description", "Meaning"),
                            ("suggested_action", "What to do")], ins.rows)],
        suggestions=["Kaun se customers ne aana band kar diya?"])


def t_at_risk(m, a: RiskArgs) -> ToolOutput:
    ins = cust_mod.at_risk_insight(m.require(), m.customer_stats(), a.limit)
    return ToolOutput(ins, [V.table(ins.title, [
        ("name", "Customer"), ("recency_days", "Days since last bill"),
        ("usual_gap_days", "Usually every (days)"), ("bills", "Bills"),
        ("monthly_value_rs", "Spend per month ₹"), ("phone", "Phone")], ins.rows)],
        suggestions=["Udhaar kitna baaki hai?", "Customer segments dikhao"])


def t_udhaar(m, a: NoArgs) -> ToolOutput:
    ins = cust_mod.udhaar_insight(m.require())
    owing = [r for r in ins.rows if r["outstanding_rs"] and r["outstanding_rs"] > 0]
    return ToolOutput(ins, [V.table(ins.title, [
        ("name", "Customer"), ("outstanding_rs", "Owes ₹"), ("days_overdue", "Oldest unpaid (days)"),
        ("overdue", "Overdue"), ("last_payment", "Last paid")], owing)],
        suggestions=["Kaun se customers ne aana band kar diya?"])


def t_cohorts(m, a: NoArgs) -> ToolOutput:
    ins = cust_mod.cohorts_insight(m.require())
    heat = V.Visual(type="chart", title=ins.title, spec={
        "kind": "cohort_heatmap", "row": "cohort", "col": "month_offset", "value": "retention_pct",
        "unit": "%", "data": [{k: r[k] for k in ("cohort", "month_offset", "retention_pct")}
                              for r in ins.rows]})
    return ToolOutput(ins, [heat])


def t_funnel(m, a: NoArgs) -> ToolOutput:
    ins = cust_mod.lifecycle_insight(m.require(), m.customer_stats())
    return ToolOutput(ins, [V.chart(ins.title, "funnel", "stage", [("customers", "Customers")], ins.rows)])


def t_profile(m, a: CustomerArgs) -> ToolOutput:
    cid, name = resolve_customer(m, a.customer)
    ins = cust_mod.profile_insight(m.require(), m.customer_stats(), cid)
    top = ins.headline.get("top_items", [])
    return ToolOutput(ins, [
        V.chart("Monthly spend", "bar", "month", [("revenue_rs", "₹")], ins.rows, "₹"),
        V.table("What they buy", [("name", "Item"), ("bills", "Bills"), ("qty", "Qty"),
                                  ("revenue_rs", "₹")], top)],
        resolved={"customer": {"said": a.customer, "id": cid, "name": name}})


for spec in [
    ToolSpec("query_metric", "Any business number for any period, optionally split by a dimension "
             "and compared with an earlier period: sales, margin, bills, average bill, customers, "
             "new/returning customers, repeat share, spend per customer... Use for 'compare this "
             "month with last month', 'sales by category', 'average bill by payment mode', 'who are "
             "my top customers' (group_by=customer).", MetricArgs, t_query_metric),
    ToolSpec("customer_segments", "Customers grouped by value and habit (RFM): Champions, Loyal, "
             "New, Promising, At risk, Hibernating - with share of sales and what to do.",
             NoArgs, t_segments),
    ToolSpec("at_risk_customers", "Regular customers who seem to have stopped buying (churn risk), "
             "with how long since their last bill vs their usual gap and monthly value at risk.",
             RiskArgs, t_at_risk),
    ToolSpec("udhaar_report", "Udhaar / khata: how much customers owe, who is overdue and for how "
             "long.", NoArgs, t_udhaar),
    ToolSpec("cohort_retention", "Monthly customer cohorts: what share came back in later months.",
             NoArgs, t_cohorts),
    ToolSpec("lifecycle_funnel", "Customer journey funnel: bought once -> came back -> regular -> "
             "regular and active.", NoArgs, t_funnel),
    ToolSpec("customer_profile", "Everything about one customer: spend, habits, segment, risk, "
             "favourite items, udhaar.", CustomerArgs, t_profile),
]:
    TOOLS[spec.name] = spec


# ------------------------------------------------------------------ insight engine tools
class FocusArgs(BaseModel):
    limit: int = Field(5, ge=1, le=10)
    category: Optional[Literal["risk", "opportunity", "info"]] = None
    language: Literal["en", "hinglish", "hi"] = Field("en", description="Match the owner's language")


class ExplainArgs(BaseModel):
    period: str = Field("last_7_days", description="this_week, last_7_days, this_month, last_30_days, "
                        "last_N_days ...")
    compare: Literal["previous_period", "same_period_last_month"] = Field(
        "previous_period", description="use same_period_last_month with period=this_month")


def t_top_insights(m, a: FocusArgs) -> ToolOutput:
    fs = [f for f in m.findings() if f.status != "dismissed"]
    if a.category:
        fs = [f for f in fs if f.category == a.category]
    fs = fs[:a.limit]
    hi = a.language != "en"
    rows = [{"id": f.id, "kind": f.kind, "category": f.category, "severity": f.severity,
             "urgent": f.urgent, "what": f.what_hi if hi else f.what, "why": f.why_hi if hi else f.why,
             "impact_paise": f.impact_paise, "impact_label": f.impact_label,
             "action": f.action.get("label", "")} for f in fs]
    ins = insight(kind="top_insights", title="What needs your attention", as_of=m.require().as_of.isoformat(),
                  headline={"findings": len(rows), "high": sum(f.severity == "high" for f in fs),
                            "urgent": sum(f.urgent for f in fs)},
                  rows=rows,
                  formula="each finding comes from a rule-based detector on your data; ranked by "
                          "urgency, severity and ₹ impact",
                  confidence="ok" if rows else "no_data")
    from app.agent.envelope import Action

    actions = [Action(type=f.action["type"], label=f.action["label"], payload=f.action.get("payload", {}))
               for f in fs if f.action][:4]
    return ToolOutput(ins, [V.table(ins.title, [("what", "What"), ("impact_rs", "₹ impact"),
                                                ("impact_label", ""), ("action", "Next step")], ins.rows)],
                      actions)


def t_explain(m, a: ExplainArgs) -> ToolOutput:
    from app.analytics.explain import explain_change

    st = m.customer_stats()
    risk = set(st.loc[st["at_risk"], "customer_id"]) if len(st) else set()
    try:
        ins = explain_change(m.require(), m.calendar, a.period, a.compare, risk)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    h = ins.headline
    wf = V.chart("What moved sales", "waterfall", "driver", [("effect_rs", "₹ effect")], ins.rows, "₹")
    if ins.confidence == "ok":
        wf.spec["start"] = {"label": h["compare_label"].capitalize(), "value": h["before_rs"]}
        wf.spec["end"] = {"label": h["period_label"].capitalize(), "value": h["now_rs"]}
    vis = [wf]
    if ins.meta.get("bridge"):
        vis.append(V.table("Bills × average bill", [("part", ""), ("change_pct", "Change %"),
                                                    ("effect_rs", "₹ effect")], ins.meta["bridge"]))
    if ins.meta.get("category_movers"):
        vis.append(V.table("Biggest category moves", [("name", "Category"), ("now_rs", "Now ₹"),
                                                      ("before_rs", "Before ₹"), ("change_pct", "Change %")],
                           ins.meta["category_movers"]))
    return ToolOutput(ins, vis, suggestions=["What should I focus on today?"])


for spec in [
    ToolSpec("top_insights", "The most important things to act on now (risks, opportunities), each with "
             "what happened, why it matters, ₹ impact and the next step. Use for 'what should I focus "
             "on', 'any problems?', 'kya karun aaj'.", FocusArgs, t_top_insights),
    ToolSpec("explain_change", "Why sales went up or down between two periods: splits the change into "
             "calendar (weekdays), festival, customers who stopped coming, stock-outs, price changes "
             "and the rest; also bills × average bill and the categories that moved most.",
             ExplainArgs, t_explain),
]:
    TOOLS[spec.name] = spec
