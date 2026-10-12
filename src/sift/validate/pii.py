"""The PII guard: personal email addresses and phone numbers do not leave Sift unless the user asked for business
contact details.

* In every text field (and list of strings), email addresses and phone numbers are removed ("[email removed]").
* A field the schema marks `contact: business` (a shop's phone, a listing agent's office line) keeps phone numbers
  and role or company addresses on the site's own domain; an address on another domain (personal webmail) is still
  removed, because a business contact published by a business uses the business's domain.
* Fields whose whole value was personal data become empty.

This is a guard against collecting personal data by accident, not a GDPR compliance tool: names of private persons
in free text are not detected (see README > Limits)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sift.schema.model import FieldType, Schema

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(
    r"(?<![\w/])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{2,4}[\s.-]\d{3,4}(?:[\s.-]\d{2,4})?(?![\w/])"
)
ROLE_LOCALS = {
    "info",
    "hello",
    "contact",
    "sales",
    "support",
    "office",
    "bookings",
    "booking",
    "lettings",
    "jobs",
    "careers",
    "press",
    "team",
    "admin",
    "enquiries",
    "reservations",
    "help",
}


@dataclass
class PiiReport:
    removed: int = 0
    kept_business: int = 0
    samples: list[str] = field(default_factory=list)
    """Field names where something was removed (never the values)."""


def _digits(text: str) -> int:
    return len(re.sub(r"\D", "", text))


def _clean(text: str, *, business: bool, site_host: str, report: PiiReport, field_name: str) -> str | None:
    def email(match: re.Match[str]) -> str:
        address = match.group(0)
        local, _, domain = address.lower().partition("@")
        same_site = (
            domain == site_host
            or site_host.endswith("." + domain)
            or domain.endswith("." + site_host.removeprefix("www."))
        )
        if business and (same_site or (local in ROLE_LOCALS and same_site)):
            report.kept_business += 1
            return address
        report.removed += 1
        report.samples.append(field_name)
        return "[email removed]"

    def phone(match: re.Match[str]) -> str:
        number = match.group(0)
        if _digits(number) < 7:
            return number
        if business:
            report.kept_business += 1
            return number
        report.removed += 1
        report.samples.append(field_name)
        return "[phone removed]"

    out = EMAIL.sub(email, text)
    out = PHONE.sub(phone, out)
    stripped = re.sub(r"\[(email|phone) removed\]", "", out)
    if not re.search(r"[A-Za-z0-9]{2,}", stripped):
        return None
    return out


def guard_record(record: dict[str, Any], schema: Schema, *, site_host: str, report: PiiReport) -> dict[str, Any]:
    out = dict(record)
    for spec in schema.fields:
        value = out.get(spec.name)
        business = spec.contact == "business"
        if isinstance(value, str) and spec.type in {FieldType.string, FieldType.enum}:
            out[spec.name] = _clean(value, business=business, site_host=site_host, report=report, field_name=spec.name)
        elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            cleaned = [
                _clean(v, business=business, site_host=site_host, report=report, field_name=spec.name) for v in value
            ]
            out[spec.name] = [v for v in cleaned if v]
    return out


def contains_pii(value: Any, planted: list[str]) -> list[str]:
    """Which of the planted strings (or their digits, for phones) appear anywhere in `value` (evaluation helper)."""
    blob = str(value)
    digits_blob = re.sub(r"\D", "", blob)
    found = []
    for item in planted:
        digits = re.sub(r"\D", "", item)[-10:]
        if ("@" in item and item.lower() in blob.lower()) or ("@" not in item and digits and digits in digits_blob):
            found.append(item)
    return found
