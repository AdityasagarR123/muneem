"""Web research tools for the agent (registered into tools.TOOLS).

Outside context only - market prices, news, regulations, competitor info. Results are tagged
external (meta.external) so the envelope shows them in a separate, cited "From the web" block.
Queries are scrubbed of customer names, phones, amounts and the shop name before they leave.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.agent.tools import TOOLS, ToolError, ToolOutput, ToolSpec
from app.analytics.common import insight
from app.research.web import ResearchError, scrub


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="What to look up on the web, in general terms (e.g. "
                                        "'sugar wholesale price India this week'). Never include "
                                        "customer names, phone numbers or the shop's own figures.")
    topic: Literal["general", "news", "finance"] = Field("general", description="news for recent "
                                                         "events, finance for commodity/market prices")
    time_range: Optional[Literal["day", "week", "month", "year"]] = Field(
        None, description="Only results from this recent window. Set it whenever the owner says "
                          "latest / today / this week / this month")
    max_results: int = Field(5, ge=1, le=8)


class WebExtractArgs(BaseModel):
    url: str = Field(..., description="A public https page, usually one returned by web_search")
    goal: str = Field(..., description="What to read from the page, e.g. 'the listed price of "
                                       "Aashirvaad atta 5kg'")


def private_names(m) -> list[str]:
    names = [m.settings.shop_name]
    ds = m.ds
    if ds is not None and len(ds.customers):
        # full names only (with a space): single first names like "Ravi" are too common to hide
        names += [n for n in ds.customers["name"].dropna().astype(str) if " " in n.strip()]
    return names


_RECENT = [("day", re.compile(r"\b(today|aaj|abhi|right now)\b", re.I)),
           ("week", re.compile(r"\b(this week|is hafte|latest|recent|current|currently|now|news)\b", re.I)),
           ("month", re.compile(r"\b(this month|is mahine)\b", re.I))]


def recency(query: str) -> str | None:
    """'latest' / 'this week' in the words means old articles must not answer it."""
    for window, rx in _RECENT:
        if rx.search(query or ""):
            return window
    return None


def _research(m):
    r = m.research
    if r is None:
        raise ToolError("Web research is not available.")
    return r


def t_web_search(m, a: WebSearchArgs) -> ToolOutput:
    r = _research(m)
    q, removed = scrub(a.query, private_names(m))
    if len(q) < 3:
        raise ToolError("After removing private details nothing was left to search for. "
                        "Ask about the market in general terms.")
    time_range = a.time_range or recency(a.query)
    try:
        res = r.search(q, topic=a.topic, time_range=time_range, max_results=a.max_results)
    except ResearchError as exc:
        raise ToolError(str(exc)) from exc
    rows = [{"title": x["title"], "domain": x["domain"], "published_date": x["published_date"],
             "snippet": x["snippet"], "url": x["url"]} for x in res["results"]]
    notes = ["Web results are outside information, not your shop's data; Muneem has not "
             "verified them."]
    if removed:
        notes.append("Removed before searching: " + ", ".join(removed) + ".")
    return ToolOutput(insight=insight(
        kind="web_search", title=f"From the web: {q}", rows=rows,
        headline={"results": len(rows), "query_sent": q, "retrieved_at": res["retrieved_at"]},
        formula=f"{res['provider']} web search, topic={a.topic}"
                + (f", last {time_range}" if time_range else ""),
        assumptions=notes, confidence="ok" if rows else "no_data", engine="tavily",
        meta={"external": True, "provider": res["provider"], "retrieved_at": res["retrieved_at"],
              "cached": res["cached"], "removed": removed}))


def t_web_extract(m, a: WebExtractArgs) -> ToolOutput:
    r = _research(m)
    goal, removed = scrub(a.goal, private_names(m))
    try:
        res = r.extract(a.url, goal)
    except ResearchError as exc:
        raise ToolError(str(exc)) from exc
    from urllib.parse import urlparse
    domain = urlparse(res["url"]).hostname
    rows = [{"label": str(f.get("label", "")), "value": str(f.get("value", "")),
             "url": res["url"], "domain": domain} for f in res["facts"]]
    found = res["status"] == "found" and (rows or res["summary"])
    notes = ["Read from a live web page - outside information that Muneem has not verified."]
    if removed:
        notes.append("Removed before sending: " + ", ".join(removed) + ".")
    return ToolOutput(insight=insight(
        kind="web_extract", title=f"From the web: {domain}", rows=rows,
        headline={"found": bool(found), "summary": res["summary"], "url": res["url"],
                  "retrieved_at": res["retrieved_at"]},
        formula=f"{res['provider']} page reader, goal: {goal}",
        assumptions=notes, confidence="ok" if found else "no_data", engine="tinyfish",
        meta={"external": True, "provider": res["provider"], "retrieved_at": res["retrieved_at"],
              "cached": res["cached"], "removed": removed}))


for spec in [
    ToolSpec("web_search", "Search the public web for OUTSIDE context only: market or wholesale "
             "prices, commodity trends, news, government rules (GST changes), competitor or brand "
             "info. Never for this shop's own numbers. Returns dated, cited sources.",
             WebSearchArgs, t_web_search),
    ToolSpec("web_extract", "Read one public web page (usually from web_search) and pull out "
             "specific facts such as a listed price. Slow and costly: use only when the search "
             "snippets do not already answer.", WebExtractArgs, t_web_extract),
]:
    TOOLS[spec.name] = spec
