"""A small natural-language filter parser.

Apple Notes hands the search string to the private NLP.framework (``NLSearchParserCreate``)
when ``performNLSearch`` is on. That parser is not portable, so this module is our own
design: it recognises a few date phrases and filters and strips them from the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}


@dataclass
class Filters:
    modified_after: datetime | None = None
    modified_before: datetime | None = None
    folder: str | None = None
    tags: list[str] = field(default_factory=list)
    has_attachments: bool = False

    def describe(self) -> str:
        parts = []
        if self.modified_after:
            parts.append(f"modified >= {self.modified_after:%Y-%m-%d}")
        if self.modified_before:
            parts.append(f"modified < {self.modified_before:%Y-%m-%d}")
        if self.folder:
            parts.append(f"folder ~ {self.folder!r}")
        parts += [f"#{t}" for t in self.tags]
        if self.has_attachments:
            parts.append("has attachments")
        return ", ".join(parts)

    def active(self) -> bool:
        return bool(self.modified_after or self.modified_before or self.folder
                    or self.tags or self.has_attachments)


def _day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def parse(text: str, now: datetime | None = None) -> tuple[str, Filters]:
    """Return (remaining search text, filters)."""
    now = now or datetime.now(timezone.utc)
    f = Filters()

    def take(pattern: str, handler) -> None:
        nonlocal text
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            handler(m)
            text = (text[: m.start()] + " " + text[m.end():]).strip()

    take(r'\bfolder:(?:"([^"]+)"|(\S+))', lambda m: setattr(f, "folder", m.group(1) or m.group(2)))
    for tag in re.findall(r"(?<!\w)#(\w+)", text):
        f.tags.append(tag.lower())
    text = re.sub(r"(?<!\w)#\w+", " ", text)
    take(r"\bwith attachments?\b", lambda m: setattr(f, "has_attachments", True))

    # Calendar periods, measured against Notes.app: "last month" on 13 Oct matched notes from
    # 1 Sep onward (start of the previous month until now); "from july" matched July only.
    local_now = now.astimezone()
    today = _day(local_now)

    def rng(start: datetime, end: datetime | None = None):
        f.modified_after = start.astimezone(timezone.utc)
        f.modified_before = end.astimezone(timezone.utc) if end else None

    def month_start(d: datetime, back: int = 0) -> datetime:
        y, m = d.year, d.month - back
        while m <= 0:
            y, m = y - 1, m + 12
        return d.replace(year=y, month=m, day=1, hour=0, minute=0, second=0, microsecond=0)

    week_start = today - timedelta(days=today.weekday())
    take(r"\btoday\b", lambda m: rng(today))
    take(r"\byesterday\b", lambda m: rng(today - timedelta(days=1), today))
    take(r"\bthis week\b", lambda m: rng(week_start))
    take(r"\blast week\b", lambda m: rng(week_start - timedelta(days=7)))
    take(r"\bthis month\b", lambda m: rng(month_start(today)))
    take(r"\blast month\b", lambda m: rng(month_start(today, 1)))
    take(r"\bthis year\b", lambda m: rng(today.replace(month=1, day=1)))
    take(r"\blast year\b", lambda m: rng(today.replace(year=today.year - 1, month=1, day=1)))
    take(r"\b(?:last|past) (\d+) days\b", lambda m: rng(local_now - timedelta(days=int(m.group(1)))))

    def month(m: re.Match) -> None:
        mo = MONTHS[m.group(1).lower()]
        year = int(m.group(2)) if m.group(2) else (local_now.year if mo <= local_now.month else local_now.year - 1)
        start = today.replace(year=year, month=mo, day=1)
        end = month_start(start.replace(day=28) + timedelta(days=4))
        rng(start, end)

    take(r"\b(?:in|from)\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?\b", month)
    if f.active():
        text = re.sub(r"\b(?:notes?|from|in|modified|edited|created)\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(), f
