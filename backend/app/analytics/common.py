"""Shared result model and presentation helpers for analytics."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.core.money import rupees


class Insight(BaseModel):
    """What every analytic / graph tool returns. The agent shows `formula` and `rows`
    as evidence, so every number the owner sees is traceable."""

    kind: str
    title: str
    as_of: str | None = None
    headline: dict[str, Any] = Field(default_factory=dict)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    formula: str = ""
    assumptions: list[str] = Field(default_factory=list)
    confidence: str = "ok"  # ok | insufficient_data | no_data | partial
    engine: str = "pandas"  # pandas | arangodb | memory-graph
    meta: dict[str, Any] = Field(default_factory=dict)


def _scalar(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.date().isoformat()
    if isinstance(v, (datetime, date)):
        return v.isoformat()[:10]
    if v is pd.NaT:
        return None
    return v


def public(obj):
    """Recursively convert to JSON-safe values; every `*_paise` key also gets an
    `*_rs` twin in rupees (paise stays, so sums can be re-checked exactly)."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = public(v)
            if isinstance(k, str) and k.endswith("_paise"):
                vv = _scalar(v)
                out[k[:-6] + "_rs"] = None if vv is None else rupees(vv)
        return out
    if isinstance(obj, (list, tuple)):
        return [public(v) for v in obj]
    if isinstance(obj, pd.DataFrame):
        return [public(r) for r in obj.to_dict("records")]
    return _scalar(obj)


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list of dicts with NaN turned back into None."""
    out = []
    for r in df.to_dict("records"):
        out.append({k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()})
    return out


def insight(**kw) -> Insight:
    kw["headline"] = public(kw.get("headline", {}))
    kw["rows"] = public(kw.get("rows", []))
    kw["meta"] = public(kw.get("meta", {}))
    return Insight(**kw)


def pct(a, b) -> float | None:
    if not b:
        return None
    return round(100.0 * a / b, 2)


def change_pct(cur, prev) -> float | None:
    if prev in (0, None) or cur is None:
        return None
    return round(100.0 * (cur - prev) / abs(prev), 1)
