"""Rules-only mode: keyword router (English + Hinglish) -> tool -> template answer.

Works with no LLM at all, so the demo survives an API outage. Every number in a
template comes straight from the tool result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.agent.tools import ToolOutput
from app.core.lexicon import find_mentions
from app.core.money import fmt_inr
from app.store.manager import DataManager


@dataclass
class Route:
    intent: str
    tool: str
    args: dict = field(default_factory=dict)


# order matters: first intent whose pattern matches (and whose needs are met) wins
INTENTS: list[tuple[str, list[str]]] = [
    ("draft", [r"\bmessage\b", r"\bmsg\b", r"whatsapp", r"\bdraft\b", r"\blikh", r"\bbhej",
               r"sandesh", r"purchase order"]),
    ("brief", [r"\bbrief\b", r"\bsubah\b", r"morning", r"aaj ka haal", r"today.?s summary"]),
    ("web", [r"\bmarket (price|rate|bhav|trend)", r"\bmandi\b", r"\bbhav\b", r"\bnews\b",
             r"\bkhabar", r"competitor", r"online (price|rate)", r"\binternet\b", r"\bon the web\b",
             r"search (online|the web|google)", r"gst (rate|rule|change|notification|council)",
             r"wholesale (price|rate)", r"\bprice trend", r"sarkari|government (rule|scheme)"]),
    ("customer_profile", [r"."]),  # only when a customer's full name is mentioned
    ("campaign_result", [r"campaign.*(result|kaam|work|asar|kaisa)", r"(result|asar).*campaign",
                         r"did .*(campaign|message).* work"]),
    ("reminder", [r"reminder", r"yaad dila", r"\bremind", r"udhaa?r.*(message|msg|bhej|likh)",
                  r"(message|msg|bhej|likh).*udhaa?r"]),
    ("campaign", [r"campaign", r"win.?back", r"wapas bula",
                  r"(customers?|grahak).*(message|msg|bhej|likh|whatsapp)",
                  r"(message|msg|bhej|likh|whatsapp).*(customers?|grahak)"]),
    ("explain", [r"(why|kyu|kyo|wajah|reason).*(sale|bikri|revenue|dhand|kamai|income|business)",
                 r"(sale|sales|bikri|revenue|dhanda).*(why|kyu|kyo|drop|gir|kam kyu|badh.*kyu)"]),
    ("focus", [r"focus", r"priorit", r"\bkya karu", r"aaj kya kar", r"what should i (do|focus)",
               r"most important", r"sabse zaroori", r"\binsights?\b", r"any problems?", r"koi problem"]),
    ("at_risk", [r"churn", r"stopped (buying|coming)", r"not (coming|buying)", r"aana band",
                 r"aane band", r"nahi? aa rah", r"nahin aa rah", r"lost customer", r"at.?risk",
                 r"chhod", r"wapas nahi", r"likely to stop"]),
    ("udhaar", [r"udhaa?r", r"khata", r"\bowe", r"baaki", r"\bdues?\b", r"paisa lena",
                r"outstanding", r"receivable"]),
    ("top_customers", [r"(valuable|best|top|loyal|important|biggest|bade|acch?e) (customers?|grahak)",
                       r"most valuable", r"customers? .*spend.* most"]),
    ("segments", [r"segment", r"customer (group|type)", r"\brfm\b", r"champion"]),
    ("cohort", [r"cohort", r"retention", r"wapas aa"]),
    ("funnel", [r"funnel", r"journey"]),
    ("compare", [r"compare", r"last month", r"pichh?le mahine", r"tulna",
                 r"(this|is) (month|mahine).*\b(vs|versus)\b"]),
    ("supplier_risk", [r"\blate\b", r"\bdelay", r"\bder (se|ho)", r"nahi aay", r"nahin aay",
                       r"not (come|arrive)", r"\bdeliver"]),
    ("supplier_priority", [r"call first", r"pehle call", r"kis(ko| supplier)", r"which supplier",
                           r"\bcall\b", r"\bphone\b"]),
    ("bought_together", [r"together", r"\bsaath\b", r"\bcombo", r"\bbundle", r"basket"]),
    ("substitutes", [r"substitut", r"alternative", r"\bbadle\b", r"\bjagah\b", r"replace",
                     r"\binstead\b"]),
    ("money_flow", [r"paisa kaha", r"paise kaha", r"money (go|went|flow)", r"where.*money"]),
    ("supply_flow", [r"\bflow\b", r"diagram", r"supply chain", r"\bpath\b", r"\bnaksha"]),
    ("reorder", [r"\border", r"restock", r"\bkharid", r"\bmangw", r"\bmanga", r"purchase",
                 r"\bbuy\b", r"\blana\b", r"\blaana\b"]),
    ("dead_stock", [r"\bdead\b", r"nahi bik", r"nahin bik", r"not sold", r"\bstuck\b", r"\bphans",
                    r"pada hai", r"haven.?t sold", r"no sale", r"unsold"]),
    ("slow", [r"\bslow", r"dheere", r"dhire"]),
    ("expenses", [r"expense", r"kharch", r"bijli", r"electric", r"\brent\b", r"salary",
                  r"kiraya", r"\bbill (badh|jump|increase)"]),
    ("payments", [r"\bupi\b", r"\bcash\b", r"payment", r"\bcredit\b", r"\bcard\b"]),
    ("dependency", [r"depend", r"concentrat"]),
    ("top", [r"\btop\b", r"\bbest\b", r"sabse (zyada|jyada|acha|accha)", r"best.?sell"]),
    ("margin", [r"margin", r"profit", r"munafa", r"kamai", r"\bearn", r"sell a lot"]),
    ("trend", [r"trend", r"graph", r"chart", r"over time", r"kitna bik", r"kitni bik",
               r"sales dikha", r"bikri dikha", r"\bbikri\b"]),
    ("stock", [r"\bstock\b", r"kitna bacha", r"kitni bachi", r"khatam", r"run out", r"days left",
               r"\bbacha\b", r"\bbachi\b"]),
    ("kpis", [r"summary", r"overview", r"kaisa chal", r"business", r"\bsales\b", r"revenue",
              r"hisaab", r"\bgst\b", r"kamaya", r"\bdhanda\b"]),
]

INTENT_TOOL = {
    "web": "web_search", "draft": "restock_draft", "brief": "morning_brief", "customer_profile": "customer_profile",
    "explain": "explain_change", "focus": "top_insights", "campaign_result": "campaign_results",
    "reminder": "draft_campaign", "campaign": "draft_campaign",
    "at_risk": "at_risk_customers", "udhaar": "udhaar_report", "top_customers": "query_metric",
    "segments": "customer_segments", "cohort": "cohort_retention", "funnel": "lifecycle_funnel",
    "compare": "query_metric", "supplier_risk": "supplier_risk", "supplier_priority": "supplier_priority",
    "bought_together": "bought_together", "substitutes": "substitutes",
    "money_flow": "money_flow", "supply_flow": "supply_flow", "reorder": "reorder_plan",
    "dead_stock": "dead_stock", "slow": "slow_movers", "expenses": "expense_report",
    "payments": "payment_mix", "dependency": "supplier_dependency", "top": "top_items",
    "margin": "margin_report", "trend": "sales_trend", "stock": "stock_status", "kpis": "get_kpis",
}

_BUDGET = re.compile(r"(?:₹|rs\.?|inr|budget|bajat)\s*([\d,]+(?:\.\d+)?)\s*(k|hazar|hazaar|thousand)?"
                     r"|([\d,]+(?:\.\d+)?)\s*(k|hazar|hazaar|thousand)?\s*(?:rs|rupees?|rupaye|₹)",
                     re.I)
_DAYS = re.compile(r"(\d{1,3})\s*(?:din|days?|day)\b", re.I)


def _budget(text: str) -> float | None:
    m = _BUDGET.search(text)
    if not m:
        return None
    num, mult = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    v = float(num.replace(",", ""))
    return v * 1000 if mult else v


def entities(m: DataManager, text: str) -> dict:
    from app.agent.tools import _weights

    ds = m.require()
    items = find_mentions(text, list(zip(ds.items["item_id"], ds.items["name"])), _weights(m))
    sups = find_mentions(text, list(zip(ds.suppliers["supplier_id"], ds.suppliers["name"])))
    cats = sorted(ds.items["category"].unique())
    catm = find_mentions(text, [(c, c) for c in cats], min_score=0.5)
    days = _DAYS.search(text)
    custs = find_mentions(text, list(zip(ds.customers["customer_id"], ds.customers["name"])),
                          min_score=0.99, limit=5)
    customer = None
    if custs:  # same name twice -> pass the name, the tool asks which one
        customer = custs[0]["id"] if len({c["name"] for c in custs}) == len(custs) else custs[0]["name"]
    return {"item": items[0] if items else None, "supplier": sups[0] if sups else None,
            "customer": customer,
            "category": catm[0]["id"] if catm else None,
            "days": int(days.group(1)) if days else None, "budget_rs": _budget(text)}


def route(m: DataManager, text: str) -> Route | None:
    low = (text or "").lower()
    e = entities(m, text)
    for intent, pats in INTENTS:
        if not any(re.search(p, low) for p in pats):
            continue
        tool = INTENT_TOOL[intent]
        args: dict = {}
        from app.agent.language import detect_language

        if intent == "web":
            if not m.research.search_ready:
                continue
            news = re.search(r"news|khabar|latest|aaj|today|this week", low)
            args = {"query": text.strip()[:300], "topic": "news" if news else "general"}
            if news:
                args["time_range"] = "week"
        elif intent == "customer_profile":
            if not e["customer"]:
                continue
            args = {"customer": e["customer"]}
        elif intent == "reminder":
            args = {"goal": "udhaar_reminder", "language": detect_language(text)}
        elif intent == "campaign":
            goal = ("reward_champions" if re.search(r"champion|best|loyal|acch?e|bade", low) else
                    "welcome_new" if re.search(r"\bnew\b|naye|welcome", low) else "win_back")
            args = {"goal": goal, "language": detect_language(text)}
        elif intent == "campaign_result":
            args = {}
        elif intent == "explain":
            if re.search(r"this week|is hafte|is week", low):
                args = {"period": "this_week", "compare": "previous_period"}
            elif re.search(r"this month|is mahine", low):
                args = {"period": "this_month", "compare": "same_period_last_month"}
            elif re.search(r"\btoday\b|\baaj\b", low):
                args = {"period": "last_1_days", "compare": "previous_period"}
            else:
                args = {"period": "last_7_days", "compare": "previous_period"}
        elif intent == "focus":
            args = {"limit": 5, "language": detect_language(text)}
        elif intent == "top_customers":
            args = {"metric": "revenue", "group_by": "customer", "period": "last_90_days",
                    "compare": "none", "limit": 10}
        elif intent == "compare":
            metric = ("gross_margin" if re.search(r"margin|profit|munafa", low) else
                      "aov" if re.search(r"average bill|avg bill|aov", low) else
                      "customers" if re.search(r"customer|grahak", low) else
                      "orders" if re.search(r"\bbills?\b|orders?", low) else "revenue")
            args = {"metric": metric, "period": "this_month", "compare": "same_period_last_month"}
        elif intent == "brief":
            args = {"language": detect_language(text)}
        elif intent == "draft" and re.search(r"customer|grahak|udhaa?r|remind|champion|best|loyal|"
                                             r"aana band|win.?back", low):
            continue  # a message to customers, not a supplier order
        elif intent == "draft":
            args = {"language": detect_language(text)}
            if e["supplier"]:
                args["supplier"] = e["supplier"]["id"]
            if e["budget_rs"] is not None:
                args["budget_rs"] = e["budget_rs"]
        elif intent == "supplier_risk":
            if not e["supplier"]:
                continue
            args = {"supplier": e["supplier"]["id"], "delay_days": e["days"] or 2}
        elif intent in ("bought_together", "substitutes"):
            if not e["item"]:
                continue
            args = {"item": e["item"]["id"]}
        elif intent == "supply_flow":
            if e["item"]:
                tool, args = "supply_path", {"item": e["item"]["id"]}
            elif e["supplier"]:
                args = {"supplier": e["supplier"]["id"]}
        elif intent == "reorder":
            if e["budget_rs"] is not None:
                args["budget_rs"] = e["budget_rs"]
            if e["supplier"]:
                args["supplier"] = e["supplier"]["id"]
        elif intent == "dead_stock" and e["days"]:
            args = {"days": max(7, e["days"])}
        elif intent == "top":
            by = "margin" if re.search(r"margin|profit|munafa|kamai", low) else \
                "units" if re.search(r"unit|quantity|zyada bik|jyada bik|bika|biki", low) else "revenue"
            args = {"by": by, "n": 5}
        elif intent == "margin":
            group = "category" if re.search(r"categor", low) else \
                "supplier" if re.search(r"supplier", low) else "item"
            args = {"group_by": group}
            if e["item"] and group == "item":
                args["item"] = e["item"]["id"]
        elif intent == "trend":
            if e["item"]:
                args["item"] = e["item"]["id"]
            elif e["category"]:
                args["category"] = e["category"]
            args["grain"] = "week" if re.search(r"week|hafte|hafta", low) else "day"
        elif intent == "stock" and e["item"]:
            args = {"item": e["item"]["id"]}
        elif intent in ("kpis", "payments", "dependency", "money_flow") and e["days"]:
            args = {"period_days": max(7, min(365, e["days"]))}
        return Route(intent, tool, args)
    return None


# ------------------------------------------------------------------ templates
def num(v, places: int = 1) -> str:
    if v is None:
        return "-"
    q = Decimal(str(v)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    s = f"{q:f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def rs(v_rs) -> str:
    return fmt_inr(round((v_rs or 0) * 100))


def _names(rows, key="name", k=3):
    return ", ".join(str(r.get(key)) for r in rows[:k])


def answer(intent: str, out: ToolOutput, lang: str) -> str:
    hi = lang in ("hinglish", "hi")
    ins, h, rows = out.insight, out.insight.headline, out.insight.rows
    if intent == "web":
        if not rows:
            return ("Web par iske baare mein kuch pakka nahi mila." if hi else
                    "I found nothing reliable on the web for that.")
        lines = [f"- {r['title']} ({r.get('domain') or ''}, "
                 f"{(r.get('published_date') or 'date unknown')[:16]})" for r in rows[:3]]
        head = ("Web se mili jaankari (Muneem ne verify nahi ki, aapki dukaan ka data nahi):" if hi
                else "From the web (outside information, not verified by Muneem and not your shop's "
                     "data):")
        return head + "\n" + "\n".join(lines)
    if intent == "draft":
        if not rows:
            return ("Abhi kisi supplier ko order bhejne ki zaroorat nahi." if hi else
                    "Nothing to order right now, so no message was drafted.")
        first = rows[0]
        head = (f"{len(rows)} supplier ke liye order message taiyaar hai (kul "
                f"{rs(h['total_incl_gst_rs'])} GST sahit). Aap check karke WhatsApp se bhejiye - "
                f"Muneem khud nahi bhejta." if hi else
                f"Drafted orders for {len(rows)} supplier(s) (total {rs(h['total_incl_gst_rs'])} "
                f"incl. GST). Review and send from WhatsApp - Muneem never sends anything itself.")
        return f"{head}\n\n{first['message']}"
    if intent == "brief":
        return h["text"]
    if intent == "explain":
        if ins.confidence != "ok":
            return "Is period ki tulna ke liye pehle ka data nahi hai." if hi else \
                "There isn't enough earlier data to compare this period."
        main = [r for r in rows if r["driver"] in h["main_reasons"]]
        rest = next(r for r in rows if r["kind"] == "remainder")
        word = ("kam" if h["direction"] == "down" else "zyada") if hi else h["direction"]
        reasons = "; ".join(f"{r['driver']} {rs(r['effect_rs'])}" for r in main) or \
            ("koi ek saaf wajah nahi" if hi else "no single clear cause")
        bridge = {b["part"]: b for b in ins.meta.get("bridge", [])}
        bills, avg = bridge.get("Number of bills", {}), bridge.get("Average bill", {})
        if hi:
            s = (f"Bikri {h['period_label']} {num(abs(h['change_pct']))}% {word} hai ({rs(h['now_rs'])}, "
                 f"pehle {rs(h['before_rs'])}). Mukhya wajah: {reasons}. Baaki {rs(rest['effect_rs'])} "
                 f"aam utaar-chadhaav.")
            if bills:
                s += f" Bills {num(bills['change_pct'])}%, average bill {num(avg.get('change_pct'))}%."
        else:
            s = (f"Sales {h['period_label']} are {word} {num(abs(h['change_pct']))}% ({rs(h['now_rs'])} vs "
                 f"{rs(h['before_rs'])}). Main reasons: {reasons}. The remaining {rs(rest['effect_rs'])} "
                 f"is normal variation.")
            if bills:
                s += f" Bills {num(bills['change_pct'])}%, average bill {num(avg.get('change_pct'))}%."
        return s
    if intent in ("campaign", "reminder"):
        if not h.get("members"):
            return "Is group mein abhi koi customer nahi hai." if hi else "No customers match this group right now."
        ex = rows[0]["message"] if rows else ""
        if h.get("udhaar_total_rs"):
            s = (f"{h['targets']} customers ko yaad dilane ke message taiyaar hain (kul "
                 f"{rs(h['udhaar_total_rs'])} baaki). Check karke WhatsApp se bhejiye." if hi else
                 f"Reminders drafted for {h['targets']} customers ({rs(h['udhaar_total_rs'])} pending). "
                 f"Review and send from WhatsApp.")
        else:
            s = (f"{h['targets']} customers ke liye message taiyaar hain; {h['holdout']} ko jaan-boojh kar "
                 f"nahi bhejenge taaki pata chale message ka asar hua ya nahi." if hi else
                 f"Messages drafted for {h['targets']} customers; {h['holdout']} are kept as a hold-out "
                 f"(no message) so we can tell whether it worked.")
        return f"{s}\n\n{ex}"
    if intent == "campaign_result":
        if h.get("status") in ("not_sent", "waiting"):
            return h.get("message", "")
        g = {r["group"]: r for r in rows}
        t, ho = g.get("Messaged", {}), g.get("Hold-out (no message)", {})
        s = (f"'{h.get('name')}': message paane wale {num(t.get('rate_pct'))}% customers wapas aaye, bina "
             f"message wale {num(ho.get('rate_pct'))}% ({num(h.get('lift_points'))} points ka farq)." if hi else
             f"'{h.get('name')}': {num(t.get('rate_pct'))}% of messaged customers came back vs "
             f"{num(ho.get('rate_pct'))}% of the hold-out ({num(h.get('lift_points'))} points).")
        if h.get("incremental_value_rs") is not None:
            s += (f" Lagbhag {rs(h['incremental_value_rs'])} ki extra bikri." if hi else
                  f" About {rs(h['incremental_value_rs'])} of extra sales.")
        verdict = h.get("verdict")
        if verdict == "worked":
            s += " Iska asar saaf hai." if hi else f" The effect is clear (p = {h.get('p_value')})."
        elif verdict == "no clear effect":
            s += " Abhi pakka nahi kaha ja sakta." if hi else f" Not conclusive yet (p = {h.get('p_value')})."
        return s
    if intent == "focus":
        if not rows:
            return "Abhi koi zaroori baat nahi." if hi else "Nothing needs your attention right now."
        lst = " ".join(f"{i}) {r['what']}." for i, r in enumerate(rows[:3], 1))
        return (f"Aaj in par dhyan dijiye: {lst}" if hi else f"Focus on these today: {lst}")
    if intent == "at_risk":
        if not rows:
            return ("Koi regular customer aana band nahi hua." if hi else
                    "No regular customer seems to have stopped buying.")
        top = ", ".join(f"{r['name'] or r['customer_id']} ({r['recency_days']} din)" if hi else
                        f"{r['name'] or r['customer_id']} ({r['recency_days']} days)" for r in rows[:3])
        return (f"{h['customers']} regular customers ne aana band kar diya lagta hai - har mahine "
                f"lagbhag {rs(h['monthly_value_at_risk_rs'])} ki kharidari khatre mein. Sabse pehle: {top}."
                if hi else
                f"{h['customers']} regular customers seem to have stopped buying - about "
                f"{rs(h['monthly_value_at_risk_rs'])} of monthly spend at risk. Start with: {top}.")
    if intent == "udhaar":
        over = [r for r in rows if r.get("overdue")]
        names = ", ".join(f"{r['name'] or r['customer_id']} {rs(r['outstanding_rs'])}" for r in over[:3])
        s = (f"Kul udhaar baaki: {rs(h['outstanding_rs'])} ({h['customers_with_balance']} customers). "
             f"{h['overdue_customers']} ka {rs(h['overdue_rs'])} 30 din se purana hai" if hi else
             f"Customers owe you {rs(h['outstanding_rs'])} in total ({h['customers_with_balance']} "
             f"customers). {h['overdue_customers']} are overdue for {rs(h['overdue_rs'])}")
        return s + (f": {names}." if names else ".")
    if intent == "top_customers":
        lst = "; ".join(f"{i}) {r['group']} {rs(r['value_rs'])}" for i, r in enumerate(rows[:3], 1))
        return (f"Pichhle 90 din ke sabse bade customers: {lst}." if hi else
                f"Top customers by spend in the last 90 days: {lst}.")
    if intent == "segments":
        seg = {r["segment"]: r for r in rows}
        ch, ar = seg.get("Champions", {}), seg.get("At risk", {})
        return (f"{ch.get('customers', 0)} Champions customers aapki {num(ch.get('revenue_share_pct'))}% "
                f"bikri laate hain. {ar.get('customers', 0)} customers 'At risk' hain - unhe abhi "
                f"message karna chahiye." if hi else
                f"{ch.get('customers', 0)} Champions bring {num(ch.get('revenue_share_pct'))}% of sales. "
                f"{ar.get('customers', 0)} customers are 'At risk' and worth a message now.")
    if intent == "cohort":
        return ("Har mahine ke naye customers mein se kitne wapas aaye - table dekhiye." if hi else
                "Here is how many customers from each starting month came back later.")
    if intent == "funnel":
        once, back = rows[0], rows[1] if len(rows) > 1 else None
        return (f"{once['customers']} customers mein se {back['customers']} ({num(back['of_all_pct'])}%) "
                f"wapas aaye." if hi and back else
                f"Of {once['customers']} customers, {back['customers']} ({num(back['of_all_pct'])}%) "
                f"came back." if back else "Not enough customer data.")
    if intent == "compare":
        unit = "rs" if "value_rs" in h else ""
        v = rs(h.get("value_rs")) if unit else num(h.get("value"), 0)
        pv = rs(h.get("compare_value_rs")) if unit else num(h.get("compare_value"), 0)
        ch = h.get("change_pct")
        if ch is None:
            return f"{h['label']} - {h['period_label']}: {v}."
        return (f"{h['label']} {h['period_label']}: {v}, pichhle mahine ke inhi dino mein {pv} "
                f"({'+' if ch >= 0 else ''}{num(ch)}%)." if hi else
                f"{h['label']} {h['period_label']}: {v} vs {pv} {h['compare_label']} "
                f"({'+' if ch >= 0 else ''}{num(ch)}%).")
    if intent == "customer_profile":
        name = h.get("name") or h.get("customer_id")
        s = (f"{name}: {h.get('bills', 0)} bills, kul {rs(h.get('revenue_rs'))}, segment "
             f"{h.get('segment', '-')}, aakhri bill {h.get('recency_days', '-')} din pehle." if hi else
             f"{name}: {h.get('bills', 0)} bills, {rs(h.get('revenue_rs'))} in total, segment "
             f"{h.get('segment', '-')}, last bill {h.get('recency_days', '-')} days ago.")
        u = h.get("udhaar") or {}
        if u.get("outstanding_rs"):
            s += (f" Udhaar baaki {rs(u['outstanding_rs'])}." if hi else
                  f" Owes {rs(u['outstanding_rs'])} on khata.")
        return s
    if intent == "reorder":
        if not rows:
            return ("Abhi kuch order karne ki zaroorat nahi - sab items mein kaafi stock hai."
                    if hi else "Nothing needs ordering right now - every item has enough stock.")
        t = rows[0]
        L = t["inputs"]["L"]
        s = (f"Aaj {len(rows)} items order karne hain - kul {rs(h['cash_needed_rs'])} (GST ke saath). "
             f"Sabse zaroori: {t['name']} × {t['order_qty']} (sirf {num(t['days_left'])} din ka stock "
             f"bacha, supplier ko {L} din lagte hain)." if hi else
             f"Order {len(rows)} items today - cash needed {rs(h['cash_needed_rs'])} (incl. GST). "
             f"Most urgent: {t['name']} × {t['order_qty']} (only {num(t['days_left'])} days of stock; "
             f"supplier needs {L} days).")
        if h.get("deferred_items"):
            s += (f" Budget mein {h['deferred_items']} items nahi aaye." if hi else
                  f" {h['deferred_items']} items did not fit the budget.")
        return s
    if intent == "supplier_priority":
        if not rows:
            return "Aaj kisi supplier ko urgent call ki zaroorat nahi." if hi else \
                "No supplier needs an urgent call today."
        t = rows[0]
        names = _names(t["items"])
        return (f"Sabse pehle {t['supplier']} ko call karo - {t['critical']} items critical aur "
                f"{t['soon']} jaldi khatam honge ({names}). Order lagbhag {rs(t['cash_needed_rs'])}."
                if hi else
                f"Call {t['supplier']} first - {t['critical']} critical and {t['soon']} soon items "
                f"({names}). Order worth about {rs(t['cash_needed_rs'])}.")
    if intent == "supplier_risk":
        hit = [r for r in rows if r["runs_out_before_delivery"]]
        if not hit:
            return (f"{h['supplier']} {h['delay_days']} din late ho to bhi koi item khatam nahi hoga."
                    if hi else f"Even if {h['supplier']} is {h['delay_days']} days late, nothing runs out.")
        return (f"Agar {h['supplier']} {h['delay_days']} din late hua, to {len(hit)} items delivery se "
                f"pehle khatam ho jayenge ({_names(hit)}) - lagbhag {rs(h['lost_revenue_rs'])} ki bikri "
                f"ka nuksaan." if hi else
                f"If {h['supplier']} is {h['delay_days']} days late, {len(hit)} items run out before "
                f"delivery ({_names(hit)}) - about {rs(h['lost_revenue_rs'])} of sales lost.")
    if intent == "dead_stock":
        if not rows:
            return "Koi dead stock nahi hai." if hi else "No dead stock."
        top = ", ".join(f"{r['name']} {rs(r['money_trapped_rs'])}" for r in rows[:3])
        return (f"{rs(h['money_trapped_rs'])} ka maal {h['items']} items mein phansa hai - "
                f"{h['days']} din se koi bikri nahi: {top}." if hi else
                f"{rs(h['money_trapped_rs'])} is stuck in {h['items']} items with no sale in "
                f"{h['days']} days: {top}.")
    if intent == "slow":
        if not rows:
            return "Koi khaas slow item nahi." if hi else "No unusually slow items."
        return (f"{h['items']} items apni category se bahut dheere bik rahe hain: {_names(rows)}." if hi
                else f"{h['items']} items sell far slower than their category: {_names(rows)}.")
    if intent == "margin":
        if ins.meta.get("args", {}).get("item") and rows:
            r = rows[0]
            return (f"{r['name']} ka margin GST hata ke {num(r['margin_pct'])}% hai - "
                    f"{rs(r['margin_rs'])} munafa, {rs(r['taxable_rs'])} ki bikri par (pichhle 30 din)."
                    if hi else
                    f"{r['name']}: margin after GST is {num(r['margin_pct'])}% - {rs(r['margin_rs'])} "
                    f"on {rs(r['taxable_rs'])} taxable sales (last 30 days).")
        flagged = [r for r in rows if r.get("flag") == "high_revenue_low_profit"]
        s = (f"GST hata ke kul margin {num(h['margin_pct'])}% hai ({rs(h['margin_rs'])})." if hi else
             f"Overall margin after GST: {num(h['margin_pct'])}% ({rs(h['margin_rs'])}).")
        if flagged:
            s += (f" Zyada bikte hain par kam kamate hain: {_names(flagged)}." if hi else
                  f" High sales but low profit: {_names(flagged)}.")
        return s
    if intent == "payments":
        share = {r["payment_mode"]: r["share_pct"] for r in rows}
        t = h.get("upi_trend") or {}
        s = (f"Bikri ka UPI {num(share.get('UPI'))}%, cash {num(share.get('cash'))}%, udhaar "
             f"{num(share.get('credit'))}% hai." if hi else
             f"Sales by value: UPI {num(share.get('UPI'))}%, cash {num(share.get('cash'))}%, credit "
             f"{num(share.get('credit'))}%.")
        if t:
            s += (f" UPI {num(t['first_4_weeks_pct'])}% se badhkar {num(t['last_4_weeks_pct'])}% ho gaya."
                  if hi else f" UPI share rose from {num(t['first_4_weeks_pct'])}% to "
                             f"{num(t['last_4_weeks_pct'])}%.")
        return s
    if intent == "trend":
        ch = h.get("second_half_vs_first_half_pct")
        fest = ins.meta.get("festivals") or []
        s = (f"{h['label']}: {num(h['units'], 0)} units bike (graph dekho)" if hi else
             f"{h['label']}: {num(h['units'], 0)} units sold (see chart)")
        if ch is not None:
            s += (f", pichhle hisse se {num(ch)}% {'zyada' if ch >= 0 else 'kam'}." if hi else
                  f", {num(abs(ch))}% {'up' if ch >= 0 else 'down'} vs the earlier half.")
        if fest:
            s += (f" {fest[0]['name']} ka asar dikh raha hai." if hi else
                  f" The {fest[0]['name']} spike is marked.")
        return s
    if intent == "top":
        key = {"revenue": "revenue_rs", "margin": "margin_rs", "units": "units",
               "margin_pct": "margin_pct"}[h["by"]]
        fmt = (lambda v: rs(v)) if key.endswith("_rs") else (lambda v: num(v, 0))
        lst = "; ".join(f"{i}) {r['name']} {fmt(r[key])}" for i, r in enumerate(rows[:3], 1))
        return (f"Sabse aage ({h['by']}): {lst}." if hi else f"Top by {h['by']}: {lst}.")
    if intent == "expenses":
        sp = [r for r in rows if r["spike"]]
        if not sp:
            return "Kharche mein koi asamanya badhat nahi." if hi else "No unusual expense spike."
        r = sp[0]
        return (f"{r['category']} ka kharcha pichhle 30 din mein {rs(r['current_rs'])} hua - pehle "
                f"average {rs(r['prior_mean_rs'])} tha ({num(r['change_pct'])}% zyada)." if hi else
                f"{r['category'].title()} spending jumped to {rs(r['current_rs'])} in the last 30 days "
                f"(earlier average {rs(r['prior_mean_rs'])}, +{num(r['change_pct'])}%).")
    if intent == "dependency":
        return (f"{h['top_supplier']} se aapki {num(h['top_share_pct'])}% bikri judi hai "
                f"(concentration: {h['concentration']})." if hi else
                f"{h['top_supplier']} supplies items behind {num(h['top_share_pct'])}% of your sales "
                f"(concentration: {h['concentration']}).")
    if intent == "bought_together":
        assoc = [r for r in rows if r.get("associated")]
        if not assoc:
            return (f"{h['name']} ke saath koi khaas jodi nahi milti." if hi else
                    f"No strong pairing found for {h['name']}.")
        r = assoc[0]
        return (f"{h['name']} ke saath sabse zyada {r['name']} bikta hai - {r['bills_together']} bills "
                f"mein saath, chance se {num(r['lift'])}× zyada." if hi else
                f"{h['name']} is most often bought with {r['name']} - {r['bills_together']} bills "
                f"together, {num(r['lift'])}× more than chance.")
    if intent == "substitutes":
        if not rows:
            return f"{h.get('name', 'Is item')} ka koi milta-julta item nahi." if hi else \
                f"No close substitute for {h.get('name', 'this item')}."
        av = [r for r in rows if r["available"]]
        lst = ", ".join(f"{r['name']} ({num(r['price_diff_pct'])}%)" for r in (av or rows)[:3])
        return (f"{h['name']} ki jagah: {lst}." if hi else f"Instead of {h['name']}: {lst}.")
    if intent in ("supply_flow", "money_flow"):
        return "Diagram neeche hai." if hi else "Here is the diagram."
    if intent == "stock":
        args = ins.meta.get("args", {})
        if args.get("item") and rows:
            r = rows[0]
            if r["confidence"] == "insufficient_data":
                return (f"{r['name']}: sirf {r['history_days']} din ka data hai - forecast ke liye kam "
                        f"se kam 14 din chahiye." if hi else
                        f"{r['name']}: only {r['history_days']} days of sales history - need 14 to "
                        f"forecast.")
            s = (f"{r['name']}: {num(r['stock'], 0)} bache hain, lagbhag {num(r['days_left'])} din ka "
                 f"stock ({r['tier']})." if hi else
                 f"{r['name']}: {num(r['stock'], 0)} left, about {num(r['days_left'])} days of stock "
                 f"({r['tier']}).")
            if r["order_qty"]:
                s += (f" Order: {r['order_qty']}." if hi else f" Suggested order: {r['order_qty']}.")
            return s
        crit = [r for r in rows if r["tier"] == "CRITICAL"]
        return (f"{h['critical']} items critical, {h['soon']} jaldi khatam honge: {_names(crit)}." if hi
                else f"{h['critical']} items critical, {h['soon']} soon: {_names(crit)}.")
    if intent == "kpis":
        ch = h.get("revenue_change_pct")
        chs = f" ({'+' if (ch or 0) >= 0 else ''}{num(ch)}%)" if ch is not None else ""
        return (f"Pichhle {ins.meta.get('args', {}).get('period_days', 30)} din: bikri "
                f"{rs(h['revenue_rs'])}{chs}, GST hata ke margin {rs(h['gross_margin_rs'])} "
                f"({num(h['gross_margin_pct'])}%), {h['bills']} bills, average bill "
                f"{rs(h['avg_bill_rs'])}. Kharche ke baad lagbhag {rs(h['net_profit_estimate_rs'])}."
                if hi else
                f"Last {ins.meta.get('args', {}).get('period_days', 30)} days: sales "
                f"{rs(h['revenue_rs'])}{chs}, margin after GST {rs(h['gross_margin_rs'])} "
                f"({num(h['gross_margin_pct'])}%), {h['bills']} bills, average bill "
                f"{rs(h['avg_bill_rs'])}. Net after overheads about {rs(h['net_profit_estimate_rs'])}.")
    return ins.title


HELP = {
    "en": "I can answer questions about your shop: what to order, which supplier to call first, "
          "dead stock, margins after GST, sales trends, payments, expenses, items bought together "
          "and substitutes. Try: \"What should I order tomorrow?\"",
    "hinglish": "Main aapki dukaan ke baare mein bata sakta hoon: kya order karna hai, kis supplier ko "
                "pehle call karna hai, dead stock, GST ke baad margin, bikri ka trend, UPI/cash, kharche. "
                "Poochho: \"Kal kya order karna chahiye?\"",
}
