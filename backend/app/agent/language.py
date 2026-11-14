"""Detect whether the owner wrote English, Hinglish (Roman Hindi) or Hindi (Devanagari)."""

from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[a-z]+")

HINGLISH = {
    "kya", "hai", "hain", "karna", "karu", "karun", "karein", "kitna", "kitni", "kitne",
    "dikhao", "batao", "bata", "kal", "aaj", "mera", "meri", "mere", "kaun", "kaunsa", "kis",
    "kisko", "nahi", "nahin", "chahiye", "bacha", "bachi", "bika", "biki", "pehle", "saath",
    "gaya", "gayi", "raha", "rahi", "wala", "wali", "kyun", "kab", "abhi", "sabse", "zyada",
    "kam", "paisa", "paise", "kamai", "munafa", "kharcha", "maal", "dukaan", "dukan", "ji",
    "hoga", "hogi", "agar", "toh", "aur", "ko", "ka", "ki", "ke", "mein", "se", "par", "liye",
    "konsa", "konsi", "khatam", "jagah", "badle", "mangwa", "mangana", "lena", "dena",
}
STRONG = HINGLISH - {"ko", "ka", "ki", "ke", "se", "par", "aur", "toh", "ji", "kam"}


def detect_language(text: str) -> str:
    if _DEVANAGARI.search(text or ""):
        return "hi"
    words = _WORD.findall((text or "").lower())
    strong = sum(w in STRONG for w in words)
    weak = sum(w in HINGLISH for w in words)
    if strong >= 1 or weak >= 2:
        return "hinglish"
    return "en"
