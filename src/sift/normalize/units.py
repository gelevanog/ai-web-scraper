"""Numbers and units: "1,200 sq ft" -> 111.48 sqm, "85 m²" -> 85, "2.5 kg" -> 2.5 kg, "5 lb" -> 2.27 kg, "4.5 out of 5"
-> 4.5, "★★★★☆" -> 4, "1.234,5" -> 1234.5. Unit conversion is a table in code, not a model's arithmetic."""

from __future__ import annotations

import re

from sift.normalize.money import parse_number_token

# unit alias -> (canonical unit, factor to canonical)
_UNITS: dict[str, tuple[str, float]] = {
    "sqm": ("sqm", 1.0),
    "m²": ("sqm", 1.0),
    "m2": ("sqm", 1.0),
    "sq m": ("sqm", 1.0),
    "square meters": ("sqm", 1.0),
    "square metres": ("sqm", 1.0),
    "sq ft": ("sqm", 0.09290304),
    "sqft": ("sqm", 0.09290304),
    "ft²": ("sqm", 0.09290304),
    "ft2": ("sqm", 0.09290304),
    "square feet": ("sqm", 0.09290304),
    "kg": ("kg", 1.0),
    "g": ("kg", 0.001),
    "lb": ("kg", 0.45359237),
    "lbs": ("kg", 0.45359237),
    "oz": ("kg", 0.028349523125),
    "km": ("km", 1.0),
    "mi": ("km", 1.609344),
    "miles": ("km", 1.609344),
    "m": ("km", 0.001),
    "l": ("l", 1.0),
    "ml": ("l", 0.001),
    "gal": ("l", 3.785411784),
    "cm": ("cm", 1.0),
    "mm": ("cm", 0.1),
    "in": ("cm", 2.54),
    "inch": ("cm", 2.54),
    "inches": ("cm", 2.54),
}
_CANONICAL = {"sqm": "sqm", "m2": "sqm", "kg": "kg", "km": "km", "l": "l", "cm": "cm"}
_NUM = re.compile(r"[-+]?(?:\d{1,3}(?:[.,'   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)")
_UNIT_RE = re.compile(
    r"([-+]?(?:\d{1,3}(?:[.,'   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?))\s*("
    + "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
    + r")(?![a-zA-Z])",
    re.I,
)
_WORD_NUMBERS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def parse_number(text: str | None) -> float | None:
    if text is None:
        return None
    raw = " ".join(str(text).split())
    if not raw:
        return None
    stars = raw.count("★")
    if stars and not re.search(r"\d", raw):
        return float(stars) + (0.5 if "½" in raw or "⯪" in raw else 0.0)
    word = re.fullmatch(r"(?i)\s*(zero|one|two|three|four|five|six|seven|eight|nine|ten)\b.*", raw)
    if word and not re.search(r"\d", raw):
        return float(_WORD_NUMBERS[word.group(1).lower()])
    match = _NUM.search(raw)
    if not match:
        return None
    token = match.group(0)
    sign = -1.0 if token.startswith("-") else 1.0
    try:
        return sign * parse_number_token(token.lstrip("+-"))
    except ValueError:
        return None


def parse_integer(text: str | None) -> int | None:
    if text is not None and re.search(r"(?i)\b(unlimited|no limit|any number)\b", str(text)):
        return None
    value = parse_number(text)
    return None if value is None else int(round(value))


def canonical_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    unit = unit.strip().lower()
    if unit in _CANONICAL:
        return _CANONICAL[unit]
    return _UNITS[unit][0] if unit in _UNITS else unit


def parse_quantity(text: str | None, target_unit: str | None) -> float | None:
    """A number converted to `target_unit` when the text names a convertible unit; else the bare number."""
    if text is None:
        return None
    raw = " ".join(str(text).split())
    target = canonical_unit(target_unit)
    match = _UNIT_RE.search(raw)
    if match and target:
        canonical, factor = _UNITS[match.group(2).lower()]
        if canonical == target:
            value = parse_number_token(match.group(1).lstrip("+-")) * factor
            return round(value, 2)
    value = parse_number(raw)
    return None if value is None else round(value, 2)
