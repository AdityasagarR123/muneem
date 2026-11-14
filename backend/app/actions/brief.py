"""Morning brief: the 5 things the owner should know today, as WhatsApp-ready text.
Deterministic (no LLM): every number comes from the analytics."""

from __future__ import annotations

from urllib.parse import quote

from app.analytics import kpis as kpis_mod
from app.analytics import sales as sales_mod
from app.analytics import stock as stock_mod
from app.analytics.common import change_pct
from app.core.money import fmt_inr
from app.graph import insights as g

T = {
    "title": {"en": "Muneem morning brief", "hinglish": "Muneem ki subah ki report",
              "hi": "मुनीम की सुबह की रिपोर्ट"},
    "order": {"en": "Order {n} items today (~{cash} incl. GST). Call {sup} first - {c} critical.",
              "hinglish": "Aaj {n} items order karne hain (~{cash} GST sahit). Pehle {sup} ko call karo - {c} critical.",
              "hi": "आज {n} आइटम ऑर्डर करने हैं (~{cash} GST सहित)। पहले {sup} को कॉल करें - {c} क्रिटिकल।"},
    "no_order": {"en": "No urgent orders today.", "hinglish": "Aaj koi urgent order nahi.",
                 "hi": "आज कोई ज़रूरी ऑर्डर नहीं।"},
    "sales": {"en": "Yesterday's sales {rev} from {bills} bills ({chg} vs 7-day average).",
              "hinglish": "Kal ki bikri {rev}, {bills} bills ({chg} pichhle 7 din ke average se).",
              "hi": "कल की बिक्री {rev}, {bills} बिल ({chg} पिछले 7 दिन के औसत से)।"},
    "dead": {"en": "{money} stuck in {n} items with no sale in 30 days.",
             "hinglish": "{money} ka maal {n} items mein phansa hai (30 din se nahi bika).",
             "hi": "{money} का माल {n} आइटम में फँसा है (30 दिन से नहीं बिका)।"},
    "spike": {"en": "{cat} spending up {pct} ({cur} vs {avg} usual).",
              "hinglish": "{cat} ka kharcha {pct} badha ({cur}, aam taur par {avg}).",
              "hi": "{cat} का खर्च {pct} बढ़ा ({cur}, आमतौर पर {avg})।"},
    "fest": {"en": "Festival demand is over for {n} items - smaller orders save {money}.",
             "hinglish": "{n} items ki tyohaar wali demand khatam - chhota order rakh ke {money} bache.",
             "hi": "{n} आइटम की त्योहार वाली मांग खत्म - छोटा ऑर्डर रखकर {money} बचे।"},
}


def _pct(v) -> str:
    if v is None:
        return "-"
    return f"{'+' if v >= 0 else ''}{v:g}%"


def morning_brief(m, lang: str = "hinglish") -> dict:
    lang = lang if lang in ("en", "hinglish", "hi") else "hinglish"
    ds = m.require()
    st = m.stock()
    lines: list[dict] = []

    plan = stock_mod.reorder_plan(st, ds)
    pri = g.supplier_priority(st, m.graph(), ds)
    if plan.rows and pri.rows:
        top = pri.rows[0]
        lines.append({"kind": "order", "action": {"type": "restock_draft", "payload": {}},
                      "text": T["order"][lang].format(
                          n=plan.headline["items"], cash=fmt_inr(plan.headline["cash_needed_paise"]),
                          sup=top["supplier"], c=top["critical"])})
    else:
        lines.append({"kind": "order", "text": T["no_order"][lang]})

    day = kpis_mod.compute_kpis(ds, 1).headline
    week = kpis_mod.compute_kpis(ds, 7).headline
    avg = week["revenue_paise"] / 7 if week["revenue_paise"] else 0
    lines.append({"kind": "sales", "text": T["sales"][lang].format(
        rev=fmt_inr(day["revenue_paise"]), bills=day["bills"],
        chg=_pct(change_pct(day["revenue_paise"], avg)))})

    dead = stock_mod.dead_stock(ds, m.settings.dead_stock_days)
    if dead.rows:
        lines.append({"kind": "dead_stock", "text": T["dead"][lang].format(
            money=fmt_inr(dead.headline["money_trapped_paise"]), n=dead.headline["items"])})

    exp = sales_mod.expense_report(ds)
    for r in [r for r in exp.rows if r.get("spike")][:1]:
        lines.append({"kind": "expense", "text": T["spike"][lang].format(
            cat=r["category"].title(), pct=_pct(r["change_pct"]),
            cur=fmt_inr(r["current_paise"]), avg=fmt_inr(r["prior_mean_paise"]))})

    fest = st[st["festive_baseline_adjusted"] & (st["naive_order_qty"].fillna(0) > st["order_qty"])]
    if len(fest):
        saved = int(((fest["naive_order_qty"] - fest["order_qty"]) * fest["unit_cost_paise"]).sum())
        lines.append({"kind": "festival", "text": T["fest"][lang].format(
            n=len(fest), money=fmt_inr(saved))})

    text = "\n".join([f"{T['title'][lang]} ({ds.as_of:%d %b})"] +
                     [f"{i}. {l['text']}" for i, l in enumerate(lines, 1)])
    return {"as_of": ds.as_of.isoformat(), "language": lang, "lines": lines, "text": text,
            "whatsapp_url": f"https://wa.me/?text={quote(text)}",
            "note": "Computed from your data; no AI involved."}
