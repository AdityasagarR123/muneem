"""Festival calendar.

Used in two directions:
* backward - demand measured inside a past festival window is not "normal"
  demand, so the baseline excludes those days (prevents over-ordering sweets
  the week after Diwali);
* forward  - if the coming lead+review horizon overlaps a festival, the
  baseline is multiplied up.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path


@dataclass(frozen=True)
class Festival:
    name: str
    start: date
    end: date
    peak: date
    uplift: dict[str, float] = field(default_factory=dict)

    def contains(self, d: date) -> bool:
        return self.start <= d <= self.end

    def factor(self, category: str, d: date) -> float:
        """Triangular profile: full uplift at peak, tapering to 35% of it at the edges."""
        u = self.uplift.get(category)
        if u is None or not self.contains(d):
            return 1.0
        half = max((self.peak - self.start).days, (self.end - self.peak).days, 1)
        shape = max(0.35, 1.0 - abs((d - self.peak).days) / half)
        return 1.0 + (u - 1.0) * shape


class FestivalCalendar:
    def __init__(self, festivals: list[Festival], note: str = ""):
        self.festivals = sorted(festivals, key=lambda f: f.start)
        self.note = note

    @classmethod
    def load(cls, path: Path) -> "FestivalCalendar":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        fests = [
            Festival(
                name=f["name"],
                start=date.fromisoformat(f["start"]),
                end=date.fromisoformat(f["end"]),
                peak=date.fromisoformat(f.get("peak", f["start"])),
                uplift={k: float(v) for k, v in f.get("uplift", {}).items()},
            )
            for f in raw.get("festivals", [])
        ]
        return cls(fests, raw.get("note", ""))

    @classmethod
    def empty(cls) -> "FestivalCalendar":
        return cls([])

    def affects(self, category: str) -> bool:
        return any(category in f.uplift for f in self.festivals)

    def is_festival_day(self, category: str, d: date) -> bool:
        return any(category in f.uplift and f.contains(d) for f in self.festivals)

    def festival_names(self, category: str, start: date, end: date) -> list[str]:
        return [
            f.name for f in self.festivals
            if category in f.uplift and f.start <= end and f.end >= start
        ]

    def factor(self, category: str, d: date) -> float:
        m = 1.0
        for f in self.festivals:
            m = max(m, f.factor(category, d))
        return m

    def forward_multiplier(self, category: str, start: date, end: date) -> tuple[float, list[str]]:
        """Average demand multiplier over [start, end] (inclusive)."""
        if end < start:
            return 1.0, []
        days = (end - start).days + 1
        total = sum(self.factor(category, start + timedelta(days=i)) for i in range(days))
        mult = total / days
        names = self.festival_names(category, start, end) if mult > 1.0 else []
        return round(mult, 4), names

    def as_dict(self) -> dict:
        return {
            "note": self.note,
            "festivals": [
                {"name": f.name, "start": f.start.isoformat(), "end": f.end.isoformat(),
                 "peak": f.peak.isoformat(), "uplift": f.uplift}
                for f in self.festivals
            ],
        }
