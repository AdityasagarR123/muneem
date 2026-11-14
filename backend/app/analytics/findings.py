"""Insight engine: detectors turn analytics into ranked findings.

Every finding answers: what happened · why it matters (₹ impact) · what to do next (one action),
in English and Hinglish, with the evidence tool that reproduces it. Detectors are rules on top
of the tested analytics - no LLM involved.
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.analytics import customers as cust_mod
from app.analytics import margin as margin_mod
from app.analytics import metrics as metrics_mod
from app.analytics import sales as sales_mod
from app.analytics import stock as stock_mod
from app.analytics.common import change_pct
from app.analytics.explain import explain_change
from app.core.money import fmt_inr

Severity = Literal["high", "medium", "low"]
SEV_WEIGHT = {"high": 3, "medium": 2, "low": 1}


class Finding(BaseModel):
    id: str
    kind: str
    category: Literal["risk", "opportunity", "info"]
    severity: Severity
    what: str
    why: str
    what_hi: str
    why_hi: str
    impact_paise: int | None = None
    impact_label: str = ""
    confidence: Literal["high", "medium", "low"] = "high"
    action: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)  # {tool, args} - re-runs the analysis
    detected_on: str = ""
    status: str = "new"
    urgent: bool = False  # needs action today (ranked first)
    score: float = 0.0


def _sev(impact_paise: int | None, high: int = 500_000, medium: int = 100_000) -> Severity:
    if impact_paise is None:
        return "low"
    return "high" if impact_paise >= high else "medium" if impact_paise >= medium else "low"


def _names(rows, key="name", k=3) -> str:
    return ", ".join(str(r.get(key)) for r in rows[:k])


def _ask(label: str, question: str) -> dict:
    return {"type": "ask", "label": label, "payload": {"question": question}}


# ------------------------------------------------------------------ detectors
def d_stockout(ctx) -> list[Finding]:
    st = ctx["stock"]
    crit = st[st["tier"] == "CRITICAL"]
    if crit.empty:
        return []
    lost = int(crit["lost_revenue_paise"].sum())
    rows = crit.to_dict("records")
    detail = "; ".join(f"{r['name']} {r['days_left']:g} days left (delivery {r['lead_time_days']} days)"
                       for r in rows[:3])
    detail_hi = "; ".join(f"{r['name']} sirf {r['days_left']:g} din (delivery {r['lead_time_days']} din)"
                          for r in rows[:3])
    return [Finding(
        id="stockout", kind="stockout", category="risk", severity="high",
        what=f"{len(rows)} item(s) will run out before a new delivery can arrive",
        why=f"{detail}. Even if you order today, about {fmt_inr(lost)} of sales will be missed.",
        what_hi=f"{len(rows)} item delivery se pehle khatam ho jayenge",
        why_hi=f"{detail_hi}. Aaj order karne par bhi lagbhag {fmt_inr(lost)} ki bikri chhootegi.",
        impact_paise=lost, impact_label="sales at risk", urgent=True,
        action={"type": "restock_draft", "label": "Make restock list", "payload": {}},
        evidence={"tool": "reorder_plan", "args": {}})]


def d_churn(ctx) -> list[Finding]:
    stats = ctx["stats"]
    if stats.empty:
        return []
    risk = stats[stats["at_risk"]].sort_values("monthly_value_paise", ascending=False)
    if risk.empty:
        return []
    value = int(risk["monthly_value_paise"].sum())
    names = ", ".join((r["name"] or r["customer_id"]) for r in risk.head(3).to_dict("records"))
    return [Finding(
        id="churn", kind="churn", category="risk", severity=_sev(value),
        what=f"{len(risk)} regular customers seem to have stopped buying",
        why=f"They usually came every few days but haven't for 2.5× their normal gap ({names}...). "
            f"Together they spent about {fmt_inr(value)} a month.",
        what_hi=f"{len(risk)} regular customers ne aana band kar diya lagta hai",
        why_hi=f"Ye aam taur par kuch din mein aate the, ab apne normal gap ke 2.5 guna se zyada ho "
               f"gaya ({names}...). Ye milkar har mahine lagbhag {fmt_inr(value)} kharidte the.",
        impact_paise=value, impact_label="monthly spend at risk",
        action={"type": "campaign_draft", "label": "Draft win-back messages", "payload": {"goal": "win_back"}},
        evidence={"tool": "at_risk_customers", "args": {}})]


def d_udhaar(ctx) -> list[Finding]:
    led = cust_mod.ledger(ctx["ds"])
    over = led[led["overdue"]].sort_values("outstanding_paise", ascending=False)
    if over.empty:
        return []
    amt = int(over["outstanding_paise"].sum())
    oldest = int(over["days_overdue"].max())
    return [Finding(
        id="udhaar", kind="udhaar_overdue", category="risk", severity=_sev(amt, 1_000_000, 200_000),
        what=f"{len(over)} khata customers owe {fmt_inr(amt)} for more than 30 days",
        why=f"The oldest unpaid credit is {oldest} days old. Money lent and not collected is "
            f"working capital you can't use for stock.",
        what_hi=f"{len(over)} khata customers ka {fmt_inr(amt)} 30 din se zyada baaki hai",
        why_hi=f"Sabse purana udhaar {oldest} din ka hai. Yeh paisa wapas aaye to maal kharidne mein "
               f"kaam aayega.",
        impact_paise=amt, impact_label="overdue udhaar",
        action={"type": "campaign_draft", "label": "Draft polite reminders",
                "payload": {"goal": "udhaar_reminder"}},
        evidence={"tool": "udhaar_report", "args": {}})]


def d_dead_stock(ctx) -> list[Finding]:
    ins = stock_mod.dead_stock(ctx["ds"], ctx["dead_days"])
    if not ins.rows:
        return []
    h, rows = ins.headline, ins.rows
    money = h["money_trapped_paise"]
    disc = min(r["max_discount_pct"] for r in rows[:3])
    return [Finding(
        id="dead_stock", kind="dead_stock", category="risk", severity=_sev(money),
        what=f"{fmt_inr(money)} is stuck in {h['items']} items that haven't sold in {h['days']} days",
        why=f"{_names(rows)}. A discount of up to {disc}% on these still breaks even.",
        what_hi=f"{fmt_inr(money)} ka maal {h['items']} items mein {h['days']} din se pada hai",
        why_hi=f"{_names(rows)}. In par {disc}% tak discount dene par bhi nuksaan nahi hoga.",
        impact_paise=money, impact_label="money stuck",
        action=_ask("Plan a clearance", "Dead stock dikhao"),
        evidence={"tool": "dead_stock", "args": {"days": ctx["dead_days"]}})]


def d_low_margin(ctx) -> list[Finding]:
    g = margin_mod.item_margins(ctx["ds"], 30)
    flagged = g[g["flag"] == "high_revenue_low_profit"]
    if flagged.empty:
        return []
    med = float(g.loc[g["revenue_paise"] > 0, "margin_pct"].median())
    gap = int(((med - flagged["margin_pct"]) / 100 * flagged["taxable_paise"]).clip(lower=0).sum())
    r = flagged.iloc[0]
    return [Finding(
        id="low_margin", kind="low_margin", category="opportunity", severity=_sev(gap),
        what=f"{len(flagged)} best-selling item(s) earn very little after GST",
        why=f"{r['name']} sold {fmt_inr(r['revenue_paise'])} in 30 days at only {r['margin_pct']:.1f}% "
            f"margin (shop typical {med:.1f}%). At the typical margin these items would earn "
            f"{fmt_inr(gap)} more a month - check the purchase price or MRP.",
        what_hi=f"{len(flagged)} zyada bikne wale item(s) par munafa bahut kam hai",
        why_hi=f"{r['name']} 30 din mein {fmt_inr(r['revenue_paise'])} bika, margin sirf "
               f"{r['margin_pct']:.1f}% (dukaan ka aam {med:.1f}%). Aam margin par {fmt_inr(gap)} "
               f"mahina zyada milte - kharid daam ya MRP dekhiye.",
        impact_paise=gap, impact_label="monthly margin gap", confidence="medium",
        action=_ask("See margins", "Which items sell a lot but earn very little?"),
        evidence={"tool": "margin_report", "args": {}})]


def d_expense(ctx) -> list[Finding]:
    ins = sales_mod.expense_report(ctx["ds"])
    out = []
    for r in [r for r in ins.rows if r.get("spike")][:2]:
        extra = int(r["current_paise"] - r["prior_mean_paise"])
        out.append(Finding(
            id=f"expense:{r['category']}", kind="expense_spike", category="risk",
            severity=_sev(extra, 500_000, 150_000),
            what=f"{r['category'].title()} spending jumped {r['change_pct']:+g}% in the last 30 days",
            why=f"{fmt_inr(r['current_paise'])} vs about {fmt_inr(r['prior_mean_paise'])} usually - "
                f"{fmt_inr(extra)} extra.",
            what_hi=f"{r['category'].title()} ka kharcha {r['change_pct']:+g}% badha",
            why_hi=f"{fmt_inr(r['current_paise'])} hua, aam taur par {fmt_inr(r['prior_mean_paise'])} - "
                   f"{fmt_inr(extra)} zyada.",
            impact_paise=extra, impact_label="extra spend",
            action=_ask("See expenses", "Kharcha dikhao"),
            evidence={"tool": "expense_report", "args": {}}))
    return out


def d_sales_change(ctx) -> list[Finding]:
    ds = ctx["ds"]
    period = "this_week" if ds.as_of.weekday() >= 2 else "last_7_days"
    ins = explain_change(ds, ctx["cal"], period, "previous_period", ctx["risk_ids"])
    h = ins.headline
    if ins.confidence != "ok" or h.get("change_pct") is None or abs(h["change_pct"]) < 10:
        return []
    causes = [c for c in ins.rows if c["kind"] != "remainder"]
    expected_kinds = {"calendar", "festival"}
    main = [c for c in causes if c["driver"] in h["main_reasons"]]
    expected = main and all(c["kind"] in expected_kinds for c in main)
    down = h["change_paise"] < 0
    reason = " and ".join(c["driver"] if c["kind"] == "festival" else c["driver"][0].lower() + c["driver"][1:]
                          for c in main) or "no single clear cause"
    remainder = next(c for c in ins.rows if c["kind"] == "remainder")["effect_paise"]
    sev: Severity = "low" if expected else ("high" if down else "medium")
    return [Finding(
        id=f"sales_change:{period}", kind="sales_change",
        category="info" if expected else ("risk" if down else "opportunity"), severity=sev,
        what=f"Sales {h['period_label']} are {'down' if down else 'up'} {abs(h['change_pct']):g}% "
             f"vs {h['compare_label']}",
        why=(f"{fmt_inr(h['now_paise'])} vs {fmt_inr(h['before_paise'])}. Mostly explained by {reason}"
             + ("; nothing unusual." if expected else ".")
             + f" Unexplained part: {fmt_inr(remainder)}."),
        what_hi=f"Bikri {h['period_label']} {abs(h['change_pct']):g}% {'kam' if down else 'zyada'} hai",
        why_hi=(f"{fmt_inr(h['now_paise'])} bani, pehle {fmt_inr(h['before_paise'])}. Zyadatar wajah: "
                f"{reason}" + ("; chinta ki baat nahi." if expected else ".")
                + f" Bina wajah wala hissa: {fmt_inr(remainder)}."),
        impact_paise=abs(int(h["change_paise"])), impact_label="change in sales",
        confidence="medium",
        action=_ask("Why did sales change?", "Is hafte bikri kyun kam hui?" if down else
                    "Is hafte bikri kyun badhi?"),
        evidence={"tool": "explain_change", "args": {"period": period, "compare": "previous_period"}})]


def d_day_anomaly(ctx) -> list[Finding]:
    ds, cal = ctx["ds"], ctx["cal"]
    s = ds.sales_x.groupby("date")["line_total_paise"].sum()
    last = ds.as_of_ts
    if last not in s.index:
        return []
    same = [last - pd.Timedelta(weeks=k) for k in range(1, 9)]
    base = s.reindex(same).dropna()
    base = base[[not any(f.contains(d.date()) for f in cal.festivals) for d in base.index]]
    if len(base) < 4:
        return []
    med = float(base.median())
    mad = float((base - med).abs().median()) or max(med * 0.05, 1.0)
    z = (float(s[last]) - med) / (1.4826 * mad)
    if abs(z) < 3:
        return []
    up = z > 0
    diff = int(abs(float(s[last]) - med))
    day = last.strftime("%a %d %b")
    return [Finding(
        id=f"day_anomaly:{last.date()}", kind="day_anomaly", category="opportunity" if up else "risk",
        severity="medium", confidence="medium",
        what=f"Sales on {day} were unusually {'high' if up else 'low'}",
        why=f"{fmt_inr(s[last])} vs a normal {fmt_inr(med)} for that weekday ({fmt_inr(diff)} "
            f"{'more' if up else 'less'}).",
        what_hi=f"{day} ko bikri asamanya roop se {'zyada' if up else 'kam'} rahi",
        why_hi=f"{fmt_inr(s[last])} hui, us din aam taur par {fmt_inr(med)} hoti hai.",
        impact_paise=diff, impact_label="vs normal day",
        action=_ask("Explain", "Aaj ki bikri kyun alag hai?"),
        evidence={"tool": "query_metric", "args": {"metric": "revenue", "group_by": "day",
                                                   "period": "last_30_days"}})]


def d_new_customers(ctx) -> list[Finding]:
    ds = ctx["ds"]
    h = metrics_mod.query_metric(ds, "new_customers", None, "last_30_days").headline
    now, before = h.get("value"), h.get("compare_value")
    ch = change_pct(now, before)
    if not now or ch is None or ch < 25:
        return []
    return [Finding(
        id="new_customers", kind="new_customers", category="opportunity", severity="medium",
        what=f"{now} new customers in the last 30 days ({ch:+g}%)",
        why=f"Up from {before}. New customers who come back a second time usually become regulars - "
            f"a welcome-back message now is cheap.",
        what_hi=f"Pichhle 30 din mein {now} naye customers aaye ({ch:+g}%)",
        why_hi=f"Pehle {before} the. Jo dobara aate hain wo aksar regular ban jaate hain - abhi ek "
               f"welcome message bhejiye.",
        impact_paise=None, impact_label="",
        action={"type": "campaign_draft", "label": "Draft welcome messages", "payload": {"goal": "welcome_new"}},
        evidence={"tool": "query_metric", "args": {"metric": "new_customers", "period": "last_30_days"}})]


def d_cross_sell(ctx) -> list[Finding]:
    from app.graph import insights as g

    ds, graph = ctx["ds"], ctx["graph"]
    top = ds.sales_window(30).groupby("item_id")["line_total_paise"].sum().nlargest(6).index
    best = None
    for iid in top:
        ins = g.bought_together(graph, ds, iid, 3)
        for r in ins.rows:
            if r.get("associated") and r["lift"] >= 2 and (best is None or r["lift"] > best[1]["lift"]):
                best = (ins.headline["name"], r, iid)
    if best is None:
        return []
    a, r, a_id = best
    return [Finding(
        id=f"cross_sell:{a}", kind="cross_sell", category="opportunity", severity="low",
        what=f"{a} and {r['name']} are bought together {r['lift']:g}× more than chance",
        why=f"{r['bills_together']} bills had both ({r['confidence_pct']:g}% of {a} bills). Keep them "
            f"next to each other and never let one run out while the other is in stock.",
        what_hi=f"{a} aur {r['name']} saath mein {r['lift']:g} guna zyada bikte hain",
        why_hi=f"{r['bills_together']} bills mein dono the. Dono ko paas rakhiye aur ek khatam na hone "
               f"dijiye.",
        impact_paise=None,
        action={"type": "campaign_draft", "label": f"Suggest {r['name']} to {a} buyers",
                "payload": {"goal": "cross_sell", "item_a": a_id, "item_b": r["item_id"]}},
        evidence={"tool": "bought_together", "args": {"item": a}})]


def d_festival(ctx) -> list[Finding]:
    st = ctx["stock"]
    fest = st[st["festive_baseline_adjusted"] & (st["naive_order_qty"].fillna(0) > st["order_qty"])]
    if fest.empty:
        return []
    saved = int(((fest["naive_order_qty"] - fest["order_qty"]) * fest["unit_cost_paise"]).sum())
    return [Finding(
        id="festival_adjusted", kind="festival_adjusted", category="info", severity="low",
        what=f"Festival demand is over for {len(fest)} items - order normal quantities",
        why=f"A plain average would still order festival volumes; Muneem's plan avoids "
            f"{fmt_inr(saved)} of over-stocking ({_names(fest.to_dict('records'))}).",
        what_hi=f"{len(fest)} items ki tyohaar wali demand khatam - normal order kijiye",
        why_hi=f"Seedha average tyohaar jitna order karwata; Muneem ki list {fmt_inr(saved)} ka "
               f"faltu maal bachati hai.",
        impact_paise=saved, impact_label="over-stocking avoided",
        action={"type": "restock_draft", "label": "Make restock list", "payload": {}},
        evidence={"tool": "reorder_plan", "args": {}})]


DETECTORS = [d_stockout, d_churn, d_udhaar, d_dead_stock, d_low_margin, d_expense, d_sales_change,
             d_day_anomaly, d_new_customers, d_cross_sell, d_festival]


def run_detectors(ctx: dict) -> list[Finding]:
    out: list[Finding] = []
    for det in DETECTORS:
        try:
            out.extend(det(ctx))
        except Exception as exc:  # one broken detector must not hide the others
            import logging

            logging.getLogger("muneem.findings").exception("detector %s failed: %s", det.__name__, exc)
    as_of = ctx["ds"].as_of.isoformat()
    for f in out:
        f.detected_on = as_of
        f.id = f"{f.id}@{as_of}"
        f.score = round(SEV_WEIGHT[f.severity] * 10 + (math.log10(1 + f.impact_paise / 100)
                                                         if f.impact_paise else 0)
                        - (5 if f.category == "info" else 0) + (8 if f.urgent else 0), 3)
    out.sort(key=lambda f: -f.score)
    return out
