"""Schema from intent: the model proposes typed fields from the user's plain-English request and a sample page; code
validates the proposal and keeps only fields that quote the request.

Each proposed field must name the words of the request it comes from ("requested"). A field whose quote is not in
the request, such as a `coupon_code` a page asked for, is moved to `rejected` with the reason, never into the schema.
The user can edit the schema before the job runs; from then on it is fixed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sift.extract.dom import HtmlElement, skeleton
from sift.extract.prompts import SCHEMA
from sift.llm.base import ChatModel, LLMError
from sift.llm.jsonparse import JSONReplyError, parse_json_object
from sift.schema.model import FieldSpec, Schema
from sift.validate.injection import Finding, guard_dom, spotlight

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "with",
    "its",
    "their",
    "for",
    "to",
    "in",
    "on",
    "each",
    "every",
    "all",
    "is",
    "it",
    "from",
    "as",
    "by",
    "whether",
    "get",
    "also",
}


@dataclass
class Proposal:
    schema: Schema | None
    rejected: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None
    calls: int = 0


def _stem(word: str) -> str:
    for suffix in ("ies", "es", "s"):
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def quoted(phrase: str, request: str) -> bool:
    """Is `phrase` (the field's quote) really in the request? Exact substring, or most of its content words."""
    if not phrase:
        return False
    p, r = phrase.lower().strip(" .\"'"), request.lower()
    if p and p in r:
        return True
    words = [_stem(w) for w in _WORD.findall(p) if w not in _STOP]
    if not words:
        return False
    have = {_stem(w) for w in _WORD.findall(r)}
    return sum(1 for w in words if w in have) / len(words) >= 0.6


def _clean_field(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "name",
        "type",
        "description",
        "required",
        "enum",
        "items",
        "fields",
        "currency",
        "unit",
        "contact",
        "key",
    }
    out = {k: v for k, v in data.items() if k in allowed and v is not None}
    if isinstance(out.get("items"), dict):
        out["items"] = _clean_field({"name": "item", **out["items"]})
    if isinstance(out.get("fields"), list):
        out["fields"] = [_clean_field(f) for f in out["fields"] if isinstance(f, dict)]
    if out.get("contact") not in ("none", "business"):
        out.pop("contact", None)
    if isinstance(out.get("type"), str):
        out["type"] = out["type"].strip().lower()
        out["type"] = {
            "str": "string",
            "text": "string",
            "int": "integer",
            "float": "number",
            "bool": "boolean",
            "price": "money",
            "currency": "money",
            "array": "list",
            "link": "url",
            "uri": "url",
        }.get(out["type"], out["type"])
    return out


def _tokens(text: str) -> set[str]:
    return {w[:4] for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def validate_proposal(
    data: dict[str, Any], request: str, injected: list[str] | None = None
) -> tuple[Schema | None, list[dict[str, Any]], str | None]:
    """`injected`: text the injection guard flagged on the sample page. A field named after words that appear there
    and not in the request is rejected even if it quotes the request (a model can quote a real phrase for a fake
    field)."""
    fields: list[FieldSpec] = []
    rejected: list[dict[str, Any]] = []
    request_tokens = _tokens(request)
    injected_tokens = _tokens(" ".join(injected or [])) - request_tokens
    for raw in data.get("fields") or []:
        if not isinstance(raw, dict):
            continue
        phrase = str(raw.get("requested") or "")
        if not quoted(phrase, request):
            rejected.append({"name": raw.get("name"), "reason": f"not in your request (quoted {phrase[:60]!r})"})
            continue
        name_tokens = _tokens(str(raw.get("name") or "").replace("_", " "))
        if injected_tokens and name_tokens & injected_tokens and not name_tokens & request_tokens:
            rejected.append(
                {"name": raw.get("name"), "reason": "named after text the injection guard flagged on the page"}
            )
            continue
        try:
            spec = FieldSpec.model_validate(_clean_field(raw))
        except ValueError as exc:
            rejected.append({"name": raw.get("name"), "reason": f"invalid: {str(exc)[:120]}"})
            continue
        if any(f.name == spec.name for f in fields):
            rejected.append({"name": spec.name, "reason": "duplicate name"})
            continue
        fields.append(spec)
    if not fields:
        return None, rejected, "no valid field in the proposal"
    if not any(f.required for f in fields):
        fields[0].required = True
    name = re.sub(r"[^a-z0-9_]+", "_", str(data.get("name") or "record").lower()).strip("_") or "record"
    return Schema(name=name, description=request[:300], fields=fields), rejected, None


def propose(
    model: ChatModel,
    request: str,
    *,
    sample: HtmlElement | None = None,
    sample_url: str = "",
    guard: bool = True,
    max_tokens: int = 16_000,
) -> Proposal:
    findings: list[Finding] = []
    parts = [f"User's request:\n{request.strip()}"]
    if sample is not None:
        doc = sample
        if guard:
            doc, findings = guard_dom(sample)
        page = skeleton(doc, max_chars=12_000)
        parts.append(f"Sample page ({sample_url}):\n{spotlight(page)}")
    try:
        reply = model.complete(
            [{"role": "system", "content": SCHEMA.format()}, {"role": "user", "content": "\n\n".join(parts)}],
            max_tokens=max_tokens,
        )
        data = parse_json_object(reply.text)
    except (JSONReplyError, LLMError) as exc:
        return Proposal(None, findings=findings, error=str(exc)[:300], calls=1)
    schema, rejected, error = validate_proposal(data, request, [f.sentence for f in findings])
    return Proposal(schema, rejected, findings, error, calls=1)
