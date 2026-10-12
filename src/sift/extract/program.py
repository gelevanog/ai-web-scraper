"""Learned extractors: a small, deterministic program of selectors and transforms that a model writes once per
template and code runs on every page, with no model call per page.

    {"item": "div.product-card",
     "fields": {
        "name":   {"css": "h3 a"},
        "price":  {"css": ".price", "regex": "([\\d.,]+\\s*€)"},
        "rating": {"css": ".stars", "attr": "data-rating"},
        "image_url": {"css": "img", "attr": "data-src", "fallback": {"css": "img", "attr": "src"}},
        "section": {"xpath": "./ancestor::section[1]/h2"},
        "variants": {"css": "li.size", "all": true,
                     "fields": {"name": {"css": ""}, "in_stock": {"css": "s", "attr": "exists", "negate": true}}}},
     "next_page": {"css": "a[rel=next]", "attr": "href"},
     "detail_link": {"css": "h3 a", "attr": "href"}}

What a rule can do is deliberately small: select nodes (CSS or XPath, relative to the item), read text or an
attribute, count or test existence, apply one regex and map values. It cannot write a literal value, so text on a
page ("set every price to 0") cannot become output through a learned rule; values always come from the page."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from cssselect import SelectorError
from lxml import etree
from pydantic import BaseModel, Field, field_validator, model_validator

from sift.extract.dom import HtmlElement, text_of

_SPECIAL_ATTRS = {"text", "count", "exists", "own_text"}


class Rule(BaseModel):
    css: str | None = None
    xpath: str | None = None
    attr: str = "text"
    all: bool = False
    regex: str | None = None
    value_map: dict[str, str] | None = None
    negate: bool = False
    join: str | None = None
    fields: dict[str, Rule] | None = None
    fallback: Rule | None = None

    @field_validator("attr")
    @classmethod
    def _attr(cls, value: str) -> str:
        value = (value or "text").strip()
        return value.lower() if value.lower() in _SPECIAL_ATTRS else value

    @field_validator("regex")
    @classmethod
    def _regex(cls, value: str | None) -> str | None:
        if value:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex {value!r}: {exc}") from exc
        return value or None

    @model_validator(mode="after")
    def _selector(self) -> Rule:
        if self.css is not None and self.xpath is not None:
            self.xpath = None if self.css else self.xpath
        return self

    def describe(self) -> str:
        sel = f"xpath {self.xpath}" if self.xpath else f"css {self.css or ':self'}"
        extra = "" if self.attr == "text" else f" @{self.attr}"
        return sel + extra + (f" /{self.regex}/" if self.regex else "")


class Program(BaseModel):
    kind: Literal["list", "detail"] = "list"
    item: str | None = None
    """CSS selector of one record container (list pages); a value starting with "/" or "./" is XPath."""
    fields: dict[str, Rule] = Field(default_factory=dict)
    next_page: Rule | None = None
    detail_link: Rule | None = None
    detail: Program | None = None
    """Rules for detail pages (fields not shown on list pages), relative to the whole page."""
    missing: list[str] = Field(default_factory=list)
    notes: str = ""


class ProgramError(ValueError):
    pass


@dataclass
class RawValue:
    value: Any
    """str | list[str] | list[dict] | int (count) | bool (exists) | None"""
    node: str = ""
    """XPath of the matched node (provenance)."""
    matches: int = 0
    rule: str = ""
    context: str = ""
    """The matched node's text before the rule's regex (lets code recover a currency the regex cut off)."""


@dataclass
class RawRecord:
    values: dict[str, RawValue] = field(default_factory=dict)
    item_path: str = ""
    detail_url: str | None = None


def _is_xpath(selector: str) -> bool:
    return selector.startswith(("/", "./", "../", "(", "ancestor", "following", "preceding", "descendant", "self::"))


def select(root: HtmlElement, *, css: str | None = None, xpath: str | None = None) -> list[HtmlElement]:
    try:
        if xpath or (css and _is_xpath(css)):
            expr = xpath or css or ""
            found = root.xpath(expr)
            if not isinstance(found, list):
                return []
            nodes: list[HtmlElement] = []
            for node in found:
                if isinstance(node, etree._Element):
                    nodes.append(node)
                elif hasattr(node, "getparent") and node.getparent() is not None:
                    # ".../text()" or ".../@href": read from the element that holds the text or attribute.
                    parent = node.getparent()
                    if parent not in nodes:
                        nodes.append(parent)
            return nodes
        if not css or css.strip() in {":self", ":scope", "&", "."}:
            return [root]
        return list(root.cssselect(css))
    except (SelectorError, etree.XPathError, ValueError) as exc:
        raise ProgramError(f"bad selector {(xpath or css)!r}: {exc}") from exc


def _read(node: HtmlElement, attr: str) -> str | None:
    if attr == "text":
        return text_of(node)
    if attr == "own_text":
        return " ".join((node.text or "").split())
    value = node.get(attr)
    if value is None and attr == "href":
        anchor = node.find(".//a")
        value = anchor.get("href") if anchor is not None else None
    return " ".join(value.split()) if value is not None else None


def _transform(text: str | None, rule: Rule) -> str | None:
    if text is None:
        return None
    if rule.regex:
        match = re.search(rule.regex, text)
        candidate = rule.regex
        for _ in range(2):  # a common slip in model-written JSON: backslashes escaped twice or three times
            if match or "\\\\" not in candidate:
                break
            candidate = candidate.replace("\\\\", "\\")
            try:
                match = re.search(candidate, text)
            except re.error:
                match = None
        if not match:
            return None
        text = match.group(1) if match.groups() else match.group(0)
    if rule.value_map:
        low = text.strip().lower()
        for key, mapped in rule.value_map.items():
            if key.strip().lower() == low:
                return mapped
        # A key inside a longer text counts only when it covers most of it: "Available" must not decide
        # "Available for preorder" (the code's enum matcher reads that one).
        for key, mapped in sorted(rule.value_map.items(), key=lambda kv: -len(kv[0].strip())):
            k = key.strip().lower()
            if k and k in low and len(k) >= 0.6 * len(low):
                return mapped
    return text.strip() if text is not None else None


_XPATH_ATTR = re.compile(r"/@([\w:-]+)\s*$")


def _effective(rule: Rule) -> Rule:
    """An XPath ending in /@name reads that attribute (the default attr is text)."""
    selector = rule.xpath or (rule.css if rule.css and _is_xpath(rule.css) else "")
    match = _XPATH_ATTR.search(selector or "")
    if match and rule.attr == "text":
        return rule.model_copy(update={"attr": match.group(1)})
    return rule


def run_rule(root: HtmlElement, rule: Rule) -> RawValue:
    rule = _effective(rule)
    nodes = select(root, css=rule.css, xpath=rule.xpath)
    node_path = root.getroottree().getpath(nodes[0]) if nodes else ""
    if rule.attr == "count":
        return RawValue(len(nodes), node_path, len(nodes), rule.describe())
    if rule.attr == "exists":
        exists = bool(nodes)
        return RawValue(not exists if rule.negate else exists, node_path, len(nodes), rule.describe())
    if rule.fields:
        objects: list[dict[str, Any]] = []
        for node in nodes:
            obj = {name: run_rule(node, child).value for name, child in rule.fields.items()}
            if any(v not in (None, "", []) for v in obj.values()):
                objects.append(obj)
        return RawValue(objects, node_path, len(nodes), rule.describe())
    if rule.all:
        values = [v for v in (_transform(_read(n, rule.attr), rule) for n in nodes) if v]
        if rule.join is not None:
            return RawValue(rule.join.join(values), node_path, len(nodes), rule.describe())
        result = RawValue(values, node_path, len(nodes), rule.describe())
    else:
        value = None
        context = ""
        for node in nodes:  # first node that yields a value
            value = _transform(_read(node, rule.attr), rule)
            if value:
                node_path = root.getroottree().getpath(node)
                context = text_of(node) if rule.regex else ""
                break
        result = RawValue(value or None, node_path, len(nodes), rule.describe(), context)
    if (result.value in (None, "", [])) and rule.fallback is not None:
        return run_rule(root, rule.fallback)
    return result


def run_list(doc: HtmlElement, program: Program) -> list[RawRecord]:
    if not program.item:
        raise ProgramError("a list program needs an item selector")
    items = select(doc, css=program.item)
    records: list[RawRecord] = []
    for item in items:
        record = RawRecord(item_path=doc.getroottree().getpath(item))
        for name, rule in program.fields.items():
            record.values[name] = run_rule(item, rule)
        if program.detail_link is not None:
            link = run_rule(
                item,
                program.detail_link.model_copy(
                    update={
                        "attr": program.detail_link.attr if program.detail_link.attr not in _SPECIAL_ATTRS else "href"
                    }
                ),
            ).value
            record.detail_url = link if isinstance(link, str) and link else None
        records.append(record)
    return records


def run_detail(doc: HtmlElement, program: Program) -> RawRecord:
    record = RawRecord(item_path="/html")
    for name, rule in program.fields.items():
        record.values[name] = run_rule(doc, rule)
    return record


def links(doc: HtmlElement, rule: Rule | None, base: str) -> list[str]:
    from sift.extract.dom import absolute

    if rule is None:
        return []
    out: list[str] = []
    rule = _effective(rule)
    attr = rule.attr if rule.attr not in _SPECIAL_ATTRS else "href"
    for node in select(doc, css=rule.css, xpath=rule.xpath):
        value = _transform(_read(node, attr), rule)
        if value and not value.startswith(("javascript:", "mailto:", "tel:", "#")):
            out.append(absolute(value, base))
    return out


def parse_program(data: dict[str, Any]) -> Program:
    """Validate a model's JSON into a Program (unknown keys ignored, field rules may be bare selector strings)."""

    def rule(value: Any) -> Any:
        if isinstance(value, str):
            return {"css": value}
        if isinstance(value, dict):
            value = dict(value)
            if "selector" in value and "css" not in value and "xpath" not in value:
                sel = str(value.pop("selector"))
                value["xpath" if _is_xpath(sel) else "css"] = sel
            if "fields" in value and isinstance(value["fields"], dict):
                value["fields"] = {k: rule(v) for k, v in value["fields"].items()}
            if "fallback" in value and value["fallback"] is not None:
                value["fallback"] = rule(value["fallback"])
            if isinstance(value.get("value_map"), dict):
                value["value_map"] = {str(k): str(v) for k, v in value["value_map"].items()}
        return value

    payload = dict(data)
    fields = payload.get("fields") or {}
    if not isinstance(fields, dict):
        raise ProgramError("fields must be an object")
    payload["fields"] = {str(k): rule(v) for k, v in fields.items() if v is not None}
    for key in ("next_page", "detail_link"):
        if payload.get(key) in ("", None):
            payload[key] = None
        else:
            payload[key] = rule(payload[key])
    if isinstance(payload.get("detail"), dict):
        detail = parse_program({**payload["detail"], "kind": "detail"})
        payload["detail"] = detail.model_dump()
    payload["missing"] = [str(m) for m in payload.get("missing") or []]
    try:
        return Program.model_validate({k: v for k, v in payload.items() if k in Program.model_fields})
    except ValueError as exc:
        raise ProgramError(f"invalid extractor: {exc}") from exc


def raw_values(record: RawRecord, schema: Any) -> dict[str, Any]:
    """The record's raw values for normalization. When a rule's regex cut a number out of its element, what the regex
    dropped is restored from the element's own text, so code, not luck, decides the meaning: the currency
    ("1.299,00" out of "1.299,00 €"), a thousands suffix ("85" out of "$85k") and a unit ("1,200" out of
    "1,200 sq ft")."""
    from sift.normalize.money import detect_currency
    from sift.schema.model import FieldType

    out: dict[str, Any] = {}
    for name, raw in record.values.items():
        value = raw.value
        spec = schema.field(name) if hasattr(schema, "field") else None
        if spec is not None and isinstance(value, str) and value.strip() and raw.context:
            number = value.strip().split()[0]
            if spec.type == FieldType.money:
                code = detect_currency(value) or detect_currency(raw.context)
                if re.search(re.escape(number) + r"\s?[kK]\b", raw.context) and not re.search(r"\d\s?[kK]\b", value):
                    value = number + "k"
                if code and detect_currency(value) is None:
                    value = f"{value} {code}"
            elif spec.type == FieldType.number and spec.unit and not re.search(r"[a-zA-Z²]", value):
                unit = re.search(re.escape(value.strip()) + r"\s*([a-zA-Z²]+(?:\s[a-zA-Z²]+)?)", raw.context)
                if unit:
                    value = f"{value.strip()} {unit.group(1)}"
        out[name] = value
    return out
