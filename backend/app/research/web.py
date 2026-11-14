"""External intelligence: Tavily web search and TinyFish page extraction.

Guarantees:
* Privacy - queries are scrubbed before they leave the server: phone numbers, emails, GSTIN,
  UPI ids, rupee amounts / long numbers, the shop name and customers' full names are removed.
* Separation - results are tagged external, carry source URL, title, published date and the
  time we retrieved them, and are shown apart from the shop's own (computed) numbers.
* Cost control - results are cached for a day; daily caps per provider (TinyFish bills per
  agent step, so extraction is capped tighter and limited to a few steps per run).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timezone
from urllib.parse import urlparse

import httpx

log = logging.getLogger("muneem.research")

TAVILY_URL = "https://api.tavily.com/search"
TINYFISH_URL = "https://agent.tinyfish.ai/v1/automation/run"

_PHONE = re.compile(r"(\+?91[\s-]?)?[6-9x]\d{4}[\s-]?\d{5}|\+?\d[\d\s-]{9,}\d", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_UPI = re.compile(r"\b[\w.-]{2,}@[a-z]{2,}\b", re.I)
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.I)
_RUPEES = re.compile(r"(₹|rs\.?|inr)\s*[\d,]+(\.\d+)?|\b\d{1,3}(,\d{2,3})+(\.\d+)?\b|\b\d{5,}\b", re.I)
_IDS = re.compile(r"\b(C|I|S|B|PO|CMP)-?\d{2,}[\w-]*\b")


class ResearchError(RuntimeError):
    pass


def scrub(query: str, private_names: list[str]) -> tuple[str, list[str]]:
    """Remove anything that could identify the shop or its customers."""
    removed: list[str] = []
    q = query
    for label, rx in (("email", _EMAIL), ("UPI id", _UPI), ("GSTIN", _GSTIN), ("phone number", _PHONE),
                      ("amount", _RUPEES), ("internal id", _IDS)):
        if rx.search(q):
            removed.append(label)
            q = rx.sub(" ", q)
    for name in sorted({n for n in private_names if n and len(n) >= 4}, key=len, reverse=True):
        pat = re.compile(r"\b" + re.escape(name) + r"\b", re.I)
        if pat.search(q):
            removed.append("customer or shop name")
            q = pat.sub(" ", q)
    q = re.sub(r"\s+", " ", q).strip(" ,.-")
    return q, sorted(set(removed))


def public_url(url: str) -> str:
    p = urlparse(url.strip())
    if p.scheme != "https" or not p.hostname:
        raise ResearchError("Only https:// web pages can be read")
    host = p.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        raise ResearchError("That address is not a public web page")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ResearchError("That address is not a public web page")
    except ValueError:
        pass  # a hostname, fine
    return url.strip()


class _Budget:
    def __init__(self):
        self.day = date.today()
        self.used: dict[str, int] = {}

    def take(self, provider: str, limit: int) -> None:
        if date.today() != self.day:
            self.day, self.used = date.today(), {}
        if self.used.get(provider, 0) >= limit:
            raise ResearchError(f"Daily {provider} limit ({limit}) reached - try again tomorrow")
        self.used[provider] = self.used.get(provider, 0) + 1


class WebResearch:
    def __init__(self, settings, http: httpx.Client | None = None):
        self.s = settings
        self.http = http or httpx.Client(timeout=settings.research_timeout_s)
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()
        self.budget = _Budget()
        self._tf_custom_steps = True  # turned off if the account lacks TinyFish's step-limit beta

    # ---------------------------------------------------------------- status
    @property
    def search_ready(self) -> bool:
        return bool(self.s.tavily_api_key.strip())

    @property
    def extract_ready(self) -> bool:
        return bool(self.s.tinyfish_api_key.strip())

    def status(self) -> dict:
        return {"search": {"provider": "Tavily", "configured": self.search_ready,
                           "used_today": self.budget.used.get("Tavily", 0), "daily_limit": self.s.tavily_daily_limit},
                "extract": {"provider": "TinyFish", "configured": self.extract_ready,
                            "used_today": self.budget.used.get("TinyFish", 0),
                            "daily_limit": self.s.tinyfish_daily_limit},
                "cache_entries": len(self._cache)}

    # ---------------------------------------------------------------- cache
    def _cached(self, key: dict, fn):
        k = json.dumps(key, sort_keys=True)
        ttl = self.s.web_cache_hours * 3600
        with self._lock:
            hit = self._cache.get(k)
            if hit and time.time() - hit[0] < ttl:
                return {**hit[1], "cached": True}
        val = fn()
        with self._lock:
            self._cache[k] = (time.time(), val)
            while len(self._cache) > 500:
                self._cache.pop(next(iter(self._cache)))
        return {**val, "cached": False}

    # ---------------------------------------------------------------- Tavily
    def search(self, query: str, topic: str = "general", time_range: str | None = None,
               max_results: int = 5) -> dict:
        if not self.search_ready:
            raise ResearchError("Web search is not set up (TAVILY_API_KEY missing)")
        body = {"query": query, "topic": topic, "search_depth": "basic",
                "max_results": max(1, min(max_results, 8)), "include_answer": False,
                "include_published_date": True}
        if time_range:
            body["time_range"] = time_range
        if topic == "general":
            body["country"] = "india"

        def call():
            self.budget.take("Tavily", self.s.tavily_daily_limit)
            try:
                r = self.http.post(TAVILY_URL, json=body,
                                   headers={"Authorization": f"Bearer {self.s.tavily_api_key.strip()}"})
            except httpx.HTTPError as exc:
                raise ResearchError(f"Web search unreachable: {type(exc).__name__}") from exc
            if r.status_code != 200:
                raise ResearchError({401: "Web search key rejected", 429: "Web search is busy - try later",
                                     432: "Web search plan limit reached", 433: "Web search spend limit reached"
                                     }.get(r.status_code, f"Web search failed ({r.status_code})"))
            data = r.json()
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return {"provider": "Tavily", "query": query, "retrieved_at": now, "results": [
                {"title": x.get("title") or x.get("url"), "url": x.get("url"),
                 "snippet": (x.get("content") or "")[:500], "published_date": x.get("published_date"),
                 "score": round(float(x.get("score") or 0), 3), "domain": urlparse(x.get("url", "")).hostname}
                for x in data.get("results", []) if x.get("url")]}

        return self._cached({"p": "tavily", **body}, call)

    # ---------------------------------------------------------------- TinyFish
    def extract(self, url: str, goal: str) -> dict:
        if not self.extract_ready:
            raise ResearchError("Page reading is not set up (TINYFISH_API_KEY missing)")
        url = public_url(url)
        body = {
            "url": url,
            "goal": (f"{goal.strip()}\nOnly report what the page actually shows. If it is not on the page, "
                     f"set status to not_found."),
            "browser_profile": "lite",
            "output_schema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["found", "not_found"]},
                    "facts": {"type": "array", "items": {"type": "object", "properties": {
                        "label": {"type": "string"}, "value": {"type": "string"}}}},
                    "summary": {"type": "string"},
                },
                "required": ["status"],
            },
            "agent_config": {"max_steps": self.s.tinyfish_max_steps,
                             "max_duration_seconds": self.s.research_timeout_s * 3},
        }

        def post():
            payload = body if self._tf_custom_steps else {k: v for k, v in body.items()
                                                          if k != "agent_config"}
            try:
                return self.http.post(TINYFISH_URL, json=payload, timeout=self.s.research_timeout_s * 4,
                                      headers={"X-API-Key": self.s.tinyfish_api_key.strip()})
            except httpx.HTTPError as exc:
                raise ResearchError(f"Page reader unreachable: {type(exc).__name__}") from exc

        def call():
            self.budget.take("TinyFish", self.s.tinyfish_daily_limit)
            r = post()
            if r.status_code == 403 and self._tf_custom_steps and "max steps" in r.text.lower():
                # step limits are a TinyFish beta feature; without it the provider default applies
                log.warning("TinyFish account has no custom max steps; using provider defaults")
                self._tf_custom_steps = False
                r = post()
            if r.status_code != 200:
                try:
                    detail = (r.json().get("error") or {}).get("message") or ""
                except ValueError:
                    detail = ""
                raise ResearchError({401: "Page reader key rejected", 402: "Page reader credits used up",
                                     429: "Page reader is busy - try later"}.get(
                    r.status_code, f"Page reading failed ({r.status_code}) {detail[:160]}".strip()))
            data = r.json()
            if data.get("status") != "COMPLETED":
                err = (data.get("error") or {}).get("message") or data.get("status")
                raise ResearchError(f"Could not read the page: {err}")
            res = data.get("result") or {}
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except ValueError:
                    res = {"status": "found", "summary": res}
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return {"provider": "TinyFish", "url": url, "goal": goal, "retrieved_at": now,
                    "status": res.get("status", "found"),
                    "facts": [f for f in (res.get("facts") or []) if isinstance(f, dict)][:30],
                    "summary": str(res.get("summary") or "")[:800], "steps": data.get("num_of_steps")}

        return self._cached({"p": "tinyfish", "url": url, "goal": goal}, call)
