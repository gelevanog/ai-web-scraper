"""Record-set checks after extraction: identity keys, cross-page dedupe, sparse non-records and numeric anomalies."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from sift.schema.model import FieldType, Schema
from sift.validate.normalize import money_amount


def norm_key(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("amount")
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def record_key(record: dict[str, Any], schema: Schema) -> str:
    return " | ".join(norm_key(record.get(name)) for name in schema.key_fields())


@dataclass
class DedupeResult:
    records: list[dict[str, Any]]
    duplicates: int = 0
    merged_keys: list[str] = field(default_factory=list)


def dedupe(records: list[dict[str, Any]], schema: Schema, *, key: str = "_key") -> DedupeResult:
    """Keeps the first occurrence of each key; later copies fill fields the first one left empty and add their
    provenance, so a product shown in a "featured" block and again on page 3 becomes one record."""
    seen: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    duplicates = 0
    merged: list[str] = []
    for record in records:
        k = record.get(key) or record_key(record.get("data", record), schema)
        if not k.strip(" |"):
            out.append(record)
            continue
        if k in seen:
            duplicates += 1
            merged.append(k)
            first = seen[k]
            data_first = first.get("data", first)
            data_new = record.get("data", record)
            for name in schema.names:
                if data_first.get(name) in (None, "", []) and data_new.get(name) not in (None, "", []):
                    data_first[name] = data_new[name]
            if "provenance" in first and "provenance" in record:
                first.setdefault("also_seen_at", []).append(record["provenance"].get("url"))
            continue
        seen[k] = record
        out.append(record)
    return DedupeResult(out, duplicates, merged)


def fill_ratio(record: dict[str, Any], schema: Schema) -> float:
    filled = sum(1 for name in schema.names if record.get(name) not in (None, "", []))
    return filled / max(len(schema.names), 1)


def is_sparse(record: dict[str, Any], schema: Schema, typical: float) -> bool:
    """A "record" that fills much less than its siblings (a totals row, a header row repeated in the body)."""
    required_missing = any(record.get(f.name) in (None, "", []) for f in schema.fields if f.required)
    ratio = fill_ratio(record, schema)
    return required_missing or (typical >= 0.6 and ratio <= typical * 0.5)


@dataclass
class Anomaly:
    index: int
    field: str
    value: float
    median: float
    reason: str


def anomalies(records: list[dict[str, Any]], schema: Schema, *, factor: float = 20.0) -> list[Anomaly]:
    """Numeric values more than `factor` times the field's median (or less than 1/factor of it): the classic misplaced
    decimal point or a price read in cents. Flagged for review, not changed."""
    found: list[Anomaly] = []
    for spec in schema.fields:
        if spec.type not in {FieldType.money, FieldType.number, FieldType.integer}:
            continue
        values: list[tuple[int, float]] = []
        for i, record in enumerate(records):
            raw = record.get(spec.name)
            number = (
                money_amount(raw)
                if spec.type == FieldType.money
                else (float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None)
            )
            if number is not None and number > 0:
                values.append((i, number))
        if len(values) < 5:
            continue
        median = statistics.median(v for _, v in values)
        if median <= 0:
            continue
        high = [(i, n) for i, n in values if n > median * factor]
        low = [(i, n) for i, n in values if n < median / factor]
        # Isolated outliers only: when many values sit far from the median the field is bimodal (rents and sale
        # prices in one list), not wrong.
        limit = max(1, len(values) // 20)
        if len(high) <= limit:
            found.extend(
                Anomaly(i, spec.name, n, median, f"{n:g} is {n / median:.0f}x the median {median:g}") for i, n in high
            )
        if len(low) <= limit:
            found.extend(
                Anomaly(i, spec.name, n, median, f"{n:g} is under 1/{factor:g} of the median {median:g}")
                for i, n in low
            )
    return found
