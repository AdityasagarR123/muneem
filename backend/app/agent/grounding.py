"""Number grounding check: every number in the answer must appear in the tool
results the model saw (allowing for rounding, lakh/crore/k wording, and small counts).
Proof for the judges that the LLM did not invent numbers."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.agent.envelope import Grounding

_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(lakh|lac|crore|cr|k|hazar|hazaar|thousand)?\b",
                  re.I)
_MULT = {"lakh": 1e5, "lac": 1e5, "crore": 1e7, "cr": 1e7, "k": 1e3, "hazar": 1e3, "hazaar": 1e3,
         "thousand": 1e3}


def _numbers_in(obj, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(float(obj))
        out.add(abs(float(obj)))  # answers write "down 20%" / "-₹17,440" for -20.1 / -17439.58
    elif isinstance(obj, str):
        for m in _NUM.finditer(obj):
            try:
                out.add(float(m.group(1).replace(",", "")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers_in(v, out)


def _matches(text_num: str, value: float, known: set[float]) -> bool:
    places = len(text_num.split(".")[1]) if "." in text_num else 0
    target = Decimal(text_num)
    for v in known:
        if abs(v - value) <= max(0.01, 0.005 * abs(v)):
            return True
        try:
            if Decimal(str(v)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) == target:
                return True
            if abs(v) >= 1000 and Decimal(str(round(v))) == target:  # ₹20,736 for 20735.98
                return True
        except InvalidOperation:
            continue
    return False


def check(answer: str, evidence: list, question: str = "") -> Grounding:
    known: set[float] = set()
    _numbers_in(evidence, known)
    asked: set[float] = set()
    _numbers_in(question, asked)
    g = Grounding()
    for m in _NUM.finditer(answer or ""):
        raw, mult = m.group(1).replace(",", ""), (m.group(2) or "").lower()
        try:
            value = float(raw) * _MULT.get(mult, 1)
        except ValueError:
            continue
        g.checked += 1
        if value in asked or (value.is_integer() and value <= 10 and not mult):
            continue
        text_num = raw if not mult else None
        ok = _matches(text_num, value, known) if text_num else any(
            abs(v - value) <= 0.006 * abs(v) for v in known if v)
        if not ok:
            g.ungrounded.append(m.group(0).strip())
    return g
