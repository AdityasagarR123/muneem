"""Hindi / Hinglish vocabulary and fuzzy item matching.

Shop owners say "aata", "cheeni", "sarson ka tel". The LLM never maps those
to item ids - this deterministic matcher does, so the mapping is testable.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from difflib import SequenceMatcher

# canonical English token -> spoken / transliterated / Devanagari variants
ALIASES: dict[str, list[str]] = {
    "atta": ["aata", "aataa", "ata", "gehu", "gehun", "wheat", "flour", "आटा"],
    "rice": ["chawal", "chaawal", "chaval", "चावल"],
    "dal": ["daal", "dhal", "dāl", "दाल"],
    "toor": ["arhar", "tuvar", "tur", "tuar"],
    "moong": ["mung", "moongdal"],
    "chana": ["channa", "chane"],
    "sugar": ["cheeni", "chini", "shakkar", "चीनी"],
    "salt": ["namak", "नमक"],
    "oil": ["tel", "तेल"],
    "mustard": ["sarson", "sarso", "सरसों"],
    "sunflower": ["surajmukhi"],
    "groundnut": ["moongphali", "mungfali", "peanut"],
    "ghee": ["ghi", "घी"],
    "milk": ["doodh", "dudh", "दूध"],
    "dahi": ["curd", "yogurt", "दही"],
    "butter": ["makhan", "makkhan", "मक्खन"],
    "paneer": ["cottage"],
    "bread": ["double", "roti", "pav", "pao"],
    "eggs": ["egg", "anda", "ande", "अंडा"],
    "tea": ["chai", "chaay", "patti", "चाय"],
    "coffee": ["kaafi", "kofi"],
    "biscuits": ["biscuit", "biskut", "biskit"],
    "noodles": ["maggi", "noodle"],
    "chips": ["wafers", "chip"],
    "water": ["paani", "pani", "पानी"],
    "soap": ["sabun", "saabun", "साबुन"],
    "detergent": ["surf", "washing", "kapde"],
    "toothpaste": ["manjan", "paste"],
    "sweets": ["mithai", "मिठाई", "sweet"],
    "cashew": ["kaju", "काजू"],
    "almonds": ["badam", "baadam", "बादाम", "almond"],
    "raisins": ["kishmish", "kismis", "किशमिश", "raisin"],
    "turmeric": ["haldi", "हल्दी"],
    "chilli": ["mirch", "lal", "मिर्च", "chili"],
    "jeera": ["cumin", "zeera", "जीरा"],
    "besan": ["gramflour"],
    "maida": ["mayda"],
    "sooji": ["suji", "rava", "semolina"],
    "agarbatti": ["incense", "dhoop"],
    "matchbox": ["machis", "maachis", "matches"],
    "diya": ["diye", "deepak", "दीया"],
}

_VARIANT_TO_CANON: dict[str, str] = {}
for canon, variants in ALIASES.items():
    _VARIANT_TO_CANON[canon] = canon
    for v in variants:
        _VARIANT_TO_CANON[v.lower()] = canon

STOPWORDS = {
    "ka", "ki", "ke", "ko", "kya", "hai", "the", "a", "an", "of", "for", "me", "mein",
    "kitna", "kitni", "kitne", "kaun", "sa", "se", "aur", "and", "dikhao", "batao", "show",
    "how", "much", "many", "what", "is", "item", "items", "wala", "wali", "packet", "pack",
}

_TOKEN = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(str(text).lower()):
        if t in STOPWORDS:
            continue
        out.append(_VARIANT_TO_CANON.get(t, t))
    return out


def _score(query_tokens: list[str], query_text: str, name: str) -> float:
    name_tokens = tokens(name)
    if not name_tokens:
        return 0.0
    hits = 0.0
    for q in query_tokens:
        if q in name_tokens:
            hits += 1.0
        elif any(SequenceMatcher(None, q, n).ratio() >= 0.84 for n in name_tokens):
            hits += 0.8
    coverage = hits / max(len(query_tokens), 1)
    fuzzy = SequenceMatcher(None, " ".join(query_tokens), " ".join(name_tokens)).ratio()
    return round(0.75 * coverage + 0.25 * fuzzy, 4)


def match_names(query: str, candidates: list[tuple[str, str]], limit: int = 5,
                min_score: float = 0.35) -> list[dict]:
    """Rank (id, name) candidates for a free-text query. Exact id match wins."""
    q = str(query).strip()
    if not q:
        return []
    for cid, name in candidates:
        if q.lower() == str(cid).lower():
            return [{"id": cid, "name": name, "score": 1.0}]
    qt = tokens(q)
    if not qt:
        return []
    scored = [
        {"id": cid, "name": name, "score": _score(qt, q, name)} for cid, name in candidates
    ]
    scored = [s for s in scored if s["score"] >= min_score]
    scored.sort(key=lambda s: (-s["score"], len(s["name"])))
    return scored[:limit]


_SIZE = re.compile(r"^\d")


def find_mentions(text: str, candidates: list[tuple[str, str]], weights: dict | None = None,
                  min_score: float = 0.4, limit: int = 3) -> list[dict]:
    """Find which candidates a whole sentence mentions ('atta ka margin kitna hai?').

    Each candidate name is scored by the IDF-weighted share of its words found in the
    sentence. A match must contain a distinctive word (used by <= 3 candidates) or
    all of the name's words - so 'dal' alone (4 dals) is ambiguous, 'toor dal' is not.
    Size tokens ('5kg') only add a bonus; `weights` (e.g. sales) break ties.
    """
    q = set(tokens(text))
    if not q:
        return []
    parsed, df = [], Counter()
    for cid, name in candidates:
        toks = tokens(name)
        core = [t for t in toks if not _SIZE.match(t)]
        size = [t for t in toks if _SIZE.match(t)]
        parsed.append((cid, name, core, size))
        df.update(set(core))
    n = max(len(candidates), 1)
    out = []
    for cid, name, core, size in parsed:
        if not core:
            continue
        idf = {t: math.log(1 + n / df[t]) for t in core}
        matched, hit, distinctive = 0.0, [], False
        for t in core:
            ok = t in q or (len(t) >= 4 and any(
                len(x) >= 4 and SequenceMatcher(None, x, t).ratio() >= 0.85 for x in q))
            if ok:
                matched += idf[t] if t in q else 0.8 * idf[t]
                hit.append(t)
                distinctive |= df[t] <= 3
        if not hit or not (distinctive or len(hit) == len(core)):
            continue
        score = matched / sum(idf.values())
        if size and any(s in q for s in size):
            score += 0.15
        score += 0.02 * (weights or {}).get(cid, 0.0)
        if score >= min_score:
            out.append({"id": cid, "name": name, "score": round(score, 3), "matched": hit})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]
