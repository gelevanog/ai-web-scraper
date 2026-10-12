"""Money parsing and currency conversion, in code, not in the model.

Handles the formats real pages use: "$1,299.00", "1.299,00 €", "1 299,00 €", "€1,299", "CHF 1'299.–", "¥12,800",
"C$49.99", "EUR 12.50", "12.50 USD", "$85k", "Free", "Was $1,499 Now $1,299", and ranges "$85k–$110k". A value whose
currency cannot be determined keeps `currency=None` and is converted only if the job declares a default currency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "CHF": 1.13,
    "JPY": 0.0067,
    "CAD": 0.73,
    "AUD": 0.66,
    "SEK": 0.095,
    "PLN": 0.25,
}

# Longest first so "US$" wins over "$" and "CA$" over "A$".
_SYMBOLS: list[tuple[str, str]] = sorted(
    [
        ("US$", "USD"),
        ("USD", "USD"),
        ("CA$", "CAD"),
        ("C$", "CAD"),
        ("AU$", "AUD"),
        ("A$", "AUD"),
        ("$", "USD"),
        ("€", "EUR"),
        ("£", "GBP"),
        ("¥", "JPY"),
        ("円", "JPY"),
        ("zł", "PLN"),
        ("Fr.", "CHF"),
    ],
    key=lambda pair: -len(pair[0]),
)
_CODES = {"USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "PLN", "NOK", "DKK", "CNY", "INR"}
_CODE_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(_CODES)) + r")(?![A-Za-z])")
_FREE = re.compile(r"\b(free|gratis|kostenlos|gratuit|no charge|complimentary|free entry|free admission)\b", re.I)
_UNPRICED = re.compile(r"\b(contact|call|on request|poa|quote|tbd|tba|custom pricing|let's talk)\b", re.I)
_NUMBER = re.compile(r"(?<![\w])(\d{1,3}(?:[.,'’   ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?:\s?([kKmM])(?![a-zA-Z]))?")
_NOW = re.compile(r"\b(now|sale|today|special|offer)\b", re.I)


@dataclass(frozen=True)
class Money:
    amount: float
    currency: str | None
    raw: str


@dataclass(frozen=True)
class FxTable:
    rates: dict[str, float]
    as_of: str = ""

    def convert(self, amount: float, source: str, target: str) -> float | None:
        if source == target:
            return round(amount, 2)
        if source not in self.rates or target not in self.rates:
            return None
        return round(amount * self.rates[source] / self.rates[target], 2)


@lru_cache(maxsize=8)
def load_fx(path: Path | None = None) -> FxTable:
    if path is not None and path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rates = {str(k).upper(): float(v) for k, v in (data.get("rates") or {}).items()}
        return FxTable(rates=rates or dict(DEFAULT_RATES), as_of=str(data.get("as_of", "")))
    return FxTable(rates=dict(DEFAULT_RATES))


def detect_currency(text: str) -> str | None:
    match = _CODE_RE.search(text)
    if match:
        return match.group(1)
    for symbol, code in _SYMBOLS:
        if symbol in text:
            return code
    if re.search(r"\bkr\b", text):
        return "SEK"
    return None


def parse_number_token(token: str, suffix: str | None = None) -> float:
    """"1.299,00" -> 1299.0; "1,299.00" -> 1299.0; "1 299,5" -> 1299.5; "1'299" -> 1299.0; "12,50" -> 12.5."""
    cleaned = re.sub(r"[   '’]", "", token)
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        head, _, tail = cleaned.rpartition(",")
        # One comma followed by exactly three digits is a thousands separator ("1,299"); otherwise a decimal comma.
        cleaned = cleaned.replace(",", "") if len(tail) == 3 and cleaned.count(",") >= 1 and head else head.replace(",", "") + "." + tail
    elif "." in cleaned:
        head, _, tail = cleaned.rpartition(".")
        if len(tail) == 3 and (cleaned.count(".") > 1 or len(head) <= 3):
            cleaned = cleaned.replace(".", "")
    value = float(cleaned)
    if suffix and suffix.lower() == "k":
        value *= 1_000
    elif suffix and suffix.lower() == "m":
        value *= 1_000_000
    return value


def _amounts(text: str) -> list[tuple[float, int]]:
    found: list[tuple[float, int]] = []
    for match in _NUMBER.finditer(text):
        try:
            found.append((parse_number_token(match.group(1), match.group(2)), match.start()))
        except ValueError:
            continue
    return found


def parse_money(text: str | None, default_currency: str | None = None) -> Money | None:
    if text is None:
        return None
    raw = " ".join(str(text).split())
    if not raw:
        return None
    currency = detect_currency(raw) or default_currency
    amounts = _amounts(raw)
    if not amounts:
        if _FREE.search(raw):
            return Money(0.0, currency, raw)
        return None
    if _UNPRICED.search(raw) and not re.search(r"[$€£¥]|\b[A-Z]{3}\b", raw):
        return None
    amount = amounts[0][0]
    if len(amounts) > 1:
        now = _NOW.search(raw)
        if now:
            after = [a for a, pos in amounts if pos > now.start()]
            amount = after[0] if after else amounts[-1][0]
    return Money(round(amount, 2), currency, raw)


def parse_money_range(text: str | None, default_currency: str | None = None) -> tuple[Money | None, Money | None]:
    """"$85k–$110k" -> (85000, 110000); "Up to £60,000" -> (None, 60000); "From €40.000" -> (40000, None)."""
    if not text:
        return None, None
    raw = " ".join(str(text).split())
    currency = detect_currency(raw) or default_currency
    amounts = [a for a, _ in _amounts(raw)]
    if not amounts:
        return None, None
    # A "k" on the upper bound applies to a bare lower bound too ("$85–110k").
    if len(amounts) >= 2 and amounts[0] < 1000 <= amounts[1] and re.search(r"\d\s?[kK]\b", raw):
        amounts[0] *= 1000
    if re.search(r"\b(up to|max(imum)?|bis zu|jusqu'à)\b", raw, re.I) and len(amounts) == 1:
        return None, Money(amounts[0], currency, raw)
    if len(amounts) == 1:
        return Money(amounts[0], currency, raw), None if re.search(r"\b(from|ab|à partir de|starting)\b", raw, re.I) else Money(amounts[0], currency, raw)
    return Money(amounts[0], currency, raw), Money(amounts[1], currency, raw)
