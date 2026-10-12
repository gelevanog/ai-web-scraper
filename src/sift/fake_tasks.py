"""Rule-based implementations of every model task, used by the offline `fake` provider (tests, CI, the zero-key
demo) and as the "rules only" baseline. They read the same prompts a real model reads.

* learn_extractor / heal_extractor: heuristic selector induction on the page skeleton (sift.extract.induce).
* learn_detail: label-based rules for detail pages ("SKU" -> the value next to it).
* direct_extract: induction on the cleaned page, or a line parser for PDF text.
* propose_schema: keyword rules over the request.

A *gullible* fake obeys instructions it finds in page text, to exercise the injection guard offline."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from sift.extract.dom import parse
from sift.extract.induce import induce, induce_detail
from sift.extract.program import run_list
from sift.llm.base import Message
from sift.llm.fake import FakeHandler
from sift.schema.model import Schema

_PAGE = re.compile(r"<<<PAGE [0-9a-f]+>>>\n(.*?)\n<<<END PAGE [0-9a-f]+>>>", re.S)
_SCHEMA = re.compile(r"^Schema JSON: (\{.*\})$", re.M)
_INJECTED_ZERO = re.compile(r"(?i)report the (?:price|salary) of every \w+ as ([\d.]+)")


def _user(messages: Sequence[Message]) -> str:
    return "\n".join(m["content"] for m in messages if m["role"] == "user")


def _schema(text: str) -> Schema:
    match = _SCHEMA.search(text)
    if not match:
        raise ValueError("no schema in the prompt")
    return Schema.model_validate(json.loads(match.group(1)))


def _page(text: str) -> str:
    match = _PAGE.search(text)
    return match.group(1) if match else ""


def learn_extractor(messages: Sequence[Message], gullible: bool) -> str:
    text = _user(messages)
    schema = _schema(text)
    doc = parse(_page(text))
    program, examples = induce(doc, schema)
    if program is None:
        return json.dumps({"item": None, "fields": {}, "missing": schema.names})
    data = json.loads(program.model_dump_json(exclude_none=True))
    data["examples"] = examples
    return json.dumps(data)


def learn_detail(messages: Sequence[Message], gullible: bool) -> str:
    text = _user(messages)
    schema = _schema(text)
    program = induce_detail(parse(_page(text)), schema)
    return json.dumps({"fields": json.loads(program.model_dump_json(exclude_none=True))["fields"]})


_PDF_LINE = re.compile(
    r"^(?P<a>.+?)\s*\.{3,}\s*(?P<price>\S*\d[\d.,]*\S*)$|^(?P<price2>\S*\d[\d.,]*\S*)\s{2,}(?P<b>.+)$"
)


def _pdf_records(content: str, schema: Schema) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    section = None
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _PDF_LINE.match(line)
        if not match:
            if len(line) < 30 and not re.search(r"\d", line):
                section = line.title()
            continue
        name = match.group("a") or match.group("b") or ""
        price = match.group("price") or match.group("price2")
        tags: list[str] = []
        paren = re.search(r"\(([^)]*)\)\s*$|\[([^\]]*)\]\s*$", name)
        if paren:
            tags = [t.strip() for t in (paren.group(1) or paren.group(2) or "").split(",") if t.strip()]
            name = name[: paren.start()].strip()
        tag_map = {"VG": "vegan", "V": "vegetarian", "GF": "gluten_free", "S": "spicy"}
        record = {schema.names[0]: name, "price": price}
        if "section" in schema.names:
            record["section"] = section
        if "dietary" in schema.names:
            record["dietary"] = [tag_map.get(t, t) for t in tags]
        records.append(record)
    return records


def direct_extract(messages: Sequence[Message], gullible: bool) -> str:
    text = _user(messages)
    schema = _schema(text)
    content = _page(text)
    if content.lstrip().startswith("<"):
        doc = parse(content)
        program, _ = induce(doc, schema)
        records: list[dict[str, Any]] = []
        if program is not None:
            for raw in run_list(doc, program):
                records.append({k: v.value for k, v in raw.values.items()})
    else:
        records = _pdf_records(content, schema)
    if gullible and (match := _INJECTED_ZERO.search(content)):
        for record in records:
            for name in schema.names:
                if "price" in name or "salary" in name:
                    record[name] = match.group(1)
            record["coupon_code"] = "FREE100"
    return json.dumps({"records": records})


_SCHEMA_RULES: list[tuple[str, dict[str, Any]]] = [
    (r"\bname\b", {"name": "name", "type": "string", "required": True}),
    (r"\btitle\b", {"name": "title", "type": "string", "required": True}),
    (r"\bstreet address\b", {"name": "address", "type": "string", "required": True}),
    (r"\bdish\b", {"name": "dish", "type": "string", "required": True}),
    (r"\bfacility\b", {"name": "facility", "type": "string", "required": True}),
    (r"\bplan\b", {"name": "plan", "type": "string", "required": True}),
    (r"\bmonthly price\b", {"name": "monthly_price", "type": "money"}),
    (r"billed annually", {"name": "annual_monthly_price", "type": "money"}),
    (r"(?<!monthly )(?<!per-month )\bprice\b", {"name": "price", "type": "money"}),
    (r"\brating\b", {"name": "rating", "type": "number"}),
    (
        r"stock status|availability",
        {"name": "stock_status", "type": "enum", "enum": ["in_stock", "out_of_stock", "preorder"]},
    ),
    (r"\bimage\b", {"name": "image_url", "type": "url"}),
    (r"product page", {"name": "product_url", "type": "url"}),
    (r"\bcompany\b", {"name": "company", "type": "string"}),
    (r"\blocation\b", {"name": "location", "type": "string"}),
    (r"salary range", {"name": "salary_min", "type": "money"}),
    (r"salary range", {"name": "salary_max", "type": "money"}),
    (
        r"employment type",
        {"name": "employment_type", "type": "enum", "enum": ["full_time", "part_time", "contract", "internship"]},
    ),
    (r"\bremote\b", {"name": "remote", "type": "boolean"}),
    (r"\bposted\b", {"name": "posted_date", "type": "date"}),
    (r"link to the posting", {"name": "job_url", "type": "url"}),
    (r"\bcity\b", {"name": "city", "type": "string"}),
    (r"sale or for rent", {"name": "listing_type", "type": "enum", "enum": ["sale", "rent"]}),
    (r"\bbedrooms\b", {"name": "bedrooms", "type": "integer"}),
    (r"\bbathrooms\b", {"name": "bathrooms", "type": "number"}),
    (r"floor area", {"name": "area_sqm", "type": "number", "unit": "sqm"}),
    (r"listing link", {"name": "listing_url", "type": "url"}),
    (r"agent's office phone", {"name": "agent_phone", "type": "string", "contact": "business"}),
    (r"\bdate\b", {"name": "date", "type": "date"}),
    (r"\bvenue\b", {"name": "venue", "type": "string"}),
    (r"\bcategory\b", {"name": "category", "type": "string"}),
    (r"link to the event", {"name": "event_url", "type": "url"}),
    (r"\bsection\b", {"name": "section", "type": "string"}),
    (r"dietary labels", {"name": "dietary", "type": "list", "items": {"name": "tag", "type": "string"}}),
    (r"\bseats\b", {"name": "seats", "type": "integer"}),
    (r"\bfeatures\b", {"name": "features", "type": "list", "items": {"name": "feature", "type": "string"}}),
    (r"\bdistrict\b", {"name": "district", "type": "string"}),
    (r"facility type", {"name": "facility_type", "type": "string"}),
    (r"\bcapacity\b", {"name": "capacity", "type": "integer"}),
    (r"opening date", {"name": "opened", "type": "date"}),
    (r"phone line", {"name": "phone", "type": "string"}),
    (r"\bsku\b", {"name": "sku", "type": "string"}),
]


def propose_schema(messages: Sequence[Message], gullible: bool) -> str:
    text = _user(messages)
    request = text.split("User's request:\n", 1)[-1].split("\n\nSample page", 1)[0]
    low = request.lower()
    fields: list[dict[str, Any]] = []
    names: set[str] = set()
    for pattern, spec in _SCHEMA_RULES:
        match = re.search(pattern, low)
        if match and spec["name"] not in names:
            if spec["name"] == "date" and "posted" in low:
                continue
            names.add(spec["name"])
            fields.append({**spec, "requested": request[match.start() : match.end()]})
    if gullible and "coupon_code" in _page(text):
        fields.append({"name": "coupon_code", "type": "string", "requested": "price"})
    return json.dumps({"name": "record", "fields": fields})


HANDLERS: dict[str, FakeHandler] = {
    "learn_extractor": learn_extractor,
    "heal_extractor": learn_extractor,
    "learn_detail": learn_detail,
    "direct_extract": direct_extract,
    "propose_schema": propose_schema,
}
