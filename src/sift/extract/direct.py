"""Strategy B: one model call per page on the cleaned page (or a PDF's text), for one-off or irregular pages.

The model copies raw values; code normalizes them exactly as it normalizes learned extractions, then checks that
each value is grounded: the raw text (or, for numbers, its digits) must occur in the page's visible text. A value
that is not on the page gets low confidence and a `not_on_page` note instead of being trusted."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sift.extract.dom import HtmlElement, clean_html, visible_text
from sift.extract.prompts import DIRECT
from sift.llm.base import ChatModel, LLMError
from sift.llm.jsonparse import JSONReplyError, parse_json_object
from sift.schema.model import FieldType, Schema
from sift.validate.injection import Finding, guard_dom, quarantine, scan, spotlight

_WS = re.compile(r"\s+")


@dataclass
class DirectResult:
    records: list[dict[str, Any]]
    calls: int
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None
    input_chars: int = 0
    truncated: bool = False


def page_content(doc: HtmlElement | None, text: str | None, *, max_chars: int) -> tuple[str, str]:
    """(content for the model, visible text for grounding checks)."""
    if doc is not None:
        return clean_html(doc, max_chars=max_chars), visible_text(doc)
    text = text or ""
    return text[:max_chars], text


def extract(
    model: ChatModel,
    schema: Schema,
    url: str,
    *,
    doc: HtmlElement | None = None,
    text: str | None = None,
    guard: bool = True,
    max_chars: int = 40_000,
    max_tokens: int = 16_000,
) -> DirectResult:
    findings: list[Finding] = []
    if guard and doc is not None:
        doc, findings = guard_dom(doc)
    content, _ = page_content(doc, text, max_chars=max_chars + 1)
    if guard and doc is None:
        findings = scan(content)
        if findings:
            content = quarantine(content, findings)
    truncated = len(content) > max_chars
    content = content[:max_chars]
    compact = schema.model_dump_json(exclude_defaults=True)
    user = f"Schema fields:\n{schema.prompt_lines()}\nSchema JSON: {compact}\n\nPage URL: {url}\n{spotlight(content)}"
    try:
        reply = model.complete([{"role": "system", "content": DIRECT.format()}, {"role": "user", "content": user}], max_tokens=max_tokens)
        data = parse_json_object(reply.text)
    except (JSONReplyError, LLMError) as exc:
        return DirectResult([], 1, findings, error=str(exc)[:300], input_chars=len(content), truncated=truncated)
    records = data.get("records")
    if not isinstance(records, list):
        return DirectResult([], 1, findings, error="no records array in the answer", input_chars=len(content), truncated=truncated)
    return DirectResult([r for r in records if isinstance(r, dict)], 1, findings, input_chars=len(content), truncated=truncated)


def grounded(raw: Any, page_text: str, spec_type: FieldType) -> bool:
    """Is this raw value visible on the page? Strings by substring, numbers by digits, booleans and enums always
    (they are judgments about the page, checked by type instead)."""
    if raw is None or raw == "" or raw == []:
        return True
    if isinstance(raw, bool) or spec_type in {FieldType.boolean, FieldType.enum}:
        return True
    if isinstance(raw, list):
        return all(grounded(item, page_text, FieldType.string) for item in raw if not isinstance(item, dict))
    if isinstance(raw, dict):
        return True
    text = _WS.sub(" ", str(raw)).strip().lower()
    haystack = _WS.sub(" ", page_text).lower()
    if text in haystack:
        return True
    digits = re.sub(r"\D", "", text)
    if digits and len(digits) >= 2:
        return digits in re.sub(r"\D", "", haystack)
    if spec_type in {FieldType.date, FieldType.url}:
        return True
    words = [w for w in re.findall(r"[a-z0-9]{3,}", text)]
    return bool(words) and sum(1 for w in words if w in haystack) / len(words) >= 0.8
