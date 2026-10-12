"""Date parsing in code: absolute formats in several locales and relative dates resolved against the crawl date.

"3 days ago", "Posted yesterday", "3d ago", "2w ago", "Today", "Tomorrow, 19:30", "in 5 days", "this Saturday",
"Sat 10 Oct" (year inferred: the nearest such date to the crawl date), "October 14, 2026", "14 Oct 2026",
"14.10.2026", "2026-10-14", "10/14/2026" (month first unless the page's locale is day-first or the day is > 12)."""

from __future__ import annotations

import datetime as dt
import re

from dateutil import parser as dateutil_parser

_UNITS = {
    "minute": 0,
    "min": 0,
    "m": 0,
    "hour": 0,
    "hr": 0,
    "h": 0,
    "day": 1,
    "d": 1,
    "week": 7,
    "wk": 7,
    "w": 7,
    "month": 30,
    "mo": 30,
    "year": 365,
    "yr": 365,
    "y": 365,
}
_AGO = re.compile(
    r"\b(\d+|an?|one|two|three|four|five|six|seven)\s*(minutes?|mins?|hours?|hrs?|days?|weeks?|wks?|months?|mos?|years?|yrs?|[hdwmy])\.?\s+ago\b",
    re.I,
)
_IN = re.compile(r"\bin\s+(\d+|an?|one|two|three|four|five|six|seven)\s+(days?|weeks?|months?)\b", re.I)
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january", "januar", "janvier"),
            ("feb", "february", "februar", "février", "fevrier"),
            ("mar", "march", "märz", "maerz", "mars"),
            ("apr", "april", "avril"),
            ("may", "mai"),
            ("jun", "june", "juni", "juin"),
            ("jul", "july", "juli", "juillet"),
            ("aug", "august", "août", "aout"),
            ("sep", "sept", "september", "septembre"),
            ("oct", "october", "oktober", "octobre"),
            ("nov", "november", "novembre"),
            ("dec", "december", "dezember", "décembre", "decembre"),
        ],
        start=1,
    )
    for m in names
}
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})([./-])(\d{1,2})\2(\d{4}|\d{2})\b")
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\.?\s+([A-Za-zéûä]{3,10})\.?(?:,?\s+(\d{4}))?\b")
_MONTH_DAY = re.compile(r"\b([A-Za-zéûä]{3,10})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b")


def _count(token: str) -> int:
    return int(token) if token.isdigit() else _WORDS.get(token.lower(), 1)


def _unit_days(unit: str) -> int:
    unit = unit.lower().rstrip(".")
    for key in sorted(_UNITS, key=len, reverse=True):
        if unit.startswith(key) and (len(key) > 1 or unit == key):
            return _UNITS[key]
    return 1


def _nearest_year(month: int, day: int, today: dt.date) -> dt.date | None:
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidates.append(dt.date(year, month, day))
        except ValueError:
            continue
    return min(candidates, key=lambda d: abs((d - today).days)) if candidates else None


def parse_date(text: str | None, today: dt.date, *, day_first: bool | None = None) -> dt.date | None:
    if not text:
        return None
    raw = " ".join(str(text).split())
    low = raw.lower()
    if match := _ISO.search(raw):
        try:
            return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    if match := _AGO.search(raw):
        return today - dt.timedelta(days=_count(match.group(1)) * _unit_days(match.group(2)))
    if match := _IN.search(raw):
        return today + dt.timedelta(days=_count(match.group(1)) * _unit_days(match.group(2)))
    if re.search(r"\b(just now|today|tonight|this evening)\b", low):
        return today
    if re.search(r"\byesterday\b", low):
        return today - dt.timedelta(days=1)
    if re.search(r"\btomorrow\b", low):
        return today + dt.timedelta(days=1)
    if match := re.search(r"\b(this|next|coming)\s+(" + "|".join(_WEEKDAYS) + r")\b", low):
        target = _WEEKDAYS.index(match.group(2))
        delta = (target - today.weekday()) % 7
        if match.group(1) == "next" and delta == 0:
            delta = 7
        return today + dt.timedelta(days=delta)
    if match := _NUMERIC.search(raw):
        a, b, year_s = int(match.group(1)), int(match.group(3)), match.group(4)
        year = int(year_s) + (2000 if len(year_s) == 2 else 0)
        dot = match.group(2) == "."
        first_is_day = day_first if day_first is not None else (dot or a > 12)
        if a > 12:
            first_is_day = True
        elif b > 12:
            first_is_day = False
        day, month = (a, b) if first_is_day else (b, a)
        try:
            return dt.date(year, month, day)
        except ValueError:
            return None
    for pattern, day_group, month_group in ((_DAY_MONTH, 1, 2), (_MONTH_DAY, 2, 1)):
        for match in pattern.finditer(raw):
            month_num = _MONTHS.get(match.group(month_group).lower())
            if not month_num:
                continue
            day = int(match.group(day_group))
            if match.group(3):
                try:
                    return dt.date(int(match.group(3)), month_num, day)
                except ValueError:
                    return None
            return _nearest_year(month_num, day, today)
    try:
        parsed = dateutil_parser.parse(raw, fuzzy=True, dayfirst=bool(day_first), default=dt.datetime(today.year, 1, 1))
    except (ValueError, OverflowError):
        return None
    if not re.search(r"\d", raw):
        return None
    return parsed.date()


def day_first_for_locale(lang: str | None) -> bool | None:
    """`<html lang="en-US">` -> False; en-GB, de, fr, ... -> True; unknown -> None (decide per value)."""
    if not lang:
        return None
    lang = lang.lower()
    return not (lang in {"en-us", "en_us", "en-ca"} or lang == "en")
