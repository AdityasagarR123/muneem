"""The single response shape every chat answer uses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    title: str = ""
    formula: str = ""
    headline: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    confidence: str = "ok"
    engine: str = "pandas"
    assumptions: list[str] = Field(default_factory=list)
    resolved: dict[str, Any] = Field(default_factory=dict)  # 'aata' -> I001 etc.
    error: str | None = None
    external: bool = False  # web result, not the shop's own data


class Visual(BaseModel):
    type: str  # chart | table | mermaid | kpis
    title: str = ""
    spec: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    type: str  # restock_draft | open_item | download_csv
    label: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    """A cited outside (web) source, shown apart from the shop's own numbers."""
    title: str
    url: str
    domain: str = ""
    snippet: str = ""
    published_date: str | None = None
    retrieved_at: str | None = None
    provider: str = ""  # Tavily | TinyFish
    tool: str = ""


class Grounding(BaseModel):
    checked: int = 0
    from_web: int = 0  # numbers that come from cited web sources (not verified shop data)
    verified: int = 0  # numbers found in this shop's computed data (excludes trivial ones)
    ungrounded: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.ungrounded


class Envelope(BaseModel):
    conversation_id: str
    question: str
    answer_md: str
    language: str = "en"  # en | hinglish | hi
    mode: str = "rules"  # llm | rules
    model: str | None = None
    confidence: str = "ok"  # ok | insufficient_data | no_data | no_tool | partial
    evidence: list[Evidence] = Field(default_factory=list)
    visuals: list[Visual] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    grounding: Grounding = Field(default_factory=Grounding)
    warnings: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    latency_ms: int = 0
