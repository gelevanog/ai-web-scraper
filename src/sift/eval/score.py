"""Scoring extracted records against ground truth.

Records are matched by their key field (normalized: case, punctuation and whitespace ignored). A field is correct
when its normalized value equals the truth: money within one cent of the USD amount, numbers within 0.1%, dates as
ISO strings, URLs after canonicalization, strings case- and punctuation-insensitively, lists as sets. A missing value
where the truth has one is wrong, and so is a value where the truth is empty."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sift.crawl.urls import canonicalize
from sift.schema.model import FieldSpec, FieldType, Schema
from sift.validate.quality import norm_key

UNSCORED = {"contact"}
"""Fields scored separately (personal data removed by the PII guard is checked by the PII trap, not here)."""


def _amount(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("amount")
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def field_equal(spec: FieldSpec, got: Any, want: Any) -> bool:
    empty_got = got in (None, "", [])
    empty_want = want in (None, "", [])
    if empty_got or empty_want:
        return empty_got and empty_want
    kind = spec.type
    if kind == FieldType.money:
        a, b = _amount(got), _amount(want)
        return a is not None and b is not None and abs(a - b) <= 0.011
    if kind in {FieldType.number, FieldType.integer}:
        try:
            a, b = float(got), float(want)
        except (TypeError, ValueError):
            return False
        return abs(a - b) <= max(0.011, abs(b) * 0.001)
    if kind == FieldType.url:
        return canonicalize(str(got)) == canonicalize(str(want))
    if kind == FieldType.list:
        if not isinstance(got, list) or not isinstance(want, list):
            return False
        if spec.items is not None and spec.items.type == FieldType.object:

            def norm_obj(obj: Any) -> tuple[tuple[str, str], ...]:
                if not isinstance(obj, dict):
                    return ()
                return tuple(sorted((k, norm_key(v) if not isinstance(v, bool) else str(v)) for k, v in obj.items()))

            return sorted(map(norm_obj, got)) == sorted(map(norm_obj, want))
        return sorted({norm_key(v) for v in got}) == sorted({norm_key(v) for v in want})
    if kind == FieldType.boolean:
        return bool(got) == bool(want)
    if kind in {FieldType.date, FieldType.datetime, FieldType.enum}:
        return str(got)[:10] == str(want)[:10] if kind != FieldType.enum else str(got) == str(want)
    return norm_key(got) == norm_key(want)


@dataclass
class SiteScore:
    site: str
    truth: int
    extracted: int
    matched: int
    fields: dict[str, list[int]] = field(default_factory=dict)
    """field -> [correct, total]"""
    unmatched_extracted: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.matched / self.extracted if self.extracted else 0.0

    @property
    def recall(self) -> float:
        return self.matched / self.truth if self.truth else 0.0

    @property
    def field_accuracy(self) -> float:
        correct = sum(c for c, _ in self.fields.values())
        total = sum(t for _, t in self.fields.values())
        return correct / total if total else 0.0

    @property
    def correct(self) -> int:
        return sum(c for c, _ in self.fields.values())

    @property
    def total(self) -> int:
        return sum(t for _, t in self.fields.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "truth": self.truth,
            "extracted": self.extracted,
            "matched": self.matched,
            "precision": round(self.precision, 4),
            "recall": self.recall and round(self.recall, 4),
            "field_accuracy": round(self.field_accuracy, 4),
            "fields": {k: {"correct": c, "total": t} for k, (c, t) in self.fields.items()},
            "unmatched_extracted": self.unmatched_extracted[:10],
            "missed": self.missed[:10],
            "errors": self.errors[:25],
        }


def score_site(site: str, schema: Schema, truth: list[dict[str, Any]], extracted: list[dict[str, Any]]) -> SiteScore:
    key = schema.key_fields()[0]
    by_key: dict[str, dict[str, Any]] = {}
    for record in extracted:
        k = norm_key(record.get(key))
        if k and k not in by_key:
            by_key[k] = record
    result = SiteScore(site, len(truth), len(by_key), 0)
    truth_keys = set()
    for want in truth:
        k = norm_key(want.get(key))
        truth_keys.add(k)
        got = by_key.get(k)
        if got is None:
            result.missed.append(str(want.get(key)))
            continue
        result.matched += 1
        for spec in schema.fields:
            if spec.name in UNSCORED or spec.name not in want:
                continue
            ok = field_equal(spec, got.get(spec.name), want.get(spec.name))
            stats = result.fields.setdefault(spec.name, [0, 0])
            stats[0] += int(ok)
            stats[1] += 1
            if not ok:
                result.errors.append(
                    {"key": want.get(key), "field": spec.name, "got": got.get(spec.name), "want": want.get(spec.name)}
                )
    result.unmatched_extracted = [str(r.get(key)) for k, r in by_key.items() if k not in truth_keys]
    return result


def aggregate(scores: list[SiteScore]) -> dict[str, Any]:
    truth = sum(s.truth for s in scores)
    extracted = sum(s.extracted for s in scores)
    matched = sum(s.matched for s in scores)
    correct = sum(s.correct for s in scores)
    total = sum(s.total for s in scores)
    return {
        "sites": len(scores),
        "truth_records": truth,
        "extracted_records": extracted,
        "matched": matched,
        "precision": round(matched / extracted, 4) if extracted else 0.0,
        "recall": round(matched / truth, 4) if truth else 0.0,
        "field_accuracy": round(correct / total, 4) if total else 0.0,
        "fields_correct": correct,
        "fields_total": total,
    }
