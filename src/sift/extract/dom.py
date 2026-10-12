"""HTML handling shared by both strategies: parsing, hidden-content removal, visible text, the condensed skeleton a
model reads to write selectors, the cleaned page a model reads to extract directly, and the structural fingerprint
used to recognise a template and detect a layout change.

Hidden content (the `hidden` attribute, `display:none`, `visibility:hidden`, `aria-hidden="true"`, `<template>`,
`<noscript>`, comments) is removed in code before extraction, so a hidden decoy item or a hidden instruction never
becomes a record and never reaches a model."""

from __future__ import annotations

import copy
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urljoin

from lxml import etree
from lxml import html as lxml_html

type HtmlElement = lxml_html.HtmlElement

_DROP_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "canvas", "link", "meta", "head")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_WS = re.compile(r"\s+")
_FOOTNOTE = re.compile(r"^[\d*†‡§¹²³⁴⁵⁶⁷⁸⁹⁰]{1,3}$")
_KEEP_ATTRS_SKELETON = (
    "class",
    "id",
    "href",
    "src",
    "data-src",
    "alt",
    "title",
    "aria-label",
    "datetime",
    "itemprop",
    "rel",
    "role",
    "type",
    "value",
    "disabled",
    "aria-disabled",
    "colspan",
    "scope",
    "lang",
)
_KEEP_ATTRS_CLEAN = (
    "href",
    "src",
    "data-src",
    "alt",
    "title",
    "aria-label",
    "datetime",
    "disabled",
    "aria-disabled",
    "value",
    "colspan",
)


def parse(html: str | bytes) -> HtmlElement:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    if not html.strip():
        html = "<html><body></body></html>"
    try:
        return lxml_html.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return lxml_html.document_fromstring("<html><body></body></html>")


def page_lang(doc: HtmlElement) -> str | None:
    lang = doc.get("lang")
    return lang.strip() if lang else None


def is_hidden(el: HtmlElement) -> bool:
    if not isinstance(el.tag, str):
        return True
    if el.tag in ("template", "noscript"):
        return True
    if el.get("hidden") is not None:
        return True
    if (el.get("aria-hidden") or "").lower() == "true" and el.tag not in ("svg", "i", "use"):
        return True
    style = el.get("style")
    return bool(style and _HIDDEN_STYLE.search(style))


def strip_hidden(doc: HtmlElement) -> tuple[HtmlElement, list[str]]:
    """A copy of the document without hidden subtrees, comments and scripts; returns the removed hidden texts too
    (the injection guard inspects them: hiding an instruction is itself evidence)."""
    doc = copy.deepcopy(doc)
    removed: list[str] = []
    for comment in doc.xpath("//comment()"):
        text = (comment.text or "").strip()
        if text:
            removed.append(text)
        _drop(comment)
    for el in list(doc.iter()):
        if not isinstance(el.tag, str):
            continue
        if el.getparent() is None:
            continue
        if el.tag in ("script", "style"):
            _drop(el)
        elif is_hidden(el):
            text = _WS.sub(" ", el.text_content()).strip()
            if text:
                removed.append(text)
            _drop(el)
    return doc, removed


def _drop(el: etree._Element) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    if tail:
        previous = el.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


def text_of(el: HtmlElement) -> str:
    """Visible text with collapsed whitespace; footnote markers in <sup> ("Library¹") are left out."""
    parts: list[str] = []

    def walk(node: HtmlElement) -> None:
        if not isinstance(node.tag, str):
            if node.tail:
                parts.append(node.tail)
            return
        if node.tag in ("script", "style", "template", "noscript"):
            if node.tail:
                parts.append(node.tail)
            return
        skip = node.tag == "sup" and _FOOTNOTE.match((node.text_content() or "").strip())
        if not skip:
            if node.text:
                parts.append(node.text)
            for child in node:
                walk(child)
        if node.tag in ("br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "dt", "dd"):
            parts.append(" ")
        if node.tail and node is not el:
            parts.append(node.tail)

    walk(el)
    return _WS.sub(" ", "".join(parts)).strip()


def absolute(url: str, base: str) -> str:
    return urljoin(base, url.strip())


# --------------------------------------------------------------------------------------------- skeleton for models


def _signature(el: HtmlElement) -> str:
    classes = " ".join(sorted((el.get("class") or "").split()))
    return f"{el.tag}.{classes}"


def _truncate(text: str, limit: int) -> str:
    text = _WS.sub(" ", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def skeleton(doc: HtmlElement, *, max_chars: int = 24_000, keep_repeats: int = 3, text_limit: int = 160) -> str:
    """Condensed HTML for selector writing: hidden parts, scripts, styles and SVG removed; only attributes that help
    selection kept; long text cut; runs of more than `keep_repeats` siblings with the same tag and classes collapsed
    into a comment saying how many were left out."""
    clean, _ = strip_hidden(doc)
    body = clean.find("body")
    root = body if body is not None else clean
    for el in list(root.iter()):
        if isinstance(el.tag, str) and el.tag in _DROP_TAGS and el is not root:
            _drop(el)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if attr not in _KEEP_ATTRS_SKELETON and not attr.startswith("data-"):
                del el.attrib[attr]
            elif attr in ("alt", "title", "aria-label"):
                el.set(attr, _truncate(el.get(attr) or "", 120))
            elif attr == "src" and (el.get(attr) or "").startswith("data:"):
                el.set(attr, "data:…")
        if el.text:
            el.text = _truncate(el.text, text_limit)
        if el.tail:
            el.tail = _truncate(el.tail, text_limit)
    for parent in list(root.iter()):
        if not isinstance(parent.tag, str):
            continue
        children = [c for c in parent if isinstance(c.tag, str)]
        runs: list[list[HtmlElement]] = []
        for child in children:
            if runs and _signature(runs[-1][-1]) == _signature(child):
                runs[-1].append(child)
            else:
                runs.append([child])
        for run in runs:
            # Table cells are columns, not repeated records: never hide them.
            if run[0].tag in ("td", "th"):
                continue
            if len(run) > keep_repeats + 1:
                extra = run[keep_repeats:]
                note = etree.Comment(
                    f' {len(extra)} more <{run[0].tag} class="{run[0].get("class") or ""}"> siblings like the ones above '
                )
                extra[0].addprevious(note)
                for el in extra:
                    _drop(el)
    out = lxml_html.tostring(root, encoding="unicode", method="html")
    out = re.sub(r">\s+<", "><", out)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n<!-- truncated -->"
    return out


def clean_html(doc: HtmlElement, *, max_chars: int = 40_000) -> str:
    """Compact HTML for direct extraction: visible content only, presentational attributes dropped, whitespace
    collapsed. Classes are dropped too (the model reads content, not hooks)."""
    clean, _ = strip_hidden(doc)
    body = clean.find("body")
    root = body if body is not None else clean
    for el in list(root.iter()):
        if isinstance(el.tag, str) and el.tag in _DROP_TAGS and el is not root:
            _drop(el)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if attr not in _KEEP_ATTRS_CLEAN and not attr.startswith("data-"):
                del el.attrib[attr]
        if el.get("src", "").startswith("data:"):
            el.set("src", "data:…")
    out = lxml_html.tostring(root, encoding="unicode", method="html")
    out = re.sub(r"\s+", " ", out)
    out = re.sub(r">\s+<", "><", out)
    for tag in ("div", "span", "section", "article", "main"):
        out = re.sub(rf"<{tag}>|</{tag}>", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out[:max_chars]


def visible_text(doc: HtmlElement) -> str:
    clean, _ = strip_hidden(doc)
    body = clean.find("body")
    return text_of(body if body is not None else clean)


# --------------------------------------------------------------------------------------------- fingerprint


@dataclass(frozen=True)
class Fingerprint:
    shingles: dict[str, int]

    def similarity(self, other: Fingerprint) -> float:
        """Weighted Jaccard over log-scaled shingle counts (1.0 = same structure)."""
        keys = set(self.shingles) | set(other.shingles)
        if not keys:
            return 1.0
        lo = hi = 0.0
        for key in keys:
            a = math.log1p(self.shingles.get(key, 0))
            b = math.log1p(other.shingles.get(key, 0))
            lo += min(a, b)
            hi += max(a, b)
        return lo / hi if hi else 1.0

    def digest(self) -> str:
        material = "|".join(f"{k}:{v}" for k, v in sorted(self.shingles.items()))
        return hashlib.sha1(material.encode()).hexdigest()[:12]


def _token(el: HtmlElement) -> str:
    classes = [c for c in (el.get("class") or "").split() if not re.search(r"\d{2,}", c)][:3]
    return str(el.tag) + ("." + ".".join(sorted(classes)) if classes else "")


def fingerprint(doc: HtmlElement) -> Fingerprint:
    """Parent>child tag-and-class pairs of the visible body: stable across pages of one template (different products,
    same markup) and very different after a redesign (renamed classes, other tags, moved elements)."""
    clean, _ = strip_hidden(doc)
    body = clean.find("body")
    root = body if body is not None else clean
    counts: Counter[str] = Counter()
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag in _DROP_TAGS:
            continue
        parent = el.getparent()
        if parent is None or not isinstance(parent.tag, str):
            continue
        counts[f"{_token(parent)}>{_token(el)}"] += 1
    return Fingerprint(dict(counts))


def content_hash(doc: HtmlElement) -> str:
    """Hash of the visible text: unchanged content means the page need not be extracted again."""
    return hashlib.sha256(visible_text(doc).encode()).hexdigest()
