"""Column profiler: what kind of data is in each column, judged from the values themselves.

Types: identifier, integer, decimal, money, percent, date, phone, email, gstin, upi, boolean,
category, text. A type is chosen when ≥85% of non-empty sample values fit it.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from app.ingest.schema import PAYMENT_MAP, TRUE_WORDS

SAMPLE = 600
FIT = 0.85

_PHONE = re.compile(r"^(\+?91[\s-]?|0)?[6-9x][\dx]{4}[\s-]?[\dx]{5}$", re.I)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
_GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_UPI = re.compile(r"^[\w.\-]{2,}@[a-z]{2,}$", re.I)
_MONEY = re.compile(r"^(₹|rs\.?|inr)?\s*-?[\d,]+(\.\d{1,2})?$", re.I)
_PCT = re.compile(r"^\d{1,2}(\.\d+)?\s*%$")
_CODE = re.compile(r"^[A-Za-z]{0,6}[-_/]?\d{1,10}[A-Za-z]?$")
_BOOL = {"yes", "no", "y", "n", "true", "false", "haan", "nahi", "0", "1"}
GST_VALUES = {0.0, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0, 40.0}


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.replace(r"(?i)(rs\.?|inr|₹|,|\s|%)", "", regex=True), errors="coerce")


def _share(mask: pd.Series) -> float:
    return float(mask.mean()) if len(mask) else 0.0


def profile_column(name: str, raw: pd.Series) -> dict:
    s = raw.astype(str).str.strip()
    total = len(s)
    nonempty = s[s.ne("") & s.str.lower().ne("nan")]
    sample = nonempty.head(SAMPLE)
    distinct = nonempty.nunique()
    info = {
        "column": name, "rows": int(total),
        "null_pct": round(100 * (1 - len(nonempty) / total), 1) if total else 0.0,
        "distinct": int(distinct),
        "distinct_pct": round(100 * distinct / len(nonempty), 1) if len(nonempty) else 0.0,
        "examples": list(dict.fromkeys(sample.head(40)))[:3],
    }
    if sample.empty:
        return {**info, "type": "empty"}
    low = sample.str.lower()
    nums = _num(sample)
    numeric = nums.notna()
    checks = [
        ("email", _share(sample.str.match(_EMAIL))),
        ("gstin", _share(sample.str.upper().str.match(_GSTIN))),
        ("upi", _share(sample.str.match(_UPI))),
        ("phone", _share(sample.str.replace(r"[\s-]", "", regex=True).str.match(_PHONE))
         if not _share(sample.str.contains(r"\.", regex=True)) > 0.5 else 0.0),
        ("percent", _share(sample.str.match(_PCT))),
        ("boolean", _share(low.isin(_BOOL)) if distinct <= 3 else 0.0),
    ]
    kind = next((k for k, v in checks if v >= FIT), None)
    if kind is None and _share(numeric) >= FIT:
        vals = nums[numeric]
        integral = _share((vals % 1).abs() < 1e-9)
        has_currency = _share(sample.str.contains(r"(?i)₹|rs\.?|inr|,", regex=True)) > 0.3
        two_dp = _share(sample.str.match(r"^-?[\d,]+\.\d{2}$")) > 0.5
        if set(vals.round(2).unique()) <= GST_VALUES and distinct <= 6:
            kind = "percent"
        elif has_currency or two_dp:
            kind = "money"
        elif integral > 0.98:
            kind = "identifier" if (distinct / len(nonempty) > 0.95 and vals.min() >= 1000) else "integer"
        else:
            kind = "decimal"
        info["stats"] = {"min": float(vals.min()), "median": float(vals.median()), "max": float(vals.max())}
        med = float(vals.median())
        mad = float((vals - med).abs().median()) or 0.0
        if mad:
            info["outliers"] = int(((vals - med).abs() > 8 * 1.4826 * mad).sum())
    if kind is None:
        dates = pd.to_datetime(sample, errors="coerce", format="mixed", dayfirst=True)
        if _share(dates.notna()) >= FIT and _share(sample.str.contains(r"[-/.]|\s[A-Za-z]{3}", regex=True)) > 0.8:
            kind = "date"
            info["stats"] = {"min": str(dates.min().date()), "max": str(dates.max().date())}
    if kind is None:
        ratio = distinct / len(nonempty)
        if _share(sample.str.match(_CODE)) >= FIT and ratio > 0.02:
            kind = "identifier"
        elif ratio <= 0.2 and distinct <= 60:
            kind = "category"
        else:
            kind = "text"
    info["type"] = kind
    if kind in ("category", "text"):
        info["payment_like_pct"] = round(100 * _share(low.map(PAYMENT_MAP).notna()), 1)
        info["bool_like_pct"] = round(100 * _share(low.isin(TRUE_WORDS | {"no", "n", "false", "nahi"})), 1)
    return info


def profile_table(df: pd.DataFrame) -> list[dict]:
    return [profile_column(c, df[c]) for c in df.columns]


def numeric_values(raw: pd.Series) -> np.ndarray:
    v = _num(raw.astype(str).str.strip())
    return v[v.notna()].to_numpy()
