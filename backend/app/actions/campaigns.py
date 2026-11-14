"""Campaigns and reminders: segment -> personal WhatsApp messages -> owner sends -> measured
against a hold-out group that received nothing.

    draft  ->  sent  ->  (after N days) measured
      \\-> cancelled

Rules:
* Muneem drafts; the owner sends (click-to-send links / CSV for a broadcast tool).
* The hold-out is chosen deterministically (hash of campaign + customer), so it is stable
  and auditable. Without a hold-out a "result" would just be normal buying.
* Results are computed from bills/repayments recorded after the send date - never guessed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

import pandas as pd
from pydantic import BaseModel, Field

from app.actions.restock import whatsapp_url
from app.analytics import customers as cust_mod
from app.core.money import fmt_inr
from app.core.text import csv_safe

log = logging.getLogger("muneem.campaigns")

Goal = Literal["win_back", "reward_champions", "welcome_new", "cross_sell", "udhaar_reminder", "custom"]
Lang = Literal["en", "hinglish", "hi"]

GOALS = {
    "win_back": {"label": "Win back customers who stopped coming", "segment": {"type": "at_risk"},
                 "holdout_pct": 10, "measure": "purchase"},
    "reward_champions": {"label": "Thank your best customers", "segment": {"type": "segment", "name": "Champions"},
                         "holdout_pct": 10, "measure": "purchase"},
    "welcome_new": {"label": "Welcome new customers back", "segment": {"type": "new_customers", "days": 30},
                    "holdout_pct": 10, "measure": "purchase"},
    "cross_sell": {"label": "Suggest an item they don't buy yet",
                   "segment": {"type": "bought_x_not_y"}, "holdout_pct": 10, "measure": "item"},
    "udhaar_reminder": {"label": "Polite udhaar reminders", "segment": {"type": "udhaar_overdue"},
                        "holdout_pct": 0, "measure": "repayment"},
    "custom": {"label": "Custom message", "segment": {"type": "segment", "name": "Loyal"},
               "holdout_pct": 10, "measure": "purchase"},
}

TEMPLATES: dict[str, dict[str, str]] = {
    "win_back": {
        "en": "Hello {name}, we haven't seen you at {shop} for a while. We'd love to have you back - "
              "message us anytime and we'll keep your items ready.{offer}",
        "hinglish": "Namaste {name} ji, kaafi din se aap {shop} nahi aaye. Jab bhi zaroorat ho, ek message "
                    "kar dijiye - aapka saamaan taiyaar rakhenge.{offer}",
        "hi": "नमस्ते {name} जी, काफ़ी दिनों से आप {shop} नहीं आए। ज़रूरत हो तो एक मैसेज कर दीजिए - "
              "आपका सामान तैयार रखेंगे।{offer}",
    },
    "reward_champions": {
        "en": "Hello {name}, thank you for being one of {shop}'s most loyal customers!{offer}",
        "hinglish": "Namaste {name} ji, {shop} ke sabse bharosemand grahakon mein se ek hone ke liye "
                    "dhanyavaad!{offer}",
        "hi": "नमस्ते {name} जी, {shop} के सबसे भरोसेमंद ग्राहकों में से एक होने के लिए धन्यवाद!{offer}",
    },
    "welcome_new": {
        "en": "Hello {name}, thanks for shopping at {shop}! Save this number - message us your list "
              "and we'll keep it ready.{offer}",
        "hinglish": "Namaste {name} ji, {shop} se kharidari ke liye dhanyavaad! Yeh number save kar "
                    "lijiye - list bhejiye, saamaan taiyaar milega.{offer}",
        "hi": "नमस्ते {name} जी, {shop} से ख़रीदारी के लिए धन्यवाद! यह नंबर सेव कर लीजिए - सूची "
              "भेजिए, सामान तैयार मिलेगा।{offer}",
    },
    "cross_sell": {
        "en": "Hello {name}, you often buy {item_a} at {shop} - we also have fresh {item_b} in stock."
              "{offer}",
        "hinglish": "Namaste {name} ji, aap {shop} se aksar {item_a} lete hain - {item_b} bhi taaza "
                    "stock mein hai.{offer}",
        "hi": "नमस्ते {name} जी, आप {shop} से अक्सर {item_a} लेते हैं - {item_b} भी ताज़ा स्टॉक में है।"
              "{offer}",
    },
    "udhaar_reminder": {
        "en": "Hello {name}, a gentle reminder from {shop}: {amount} is pending on your account. "
              "Please clear it by UPI or cash when convenient. Thank you!",
        "hinglish": "Namaste {name} ji, {shop} ke khate mein {amount} baaki hai. Suvidha anusaar UPI ya "
                    "cash se jama kar dijiye. Dhanyavaad!",
        "hi": "नमस्ते {name} जी, {shop} के खाते में {amount} बाकी है। सुविधा अनुसार UPI या नकद से "
              "जमा कर दीजिए। धन्यवाद!",
    },
    "custom": {"en": "Hello {name}, {offer}", "hinglish": "Namaste {name} ji, {offer}",
               "hi": "नमस्ते {name} जी, {offer}"},
}


class Member(BaseModel):
    customer_id: str
    name: str = ""
    phone: str = ""
    group: Literal["target", "holdout"] = "target"
    amount_paise: int | None = None  # udhaar outstanding at draft time
    message: str = ""
    whatsapp_url: str = ""


class Campaign(BaseModel):
    campaign_id: str
    name: str
    goal: Goal
    segment: dict
    language: Lang = "hinglish"
    offer: str = ""
    template: str = ""
    holdout_pct: int = 10
    measure_after_days: int = 14
    status: Literal["draft", "sent", "cancelled"] = "draft"
    created_at: str
    sent_at: str | None = None
    members: list[Member] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)  # item_a / item_b for cross-sell
    history: list[dict] = Field(default_factory=list)
    note: str = "Send only to customers who agreed to receive WhatsApp messages from the shop."

    @property
    def targets(self) -> list[Member]:
        return [m for m in self.members if m.group == "target"]


def _h(campaign_id: str, customer_id: str) -> int:
    return int(hashlib.sha1(f"{campaign_id}:{customer_id}".encode()).hexdigest()[:8], 16)


def holdout_ids(campaign_id: str, customer_ids: list[str], pct: int) -> set[str]:
    """Exactly round(n × pct%) customers (at least 1 when n ≥ 5), picked by a stable hash so the
    split is reproducible and can't be cherry-picked."""
    n = len(customer_ids)
    if pct <= 0 or n == 0:
        return set()
    k = round(n * pct / 100)
    if n >= 5:
        k = max(k, 1)
    return set(sorted(customer_ids, key=lambda c: _h(campaign_id, c))[:k])


def render(template: str, shop: str, m: Member, params: dict, offer: str) -> str:
    first = (m.name or "").split(" ")[0] or ""
    return template.format(
        name=first or "", shop=shop, amount=fmt_inr(m.amount_paise or 0),
        item_a=params.get("item_a_name", ""), item_b=params.get("item_b_name", ""),
        offer=(" " + offer.strip()) if offer.strip() else "",
    ).replace(" ,", ",").replace("  ", " ").replace("Hello ,", "Hello,").replace("Namaste  ji", "Namaste ji")


# ------------------------------------------------------------------ segments
def resolve_segment(m, segment: dict, params: dict) -> pd.DataFrame:
    """Deterministic member list: customer_id, name, phone (+ amount_paise for udhaar)."""
    ds = m.require()
    st = m.customer_stats()
    kind = segment.get("type")
    cols = ["customer_id", "name", "phone"]
    if kind == "at_risk":
        out = st[st["at_risk"]][cols]
    elif kind == "segment":
        out = st[st["segment"] == segment.get("name")][cols]
    elif kind == "new_customers":
        days = int(segment.get("days", 30))
        new = (ds.as_of_ts - st["first_bill"]).dt.days < days
        out = st[new][cols]
    elif kind == "udhaar_overdue":
        led = cust_mod.ledger(ds)
        led = led[led["overdue"]]
        names = ds.customers.set_index("customer_id")
        out = pd.DataFrame({"customer_id": led["customer_id"],
                            "name": led["customer_id"].map(names["name"]).fillna(""),
                            "phone": led["customer_id"].map(names["phone"]).fillna(""),
                            "amount_paise": led["outstanding_paise"].astype("int64")})
    elif kind == "bought_x_not_y":
        a, b = params.get("item_a"), params.get("item_b")
        if not a or not b:
            raise ValueError("cross_sell needs item_a (what they buy) and item_b (what to suggest)")
        s = ds.sales_window(90)
        s = s[s["customer_id"] != ""]
        buyers_a = set(s.loc[s["item_id"] == a, "customer_id"])
        ever_b = set(ds.sales.loc[ds.sales["item_id"] == b, "customer_id"])
        ids = sorted(buyers_a - ever_b)
        out = st[st["customer_id"].isin(ids)][cols]
    elif kind == "customer_ids":
        ids = segment.get("ids", [])
        c = ds.customers.set_index("customer_id")
        out = pd.DataFrame({"customer_id": ids, "name": [c["name"].get(i, "") for i in ids],
                            "phone": [c["phone"].get(i, "") for i in ids]})
    else:
        raise ValueError(f"Unknown segment type '{kind}'")
    return out.reset_index(drop=True)


# ------------------------------------------------------------------ measurement
def _p_value(x1: int, n1: int, x2: int, n2: int) -> float | None:
    """Two-proportion z-test (two-sided)."""
    if min(n1, n2) == 0:
        return None
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (x1 / n1 - x2 / n2) / se
    return round(math.erfc(abs(z) / math.sqrt(2)), 4)


def measure(campaign: Campaign, ds) -> dict:
    if campaign.status != "sent" or not campaign.sent_at:
        return {"status": "not_sent", "message": "Results start after the campaign is marked sent."}
    sent = date.fromisoformat(campaign.sent_at[:10])
    start = sent + timedelta(days=1)
    end_full = sent + timedelta(days=campaign.measure_after_days)
    end = min(end_full, ds.as_of)
    if end < start:
        return {"status": "waiting", "message": f"No data after {sent} yet - upload new bills to measure.",
                "window": [start.isoformat(), end_full.isoformat()]}
    goal = GOALS[campaign.goal]["measure"]
    groups = {"target": [mm.customer_id for mm in campaign.targets],
              "holdout": [mm.customer_id for mm in campaign.members if mm.group == "holdout"]}
    s = ds.sales_x
    win = s[(s["date"] >= pd.Timestamp(start)) & (s["date"] <= pd.Timestamp(end))]
    rep = ds.repayments
    rwin = rep[(rep["date"] >= pd.Timestamp(start)) & (rep["date"] <= pd.Timestamp(end))] if len(rep) else rep
    out_groups = {}
    for g, ids in groups.items():
        ids_set = set(ids)
        n = len(ids_set)
        if goal == "repayment":
            paid = rwin[rwin["customer_id"].isin(ids_set)] if len(rwin) else rwin
            converted = paid["customer_id"].nunique() if len(paid) else 0
            value = int(paid["amount_paise"].sum()) if len(paid) else 0
        else:
            w = win[win["customer_id"].isin(ids_set)]
            if goal == "item":
                w = w[w["item_id"] == campaign.params.get("item_b")]
            converted = w["customer_id"].nunique()
            value = int(w["line_total_paise"].sum())
        out_groups[g] = {"members": n, "converted": int(converted),
                         "rate_pct": round(100 * converted / n, 1) if n else None,
                         "value_paise": value, "value_per_member_paise": int(round(value / n)) if n else None}
    t, h = out_groups["target"], out_groups["holdout"]
    res = {"status": "measured" if end >= end_full else "measuring", "window": [start.isoformat(), end.isoformat()],
           "days_measured": (end - start).days + 1, "full_window_days": campaign.measure_after_days,
           "metric": {"purchase": "customers who bought", "item": "customers who bought the suggested item",
                      "repayment": "customers who repaid"}[goal],
           "groups": out_groups}
    if h["members"] and t["members"]:
        lift_pts = round((t["rate_pct"] or 0) - (h["rate_pct"] or 0), 1)
        incremental = int(round(((t["value_per_member_paise"] or 0) - (h["value_per_member_paise"] or 0))
                                * t["members"]))
        p = _p_value(t["converted"], t["members"], h["converted"], h["members"])
        res.update({
            "lift_points": lift_pts,
            "relative_lift_pct": round(100 * lift_pts / h["rate_pct"], 1) if h["rate_pct"] else None,
            "incremental_value_paise": incremental, "p_value": p,
            "verdict": ("worked" if p is not None and p < 0.05 and lift_pts > 0 else
                        "no clear effect" if p is not None else "too few to tell"),
        })
        if h["members"] < 20:
            res["caution"] = f"Only {h['members']} customers in the hold-out - treat the result as a hint."
    else:
        res["verdict"] = "no hold-out - showing the response only"
    res["formula"] = ("rate = members with ≥1 bill (or repayment) in the window ÷ members; lift = target "
                      "rate − hold-out rate; extra value = (value per target − value per hold-out) × "
                      "targets; p-value from a two-proportion z-test")
    return res


# ------------------------------------------------------------------ store
class CampaignStore:
    def __init__(self, manager):
        self.m = manager
        self.items: dict[str, Campaign] = {}
        self._lock = threading.RLock()
        self._loaded = False

    def _shop(self) -> str:
        return self.m.settings.shop_name

    def _persist(self, c: Campaign) -> None:
        if self.m.sql is not None:
            try:
                self.m.sql.upsert_campaign(c.model_dump())
            except Exception as exc:  # pragma: no cover
                log.warning("could not save campaign %s: %s", c.campaign_id, exc)

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.m.sql is not None:
            try:
                for d in self.m.sql.campaigns():
                    c = Campaign(**d)
                    self.items[c.campaign_id] = c
            except Exception as exc:  # pragma: no cover
                log.warning("could not load campaigns: %s", exc)
        self._load_sample()

    def _load_sample(self) -> None:
        ds = self.m.require()
        f = self.m.settings.data_dir / "campaigns.json"
        if ds.source != "sample" or not f.exists():
            return
        for raw in json.loads(f.read_text(encoding="utf-8")):
            if raw["campaign_id"] in self.items:
                continue
            ids = [x["customer_id"] for x in raw["members"]]
            grp = {x["customer_id"]: x["group"] for x in raw["members"]}
            seg = resolve_segment(self.m, {"type": "customer_ids", "ids": ids}, {})
            c = Campaign(campaign_id=raw["campaign_id"], name=raw["name"], goal=raw["goal"],
                         segment=raw["segment"], language=raw["language"], holdout_pct=raw["holdout_pct"],
                         measure_after_days=raw["measure_after_days"], status="sent",
                         template=TEMPLATES[raw["goal"]][raw["language"]],
                         created_at=raw["sent_at"], sent_at=raw["sent_at"],
                         history=[{"status": "sent", "at": raw["sent_at"]}])
            c.members = [Member(customer_id=r.customer_id, name=r.name, phone=r.phone,
                                group=grp[r.customer_id]) for r in seg.itertuples()]
            self._render(c)
            self.items[c.campaign_id] = c

    def _render(self, c: Campaign) -> None:
        for mm in c.members:
            mm.message = render(c.template, self._shop(), mm, c.params, c.offer)
            mm.whatsapp_url = whatsapp_url(mm.message, mm.phone)

    def goal_defaults(self) -> list[dict]:
        return [{"goal": g, **v, "templates": TEMPLATES[g]} for g, v in GOALS.items()]

    def build(self, goal: str, segment: dict | None = None, language: str = "hinglish", offer: str = "",
              params: dict | None = None, name: str | None = None, holdout_pct: int | None = None,
              measure_after_days: int = 14, template: str | None = None) -> Campaign:
        if goal not in GOALS:
            raise ValueError(f"Unknown goal '{goal}'")
        params = dict(params or {})
        ds = self.m.require()
        for k in ("item_a", "item_b"):
            if params.get(k):
                params[f"{k}_name"] = ds.item_index["name"].get(params[k], params[k])
        seg = segment or GOALS[goal]["segment"]
        members = resolve_segment(self.m, seg, params)
        cid = f"CMP-{date.today():%y%m%d}-{uuid4().hex[:5].upper()}"
        hp = GOALS[goal]["holdout_pct"] if holdout_pct is None else holdout_pct
        c = Campaign(
            campaign_id=cid, name=name or GOALS[goal]["label"], goal=goal, segment=seg, language=language,
            offer=offer, holdout_pct=hp, measure_after_days=measure_after_days, params=params,
            template=template or TEMPLATES[goal][language],
            created_at=datetime.now(timezone.utc).isoformat())
        recs = members.to_dict("records")
        hold = holdout_ids(cid, [r["customer_id"] for r in recs], hp)
        c.members = [Member(customer_id=r["customer_id"], name=r.get("name") or "", phone=r.get("phone") or "",
                            amount_paise=int(r["amount_paise"]) if "amount_paise" in r and
                            pd.notna(r["amount_paise"]) else None,
                            group="holdout" if r["customer_id"] in hold else "target")
                     for r in recs]
        self._render(c)
        c.history = [{"status": "draft", "at": c.created_at}]
        return c

    def create(self, **kw) -> Campaign:
        c = self.build(**kw)
        with self._lock:
            self._load()
            self.items[c.campaign_id] = c
        self._persist(c)
        return c

    def get(self, cid: str) -> Campaign:
        with self._lock:
            self._load()
            if cid not in self.items:
                raise KeyError(cid)
            return self.items[cid]

    def list(self, status: str | None = None) -> list[Campaign]:
        with self._lock:
            self._load()
            out = [c for c in self.items.values() if status is None or c.status == status]
        return sorted(out, key=lambda c: c.created_at, reverse=True)

    def edit(self, cid: str, language: str | None = None, offer: str | None = None,
             template: str | None = None, exclude: list[str] | None = None) -> Campaign:
        with self._lock:
            c = self.get(cid)
            if c.status != "draft":
                raise ValueError(f"Campaign {cid} is '{c.status}' - only drafts can be edited")
            if language:
                c.language = language
                if template is None:
                    c.template = TEMPLATES[c.goal][language]
            if template is not None:
                try:
                    template.format(name="", shop="", amount="", item_a="", item_b="", offer="")
                except (KeyError, IndexError, ValueError) as exc:
                    raise ValueError("Template may only use {name}, {shop}, {amount}, {item_a}, "
                                     "{item_b}, {offer}") from exc
                c.template = template
            if offer is not None:
                c.offer = offer
            if exclude:
                c.members = [mm for mm in c.members if mm.customer_id not in set(exclude)]
            self._render(c)
            self._persist(c)
            return c

    def set_status(self, cid: str, status: str) -> Campaign:
        with self._lock:
            c = self.get(cid)
            allowed = {"draft": {"sent", "cancelled"}, "sent": set(), "cancelled": set()}[c.status]
            if status not in allowed:
                raise ValueError(f"Cannot move campaign from '{c.status}' to '{status}'")
            now = datetime.now(timezone.utc).isoformat()
            c.status = status
            if status == "sent":
                c.sent_at = self.m.require().as_of.isoformat()  # the data's "today"
            c.history.append({"status": status, "at": now})
            self._persist(c)
            return c

    def result(self, cid: str) -> dict:
        c = self.get(cid)
        return {"campaign_id": cid, "name": c.name, "goal": c.goal, **measure(c, self.m.require())}

    def to_csv(self, c: Campaign) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["customer_id", "name", "phone", "message", "whatsapp_link"])
        for mm in c.targets:
            w.writerow([csv_safe(mm.customer_id), csv_safe(mm.name), csv_safe(mm.phone),
                        csv_safe(mm.message), mm.whatsapp_url])
        return buf.getvalue()


def summary(c: Campaign) -> dict:
    return {
        "campaign_id": c.campaign_id, "name": c.name, "goal": c.goal, "status": c.status,
        "language": c.language, "segment": c.segment, "members": len(c.members),
        "targets": len(c.targets), "holdout": len(c.members) - len(c.targets),
        "holdout_pct": c.holdout_pct, "created_at": c.created_at, "sent_at": c.sent_at,
        "sample_message": c.targets[0].message if c.targets else "",
        "udhaar_total_paise": sum(mm.amount_paise or 0 for mm in c.targets) if c.goal == "udhaar_reminder" else None,
    }


def wa_share(text: str) -> str:
    return f"https://wa.me/?text={quote(text)}"
