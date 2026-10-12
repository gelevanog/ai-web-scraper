"""Strings, booleans, enums and phone numbers."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_FOOTNOTE_TAIL = re.compile(r"[\s]*[¹²³⁴⁵⁶⁷⁸⁹⁰*†‡§]+$")

_TRUE = (
    "yes",
    "true",
    "y",
    "✓",
    "✔",
    "available",
    "in stock",
    "included",
    "remote",
    "100% remote",
    "fully remote",
    "on",
)
_FALSE = (
    "no",
    "false",
    "n",
    "✗",
    "✘",
    "—",
    "-",
    "none",
    "n/a",
    "unavailable",
    "sold out",
    "out of stock",
    "not available",
    "disabled",
    "hybrid",
    "on-site",
    "onsite",
    "in office",
    "in-office",
    "off",
)

# Common page wording for enum values. Applied only when the target value is one of the field's enum values.
ENUM_SYNONYMS: dict[str, tuple[str, ...]] = {
    "in_stock": (
        "in stock",
        "available",
        "ships",
        "ready to ship",
        "available now",
        "in-stock",
        "instock",
        "add to cart",
        "buy now",
    ),
    "out_of_stock": (
        "out of stock",
        "sold out",
        "unavailable",
        "currently unavailable",
        "not available",
        "out-of-stock",
        "outofstock",
        "no stock",
    ),
    "preorder": ("pre-order", "preorder", "pre order", "coming soon", "available for preorder", "backorder"),
    "sale": ("for sale", "buy", "sale", "freehold", "to buy"),
    "rent": ("for rent", "to rent", "to let", "rental", "per month", "pcm", "/ month", "lease", "let"),
    "full_time": ("full-time", "full time", "fulltime", "permanent", "ft"),
    "part_time": ("part-time", "part time", "parttime", "pt"),
    "contract": ("contract", "contractor", "freelance", "temporary", "fixed-term", "fixed term"),
    "internship": ("intern", "internship", "trainee", "apprentice"),
    "music": ("music", "concert", "live music", "gig"),
    "theatre": ("theatre", "theater", "stage", "play"),
    "comedy": ("comedy", "stand-up", "standup"),
    "food": ("food", "food & drink", "drink", "dining", "tasting"),
    "tech": ("tech", "technology", "meetup"),
    "family": ("family", "kids", "kids & family", "children"),
    "art": ("art", "arts", "exhibition", "gallery"),
    "sports": ("sports", "sport", "fitness", "sports & fitness"),
    "vegan": ("vegan", "plant-based"),
    "vegetarian": ("vegetarian", "veggie"),
    "gluten_free": ("gluten free", "gluten-free", "gf", "coeliac"),
    "spicy": ("spicy", "hot", "chilli", "chili"),
    "library": ("library", "libraries"),
    "pool": ("pool", "swimming", "swimming pool", "lido", "baths"),
    "sports_hall": ("sports hall", "sports centre", "gym", "leisure centre"),
    "community_centre": ("community centre", "community center", "community hall", "village hall"),
    "park": ("park", "gardens", "green", "recreation ground"),
}


def clean_string(value: str | None) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)) if any(ch in str(value) for ch in "ﬁﬂ") else str(value)
    text = _WS.sub(" ", text).strip()
    text = _FOOTNOTE_TAIL.sub("", text).strip()
    return text or None


def snake(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if value is None:
        return None
    text = _WS.sub(" ", str(value)).strip().lower()
    if not text:
        return None
    if text in _FALSE or any(text.startswith(f) for f in ("not ", "no ")):
        return False
    if text in _TRUE:
        return True
    for word in _FALSE:
        if len(word) > 3 and word in text:
            return False
    for word in _TRUE:
        if len(word) > 3 and word in text:
            return True
    return None


def match_enum(value: object, allowed: list[str]) -> tuple[str | None, float]:
    """(enum value, confidence). Exact or snake-case match 1.0; a known synonym 0.9; a token of the text 0.7."""
    if value is None:
        return None, 0.0
    text = _WS.sub(" ", str(value)).strip().lower()
    if not text:
        return None, 0.0
    key = snake(text)
    if key in allowed:
        return key, 1.0
    for target in allowed:
        for synonym in ENUM_SYNONYMS.get(target, ()):
            if text == synonym:
                return target, 0.9
    best: tuple[str | None, int] = (None, 0)
    for target in allowed:
        for synonym in (*ENUM_SYNONYMS.get(target, ()), target.replace("_", " ")):
            if (
                len(synonym) >= 3
                and re.search(rf"(?<![a-z]){re.escape(synonym)}(?![a-z])", text)
                and len(synonym) > best[1]
            ):
                best = (target, len(synonym))
    if best[0]:
        return best[0], 0.7
    return None, 0.0


def normalize_phone(text: str | None) -> str | None:
    if not text:
        return None
    raw = str(text)
    if raw.lower().startswith("tel:"):
        raw = raw[4:]
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return None
    return ("+" if raw.strip().startswith("+") or len(digits) > 10 else "") + digits
