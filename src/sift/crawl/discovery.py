"""Link discovery without a model: pagination, "needs a browser" and links relevant to the request.

Pagination is found the way a person finds it: `rel="next"`, a link whose text says next / older / more / › / »,
numbered page links whose URL differs from the current one only in a number, and "show more" links with an href.
Learned extractors add their own `next_page` rule on top; the two are combined."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

from sift.crawl.urls import canonicalize, is_http, is_resource, same_site
from sift.extract.dom import HtmlElement, strip_hidden, text_of, visible_text

NEXT_TEXT = re.compile(
    r"^\s*(next|next page|older|older entries|more results|show more|load more|see more|weiter|suivant|siguiente|volgende|›|»|→|>>?)\b|^\s*(›|»|→)\s*$|\b(next|older)\s*(page|\d+)?\s*[›»→]?\s*$",
    re.I,
)
PAGE_PARAMS = ("page", "p", "pg", "start", "offset", "from", "skip", "cursor")
_NUMBER = re.compile(r"\d+")


def _page_shape(url: str) -> tuple[str, str]:
    """URL with every number replaced, to compare page-N URLs ("/catalog/page/3/" ~ "/catalog/page/7/")."""
    parts = urlsplit(url)
    return _NUMBER.sub("#", parts.path), "&".join(sorted(k for k, _ in parse_qsl(parts.query)))


def pagination_links(doc: HtmlElement, url: str) -> list[str]:
    clean, _ = strip_hidden(doc)
    found: list[str] = []
    current = canonicalize(url)

    def add(href: str | None) -> None:
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            return
        target = canonicalize(href, url)
        if target != current and same_site(url, target) and is_http(target) and target not in found:
            found.append(target)

    for link in clean.xpath("//link[@rel='next'] | //a[contains(concat(' ', normalize-space(@rel), ' '), ' next ')]"):
        add(link.get("href"))
    for a in clean.iter("a"):
        href = a.get("href")
        if not href:
            continue
        text = text_of(a)
        label = (a.get("aria-label") or "") + " " + (a.get("title") or "")
        if NEXT_TEXT.search(text) or re.search(r"\bnext\b", label, re.I):
            add(href)
        elif text.isdigit() and int(text) < 1000:
            target = canonicalize(href, url)
            path_shape, query_shape = _page_shape(target)
            cur_path, _cur_query = _page_shape(current)
            query = dict(parse_qsl(urlsplit(target).query))
            if (
                (query_shape and any(p in query for p in PAGE_PARAMS))
                or (path_shape == cur_path and target != current)
                or "page" in target
            ):
                add(href)
    return found


def needs_browser(doc: HtmlElement, raw_html: str) -> str | None:
    """Why this page needs JavaScript, or None: an empty application shell, a load-more button without a link, or an
    infinite-scroll script."""
    text = visible_text(doc)
    scripts = len(re.findall(r"<script\b", raw_html, re.I))
    if (
        scripts
        and len(text) < 400
        and re.search(r'id=["\'](app|root|__next|main-app)["\']|data-reactroot|ng-app|loading', raw_html, re.I)
    ):
        return "empty application shell"
    for button in doc.iter("button"):
        if re.search(r"(load|show|see|view)\s+more|more results", text_of(button), re.I):
            return "load-more button"
    if scripts and re.search(r"IntersectionObserver|onscroll|addEventListener\(\s*['\"]scroll", raw_html):
        return "infinite scroll"
    return None


def relevant_links(doc: HtmlElement, url: str, words: set[str], *, limit: int = 5) -> list[str]:
    """Same-site links whose text or URL shares words with the request ("menu" -> "Download our menu (PDF)")."""
    clean, _ = strip_hidden(doc)
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for a in clean.iter("a"):
        href = a.get("href")
        if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
            continue
        target = canonicalize(href, url)
        if target in seen or not same_site(url, target) or (is_resource(target) and not target.endswith(".pdf")):
            continue
        seen.add(target)
        tokens = set(re.findall(r"[a-z]{3,}", (text_of(a) + " " + urlsplit(target).path).lower()))
        overlap = len(tokens & words)
        if overlap:
            scored.append((overlap + (1 if target.endswith(".pdf") else 0), target))
    scored.sort(key=lambda pair: -pair[0])
    return [target for _, target in scored[:limit]]


def request_words(description: str, field_names: list[str]) -> set[str]:
    words = set(re.findall(r"[a-z]{3,}", description.lower()))
    for name in field_names:
        words.update(w for w in name.split("_") if len(w) >= 3)
    return words - {
        "the",
        "and",
        "with",
        "every",
        "all",
        "each",
        "its",
        "their",
        "from",
        "link",
        "page",
        "pages",
        "follow",
        "pagination",
        "usd",
        "url",
    }
