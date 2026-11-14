"""Untrusted-text handling. Every string from an upload passes through here."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_KEY_BAD = re.compile(r"[^A-Za-z0-9_\-:.@()+,=;$!*'%]")


def sanitize_text(value, max_len: int = 80) -> str:
    """Drop control/format characters, collapse whitespace, cap length."""
    if value is None:
        return ""
    s = str(value)
    s = "".join(ch for ch in s if unicodedata.category(ch)[0] != "C" or ch in " \t")
    s = _WS.sub(" ", s).strip()
    return s[:max_len]


def normalize_header(h) -> str:
    """'MRP (incl GST)' -> 'mrp_incl_gst'."""
    return re.sub(r"[^0-9a-z]+", "_", str(h).strip().lower()).strip("_")


def csv_safe(value) -> str:
    """Prevent spreadsheet formula injection in exported CSVs."""
    s = "" if value is None else str(value)
    if s and s[0] in "=+-@\t\r":
        return "'" + s
    return s


def safe_key(value: str) -> str:
    """ArangoDB _key-safe version of an id (ids themselves are stored untouched)."""
    k = _KEY_BAD.sub("_", str(value))[:250]
    return k or "_"


def slug(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "-", str(value).lower()).strip("-") or "uncategorised"
