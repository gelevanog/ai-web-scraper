"""Turns raw values (strings a selector or a model read from the page) into typed values, field by field, in code.

Each field gets a value, a confidence and notes. Confidence is mechanical, not a model's opinion: a value read by an
unambiguous selector and normalized cleanly scores high; a value that needed a fuzzy enum match, an assumed currency
or that is not present on the page at all scores lower."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from sift.normalize.dates import parse_date
from sift.normalize.money import FxTable, parse_money
from sift.normalize.text import clean_string, match_enum, normalize_phone, parse_bool
from sift.normalize.units import parse_integer, parse_number, parse_quantity
from sift.schema.model import FieldSpec, FieldType, Schema


@dataclass
class Context:
    page_url: str
    today: dt.date
    fx: FxTable
    day_first: bool | None = None
    default_currency: str | None = None


@dataclass
class FieldResult:
    value: Any
    confidence: float
    raw: Any = None
    notes: list[str] = field(default_factory=list)


def _money(raw: Any, spec: FieldSpec, ctx: Context) -> FieldResult:
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        raw = str(raw)
    parsed = parse_money(raw if isinstance(raw, str) else None, ctx.default_currency)
    if parsed is None:
        return FieldResult(None, 0.0, raw, ["not a price"] if raw else [])
    target = spec.currency or "USD"
    notes: list[str] = []
    confidence = 1.0
    source = parsed.currency
    if source is None:
        source = target
        confidence = 0.6
        notes.append(f"currency not shown; assumed {target}")
    converted = ctx.fx.convert(parsed.amount, source, target)
    if converted is None:
        return FieldResult(None, 0.0, raw, [f"no exchange rate for {source}"])
    if source != target:
        notes.append(f"converted {parsed.amount:g} {source} at {ctx.fx.rates.get(source)}")
    value = {"amount": converted, "currency": target}
    if source != target:
        value.update({"source_amount": parsed.amount, "source_currency": source})
    return FieldResult(value, confidence, raw, notes)


def normalize_value(raw: Any, spec: FieldSpec, ctx: Context) -> FieldResult:
    if raw is None or raw == "" or raw == []:
        return FieldResult(None, 0.0, raw)
    kind = spec.type
    if kind == FieldType.list:
        items = raw if isinstance(raw, list) else [x for x in re.split(r"\s*[,;|•·]\s*", str(raw)) if x]
        assert spec.items is not None
        values: list[Any] = []
        confidences: list[float] = []
        for item in items:
            result = normalize_value(item, spec.items, ctx)
            if result.value is not None and result.value not in values:
                values.append(result.value)
                confidences.append(result.confidence)
        conf = min(confidences) if confidences else 0.0
        return FieldResult(values, conf, raw)
    if kind == FieldType.object:
        if not isinstance(raw, dict):
            return FieldResult(None, 0.0, raw, ["expected an object"])
        obj: dict[str, Any] = {}
        confs: list[float] = []
        for child in spec.fields or []:
            result = normalize_value(raw.get(child.name), child, ctx)
            obj[child.name] = result.value
            if result.value is not None:
                confs.append(result.confidence)
        return FieldResult(obj, min(confs) if confs else 0.0, raw)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
        if raw is None:
            return FieldResult(None, 0.0, raw)
    if kind == FieldType.money:
        return _money(raw, spec, ctx)
    if kind == FieldType.boolean:
        value = parse_bool(raw)
        return FieldResult(value, 1.0 if value is not None else 0.0, raw)
    if kind == FieldType.integer:
        number = raw if isinstance(raw, int) and not isinstance(raw, bool) else parse_integer(str(raw))
        return FieldResult(number, 1.0 if number is not None else 0.0, raw)
    if kind == FieldType.number:
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            return FieldResult(round(float(raw), 2), 1.0, raw)
        value_f = parse_quantity(str(raw), spec.unit) if spec.unit else parse_number(str(raw))
        if value_f is not None and not spec.unit:
            value_f = round(value_f, 2)
        return FieldResult(value_f, 1.0 if value_f is not None else 0.0, raw)
    if kind == FieldType.date or kind == FieldType.datetime:
        parsed = parse_date(str(raw), ctx.today, day_first=ctx.day_first)
        if parsed is None:
            return FieldResult(None, 0.0, raw, ["not a date"])
        relative = bool(re.search(r"(?i)\bago\b|today|yesterday|tomorrow|\bthis\b|\bnext\b|\bin \d", str(raw)))
        return FieldResult(parsed.isoformat(), 0.9 if relative else 1.0, raw, [f"relative date resolved against {ctx.today}"] if relative else [])
    if kind == FieldType.url:
        text = str(raw).strip()
        if text.startswith(("javascript:", "data:", "#", "mailto:")):
            return FieldResult(None, 0.0, raw, ["not a URL"])
        from urllib.parse import urljoin

        return FieldResult(urljoin(ctx.page_url, text), 1.0, raw)
    if kind == FieldType.enum:
        value_e, conf = match_enum(raw, list(spec.enum or []))
        return FieldResult(value_e, conf, raw, [] if conf >= 0.9 or value_e is None else ["fuzzy enum match"])
    text_value = clean_string(str(raw))
    if text_value and re.search(r"phone|tel|fax|mobile", spec.name):
        phone = normalize_phone(text_value)
        if phone:
            return FieldResult(phone, 1.0, raw)
    return FieldResult(text_value, 1.0 if text_value else 0.0, raw)


def normalize_record(raw: dict[str, Any], schema: Schema, ctx: Context) -> dict[str, FieldResult]:
    """Only schema fields survive: anything else a model or a page adds is dropped here."""
    return {spec.name: normalize_value(raw.get(spec.name), spec, ctx) for spec in schema.fields}


def money_amount(value: Any) -> float | None:
    if isinstance(value, dict) and isinstance(value.get("amount"), int | float):
        return float(value["amount"])
    return None
