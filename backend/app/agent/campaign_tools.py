"""Campaign tools for the agent (registered into tools.TOOLS)."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.actions import campaigns as camp_mod
from app.agent import visuals as V
from app.agent.envelope import Action
from app.agent.tools import TOOLS, ToolError, ToolOutput, ToolSpec, resolve_item
from app.analytics import customers as cust_mod
from app.analytics.common import insight

Segment = Literal[tuple(cust_mod.SEGMENTS)]  # type: ignore[valid-type]


class CampaignArgs(BaseModel):
    goal: Literal[tuple(camp_mod.GOALS)] = Field(  # type: ignore[valid-type]
        "win_back", description="win_back (customers who stopped coming), reward_champions, "
                                "welcome_new, cross_sell (needs item_a + item_b), udhaar_reminder")
    language: Literal["en", "hinglish", "hi"] = Field("hinglish", description="Match the owner's language")
    offer: Optional[str] = Field(None, description="Offer line ONLY if the owner asked for one - never invent")
    segment: Optional[Segment] = Field(None, description="Send to this customer segment instead of the default")
    item_a: Optional[str] = Field(None, description="cross_sell: item the customers already buy")
    item_b: Optional[str] = Field(None, description="cross_sell: item to suggest")


class ResultArgs(BaseModel):
    campaign: Optional[str] = Field(None, description="Campaign id or name; empty = latest sent campaign")


def t_draft_campaign(m, a: CampaignArgs) -> ToolOutput:
    params, resolved = {}, {}
    for k in ("item_a", "item_b"):
        v = getattr(a, k)
        if v:
            iid, name = resolve_item(m, v)
            params[k] = iid
            resolved[k] = {"said": v, "id": iid, "name": name}
    if a.goal == "cross_sell" and len(params) < 2:
        raise ToolError("For a cross-sell campaign tell me which item they buy and which item to suggest.")
    seg = {"type": "segment", "name": a.segment} if a.segment else None
    try:
        c = m.campaigns.create(goal=a.goal, segment=seg, language=a.language, offer=a.offer or "",
                               params=params)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    s = camp_mod.summary(c)
    rows = [{"name": mm.name or mm.customer_id, "group": mm.group, "message": mm.message,
             "amount_paise": mm.amount_paise} for mm in c.members[:8]]
    ins = insight(kind="campaign_draft", title=f"Draft campaign - {c.name} (not sent)",
                  as_of=m.require().as_of.isoformat(), headline=s, rows=rows,
                  formula="members chosen by a fixed rule on your data; hold-out customers are picked by "
                          "a hash so they stay the same and get no message - they show what would have "
                          "happened anyway",
                  assumptions=[c.note, "Drafts only - the owner sends the messages"],
                  confidence="ok" if c.members else "no_data")
    acts = [Action(type="open_whatsapp", label=f"Send to {mm.name or mm.customer_id}",
                   payload={"url": mm.whatsapp_url}) for mm in c.targets[:2]]
    acts.append(Action(type="download_csv", label="Download all messages",
                       payload={"path": f"/api/campaigns/{c.campaign_id}/export.csv"}))
    acts.append(Action(type="open_campaign", label="Review campaign", payload={"campaign_id": c.campaign_id}))
    return ToolOutput(ins, [V.table("Messages", [("name", "Customer"), ("group", "Group"),
                                                 ("message", "Message")], rows)], acts, resolved,
                      ["Pichhle campaign ka result kya raha?"])


def t_campaign_results(m, a: ResultArgs) -> ToolOutput:
    sent = m.campaigns.list("sent")
    if not sent:
        raise ToolError("No campaign has been sent yet.")
    c = sent[0]
    if a.campaign:
        q = a.campaign.lower()
        c = next((x for x in sent if x.campaign_id.lower() == q or q in x.name.lower()), None)
        if c is None:
            raise ToolError(f"No sent campaign matches '{a.campaign}'.", [x.name for x in sent[:5]])
    r = m.campaigns.result(c.campaign_id)
    groups = [{"group": "Messaged" if g == "target" else "Hold-out (no message)", **v}
              for g, v in r.get("groups", {}).items()]
    head = {k: v for k, v in r.items() if k not in ("groups", "formula")}
    ins = insight(kind="campaign_result", title=f"Did it work? - {c.name}", as_of=m.require().as_of.isoformat(),
                  headline=head, rows=groups, formula=r.get("formula", ""),
                  assumptions=[r["caution"]] if r.get("caution") else [],
                  confidence="ok" if groups else "no_data")
    vis = [V.chart("Came back within the window", "bar", "group", [("rate_pct", "% who bought")],
                   ins.rows, "%")] if groups else []
    return ToolOutput(ins, vis)


for spec in [
    ToolSpec("draft_campaign", "Draft personal WhatsApp messages to a group of customers (win back "
             "lapsed regulars, thank champions, welcome new customers, cross-sell an item, or polite "
             "udhaar reminders). Keeps a hold-out group to measure the effect. Owner sends.",
             CampaignArgs, t_draft_campaign),
    ToolSpec("campaign_results", "Did a sent campaign work? Compares customers who got the message with "
             "the hold-out group that didn't (return rate, extra sales, p-value).", ResultArgs,
             t_campaign_results),
]:
    TOOLS[spec.name] = spec
