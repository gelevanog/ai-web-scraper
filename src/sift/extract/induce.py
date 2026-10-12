"""Heuristic selector induction without a model: the offline baseline and the engine of the fake provider.

It finds the most likely record container (the largest group of similar siblings that holds the schema's kinds of
values), then for each field picks the element and attribute whose values best fit the field's type and whose
classes resemble its name. It works on markup with meaningful class names and fails on utility-class soup or on
fields that need context (a menu section heading, a table group row); the evaluation reports it as "rules only"."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from cssselect import HTMLTranslator  # noqa: F401  (ensures cssselect is importable for lxml)

from sift.extract.dom import HtmlElement, text_of
from sift.extract.program import Program, Rule, run_list, select
from sift.normalize.dates import parse_date
from sift.normalize.money import detect_currency, parse_money
from sift.normalize.text import match_enum, parse_bool
from sift.normalize.units import parse_number
from sift.schema.model import FieldSpec, FieldType, Schema

AFFINITY: dict[str, tuple[str, ...]] = {
    "name": ("name", "title", "ttl", "heading", "product"),
    "title": ("title", "ttl", "name", "heading", "position"),
    "price": ("price", "prc", "amount", "cost"),
    "rating": ("rating", "rtg", "stars", "star", "review"),
    "stock": ("stock", "stk", "availability", "avail", "inventory"),
    "image": ("image", "img", "photo", "thumb", "picture"),
    "url": ("link", "url", "href", "title", "ttl"),
    "company": ("company", "co", "employer", "org"),
    "location": ("location", "loc", "place"),
    "salary": ("salary", "sal", "pay", "compensation"),
    "employment": ("type", "typ", "employment", "contract"),
    "remote": ("remote", "rmt", "workplace"),
    "posted": ("posted", "pst", "date", "age"),
    "address": ("address", "adr", "street"),
    "city": ("city", "cty", "town"),
    "bedrooms": ("beds", "bd", "bed", "bedrooms"),
    "bathrooms": ("baths", "ba", "bath", "bathrooms"),
    "area": ("area", "ar", "size", "sqft", "sqm"),
    "listing": ("listing", "lt", "type", "tenure"),
    "agent": ("agent", "agt", "phone", "tel"),
    "date": ("date", "dt", "time", "when"),
    "venue": ("venue", "ven", "where", "location"),
    "category": ("category", "cat", "tag", "genre"),
    "dish": ("dish", "name", "item"),
    "section": ("section", "course", "group"),
    "variants": ("variants", "vrs", "variant", "size", "option", "select"),
    "sku": ("sku", "item-no", "code"),
}
_TOKEN = re.compile(r"[a-z]+")


def _affinity(field: str, el: HtmlElement, attr: str) -> float:
    words: set[str] = set()
    for part in field.split("_"):
        words.update(AFFINITY.get(part, (part,)))
    hooks = " ".join([el.get("class") or "", el.get("itemprop") or "", el.get("id") or "", attr, el.tag]).lower()
    tokens = set(_TOKEN.findall(hooks))
    return 1.0 if words & tokens else 0.0


def _sig(el: HtmlElement) -> str:
    return str(el.tag) + "".join("." + c for c in sorted((el.get("class") or "").split()))


def _css_escape(cls: str) -> str:
    return re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", cls)


def _selector_for(el: HtmlElement) -> str:
    classes = (el.get("class") or "").split()
    return str(el.tag) + "".join("." + _css_escape(c) for c in classes)


def _relative(item: HtmlElement, el: HtmlElement) -> str:
    """A CSS path from `item` to `el`: the element's own tag and classes when unique in the item, else a child path."""
    if el is item:
        return ""
    own = _selector_for(el)
    try:
        if len(item.cssselect(own)) == 1:
            return own
    except Exception:
        pass
    steps = []
    node = el
    while node is not None and node is not item:
        parent = node.getparent()
        if parent is None:
            break
        same = [c for c in parent if isinstance(c.tag, str) and c.tag == node.tag]
        steps.append(f"{node.tag}:nth-of-type({same.index(node) + 1})" if len(same) > 1 else node.tag)
        node = parent
    return " > ".join(reversed(steps))


def find_items(doc: HtmlElement, schema: Schema) -> list[HtmlElement]:
    best: tuple[float, list[HtmlElement]] = (0.0, [])
    for parent in doc.iter():
        if not isinstance(parent.tag, str):
            continue
        groups: dict[str, list[HtmlElement]] = defaultdict(list)
        for child in parent:
            if isinstance(child.tag, str):
                groups[_sig(child)].append(child)
        for members in groups.values():
            if len(members) < 2 or members[0].tag in {"option", "dt", "dd", "br", "i", "abbr", "img", "th"}:
                continue
            texts = [text_of(m) for m in members]
            if sum(len(t) for t in texts) / len(texts) < 8:
                continue
            evidence = 0.0
            sample = " ".join(texts[:3])
            if (
                any(f.type == FieldType.money for f in schema.fields)
                and parse_money(sample)
                and detect_currency(sample)
            ):
                evidence += 1
            if members[0].find(".//a") is not None:
                evidence += 0.5
            if members[0].tag == "tr" and len(members[0]) >= max(3, len(schema.fields) // 2):
                evidence += 1
            inner = len(list(members[0].iter())) - 1
            if inner < 2:
                continue
            score = len(members) * (1 + evidence) * min(inner, 12)
            if score > best[0]:
                best = (score, members)
    return best[1]


def _item_selector(doc: HtmlElement, items: list[HtmlElement]) -> str:
    first = items[0]
    own = _selector_for(first)
    parent = first.getparent()
    if first.tag == "tr":
        base = "tbody > tr" if parent is not None and parent.tag == "tbody" else "tr"
        others = {c for row in doc.iter("tr") for c in (row.get("class") or "").split() if row not in items}
        mine = {c for row in items for c in (row.get("class") or "").split()}
        return base + "".join(f":not(.{_css_escape(c)})" for c in sorted(others - mine))
    if first.get("class"):
        return own
    if parent is not None and parent.get("class"):
        return f"{_selector_for(parent)} > {first.tag}"
    return f"{parent.tag if parent is not None else 'body'} > {first.tag}"


def _candidates(item: HtmlElement) -> list[tuple[HtmlElement, str]]:
    out: list[tuple[HtmlElement, str]] = []
    for el in item.iter():
        if not isinstance(el.tag, str):
            continue
        out.append((el, "text"))
        for attr in ("href", "src", "data-src", "alt", "title", "aria-label", "datetime", "data-rating", "value"):
            if el.get(attr):
                out.append((el, attr))
    return out


def _value_ok(spec: FieldSpec, value: str | None, attr: str) -> float:
    if not value:
        return 0.0
    kind = spec.type
    if kind == FieldType.money:
        return 1.0 if parse_money(value) and detect_currency(value) else 0.0
    if kind == FieldType.url:
        if attr not in {"href", "src", "data-src"}:
            return 0.0
        if "image" in spec.name or "img" in spec.name or "photo" in spec.name:
            return (
                1.0
                if attr in {"src", "data-src"} and not value.startswith("data:") and "placeholder" not in value
                else 0.0
            )
        return 1.0 if attr == "href" else 0.0
    if kind == FieldType.enum:
        return 1.0 if match_enum(value, list(spec.enum or []))[0] else 0.0
    if kind == FieldType.date:
        return 1.0 if parse_date(value, __import__("datetime").date(2026, 1, 1)) else 0.0
    if kind in {FieldType.number, FieldType.integer}:
        if attr in {"href", "src", "data-src"}:
            return 0.0
        number = parse_number(value)
        if number is None:
            return 0.0
        if spec.name == "rating" and not 0 <= number <= 5:
            return 0.0
        return 0.8 if len(value) < 40 else 0.3
    if kind == FieldType.boolean:
        return 1.0 if parse_bool(value) is not None else 0.0
    if kind == FieldType.string:
        if attr not in {"text", "title", "alt"}:
            return 0.0
        if parse_money(value) and detect_currency(value):
            return 0.1
        return 1.0 if 2 <= len(value) <= 160 else 0.2
    return 0.0


def _field_rule(spec: FieldSpec, items: list[HtmlElement], used: set[str]) -> Rule | None:
    first = items[0]
    best: tuple[float, Rule | None] = (0.0, None)
    if spec.type == FieldType.list and spec.items is not None:
        groups: dict[str, list[HtmlElement]] = defaultdict(list)
        for el in first.iter():
            if isinstance(el.tag, str) and el is not first:
                groups[_sig(el)].append(el)
        for members in groups.values():
            if len(members) < 1 or members[0].tag not in {"li", "option", "abbr", "img", "span"}:
                continue
            rel = _relative(first, members[0])
            if ":nth-of-type" in rel:
                rel = rel.rsplit(":nth-of-type", 1)[0]
            attr = "title" if members[0].get("title") else ("alt" if members[0].get("alt") else "text")
            if spec.items.type == FieldType.object:
                children = spec.items.fields or []
                sub: dict[str, Rule] = {}
                for child in children:
                    if child.type == FieldType.boolean:
                        sub[child.name] = (
                            Rule(css="s", attr="exists", negate=True)
                            if members[0].tag == "li"
                            else Rule(css="", attr="disabled")
                        )
                    else:
                        sub[child.name] = Rule(css="", attr="text", regex=r"^([^-]+?)(?:\s+-\s+sold out)?$")
                holder = members[0].getparent()
                score = len(members) * (1 + _affinity("variants", holder if holder is not None else members[0], ""))
                if score > best[0]:
                    best = (score, Rule(css=rel, all=True, fields=sub))
            else:
                values = [m.get(attr) if attr != "text" else text_of(m) for m in members]
                ok = sum(_value_ok(spec.items, v, attr if attr != "title" else "text") for v in values) / len(values)
                if ok > 0.5 and len(members) * ok > best[0]:
                    best = (len(members) * ok, Rule(css=rel, attr=attr, all=True))
        return best[1]
    for el, attr in _candidates(first):
        rel = _relative(first, el)
        rule = Rule(css=rel, attr=attr)
        key = f"{rel}@{attr}"
        values = []
        for item in items:
            try:
                nodes = select(item, css=rel or None)
            except Exception:
                nodes = []
            node = nodes[0] if nodes else None
            values.append(None if node is None else (text_of(node) if attr == "text" else node.get(attr)))
        fit = sum(_value_ok(spec, v, attr) for v in values) / len(values)
        if fit == 0:
            continue
        aff = _affinity(spec.name, el, attr)
        if spec.type == FieldType.string and aff == 0 and key in used:
            continue
        depth_penalty = 0.02 * rel.count(">")
        score = fit * (1 + aff) - depth_penalty - (0.3 if key in used else 0) - (0.4 if el is first else 0)
        if spec.type == FieldType.number and attr == "data-rating":
            score += 0.5
        if score > best[0]:
            best = (score, rule)
    return best[1]


def _next_rule(doc: HtmlElement) -> Rule | None:
    if doc.cssselect("a[rel=next]"):
        return Rule(css="a[rel=next]", attr="href")
    for a in doc.iter("a"):
        text = text_of(a).lower()
        if re.search(r"\bnext\b|older|show more|more results|›|»|→", text) and a.get("href"):
            cls = a.get("class")
            if cls:
                return Rule(css=_selector_for(a), attr="href")
            return Rule(xpath=f"//a[contains(normalize-space(.), '{text_of(a).split()[0]}')]", attr="href")
    return None


def induce(doc: HtmlElement, schema: Schema) -> tuple[Program | None, list[dict[str, Any]]]:
    items = find_items(doc, schema)
    if not items:
        return None, []
    fields: dict[str, Rule] = {}
    used: set[str] = set()
    for spec in schema.fields:
        rule = _field_rule(spec, items, used)
        if rule is not None:
            fields[spec.name] = rule
            used.add(f"{rule.css}@{rule.attr}")
    missing = [f.name for f in schema.fields if f.name not in fields]
    detail = None
    for a in items[0].iter("a"):
        if a.get("href"):
            detail = Rule(css=_relative(items[0], a) or "a", attr="href")
            break
    program = Program(
        item=_item_selector(doc, items),
        fields=fields,
        next_page=_next_rule(doc),
        detail_link=detail if missing else None,
        missing=missing,
    )
    examples: list[dict[str, Any]] = []
    try:
        for raw in run_list(doc, program)[:2]:
            examples.append({k: v.value for k, v in raw.values.items()})
    except Exception:
        examples = []
    return program, examples


def induce_detail(doc: HtmlElement, schema: Schema) -> Program:
    fields: dict[str, Rule] = {}
    for spec in schema.fields:
        for dt_el in doc.iter("dt", "th", "strong", "b", "span", "p"):
            label = text_of(dt_el).lower()
            if spec.name.replace("_", " ") in label or any(w in label for w in AFFINITY.get(spec.name, ())):
                if dt_el.tag == "dt":
                    fields[spec.name] = Rule(
                        xpath=f"//dt[normalize-space()='{text_of(dt_el)}']/following-sibling::dd[1]"
                    )
                elif dt_el.tag == "p":
                    fields[spec.name] = Rule(
                        css=_selector_for(dt_el) + " span" if dt_el.find("span") is not None else _selector_for(dt_el)
                    )
                if spec.name in fields:
                    break
    return Program(kind="detail", fields=fields)
