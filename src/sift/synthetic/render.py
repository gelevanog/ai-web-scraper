"""Renders a SiteSpec into static files for one layout (v1 or v2).

Output: {storage_path: (content_type, bytes)}; storage paths come from `storage_path(url_path, query)`, so a URL with
a query string ("/catalog/?page=2") maps to one file ("catalog/index.html@page=2")."""

from __future__ import annotations

import html
import json
import random
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from sift.synthetic.data import INJECTION_TEXT, SiteSpec, slug
from sift.synthetic.pdf import Line, make_pdf
from sift.synthetic.styles import Style, make_style

Files = dict[str, tuple[str, bytes]]
e = html.escape

ROLES = {
    "ecommerce": ["image", "title", "price", "rating", "stock", "variants", "desc", "button"],
    "jobs": ["title", "company", "location", "salary", "type", "remote", "posted", "contact"],
    "realestate": ["image", "address", "city", "price", "listing", "facts", "agent"],
    "events": ["title", "date", "venue", "price", "category"],
}
BLOCKS = {"ecommerce": "product-card", "jobs": "job-card", "realestate": "listing-card", "events": "event-card"}


def storage_path(url_path: str, query: str = "") -> str:
    path = url_path.lstrip("/")
    if path == "" or path.endswith("/"):
        path += "index.html"
    if query:
        path += "@" + query
    return path


def url_to_storage(url: str) -> str:
    parts = urlsplit(url)
    return storage_path(parts.path or "/", parts.query)


def styles_for(spec: SiteSpec) -> dict[str, Style]:
    rng = random.Random(spec.variant_seed)
    roles = ROLES.get(spec.vertical, ["title", "price"])
    block = BLOCKS.get(spec.vertical, "row")
    v1 = make_style(rng, block=block, roles=roles)
    v2 = make_style(rng, block=block, roles=roles, v1=v1)
    if spec.mode in {"onepage", "pdf"}:
        v1.pagination = v2.pagination = "none"
    return {"v1": v1, "v2": v2}


# --------------------------------------------------------------------------------------------- formatting


def fmt_money(amount: float, currency: str, locale: str, v: str = "v1") -> str:
    whole = amount == int(amount)
    if currency == "JPY":
        return f"¥{int(amount):,}" if v == "v1" else f"{int(amount):,} 円"
    if currency == "EUR":
        if locale.startswith(("de", "it", "pt")):
            num = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            return f"{num} €" if v == "v1" else f"EUR {num}"
        if locale.startswith("fr"):
            num = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
            return f"{num} €" if v == "v1" else f"€{amount:,.2f}"
        return f"€{amount:,.2f}"
    if currency == "GBP":
        return f"£{amount:,.2f}" if v == "v1" or not whole else f"£{int(amount):,}"
    if currency == "CHF":
        num = f"{amount:,.2f}".replace(",", "'")
        return f"CHF {num}" if v == "v1" else f"{num} CHF"
    if currency == "CAD":
        return f"C${amount:,.2f}" if v == "v1" else f"CA${amount:,.2f}"
    if currency == "AUD":
        return f"A${amount:,.2f}"
    return f"${amount:,.2f}" if v == "v1" or not whole else f"${int(amount):,}"


def fmt_salary(lo: int | None, hi: int | None, currency: str, v: str) -> str:
    if lo is None or hi is None:
        return "Salary not disclosed" if v == "v1" else "Competitive salary"
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}[currency]
    if v == "v2":
        return f"{symbol}{lo // 1000}k–{symbol}{hi // 1000}k"
    if currency == "EUR":
        a = f"{lo:,}".replace(",", ".")
        b = f"{hi:,}".replace(",", ".")
        return f"{a} € – {b} € per year"
    suffix = " per annum" if currency == "GBP" else " a year"
    return f"{symbol}{lo:,} – {symbol}{hi:,}{suffix}"


def stars_text(rating: float) -> str:
    full = int(rating)
    return "★" * full + ("½" if rating - full >= 0.5 else "") + "☆" * (5 - full - (1 if rating - full >= 0.5 else 0))


STOCK_WORDS = {
    "v1": {
        "in_stock": ["In stock", "In stock", "Ready to ship"],
        "out_of_stock": ["Out of stock", "Sold out", "Currently unavailable"],
        "preorder": ["Pre-order", "Pre-order now", "Pre-order (ships in November)"],
    },
    "v2": {
        "in_stock": ["Available now", "Ships in 24 hours", "Available"],
        "out_of_stock": ["Unavailable", "Out of stock", "Sold out"],
        "preorder": ["Preorder", "Available for preorder", "Preorder today"],
    },
}
ETYPE_WORDS = {
    "v1": {"full_time": "Full-time", "part_time": "Part-time", "contract": "Contract", "internship": "Internship"},
    "v2": {
        "full_time": "Full time",
        "part_time": "Part time",
        "contract": "Freelance / contract",
        "internship": "Intern",
    },
}
REMOTE_WORDS = {
    "v1": {"Remote": "Remote", "Hybrid": "Hybrid", "On-site": "On-site"},
    "v2": {"Remote": "100% remote", "Hybrid": "Hybrid", "On-site": "In office"},
}
CATEGORY_WORDS = {
    "v1": {
        "music": "Music",
        "theatre": "Theatre",
        "comedy": "Comedy",
        "food": "Food & drink",
        "tech": "Tech",
        "family": "Family",
        "art": "Art",
        "sports": "Sports & fitness",
    },
    "v2": {
        "music": "Live music",
        "theatre": "Theatre & stage",
        "comedy": "Comedy",
        "food": "Food",
        "tech": "Technology",
        "family": "Kids & family",
        "art": "Arts",
        "sports": "Sports",
    },
}
TAG_WORDS = {
    "vegan": ("VG", "Vegan"),
    "vegetarian": ("V", "Vegetarian"),
    "gluten_free": ("GF", "Gluten free"),
    "spicy": ("S", "Spicy"),
}


def posted_text(days: int, v: str) -> str:
    if v == "v1":
        if days == 0:
            return "Posted today"
        if days == 1:
            return "Posted yesterday"
        return f"Posted {days} days ago"
    if days == 0:
        return "Today"
    if days % 7 == 0:
        return f"{days // 7}w ago"
    return f"{days}d ago"


# --------------------------------------------------------------------------------------------- page shell


def page(spec: SiteSpec, style: Style, title: str, main: str, *, head: str = "", scripts: str = "") -> str:
    nav = f'<a href="/">Home</a> <a href="{spec.start_path}">{e(_section_label(spec))}</a> <a href="/about/">About</a> <a href="/contact/">Contact</a>'
    if style.v == "v1":
        top = f'<header class="site-header"><a class="logo" href="/">{e(spec.name)}</a><nav>{nav}</nav></header>'
        bottom = f'<footer class="site-footer"><p>© 2026 {e(spec.name)}. Fictional site generated for testing.</p><a href="/privacy/">Privacy</a></footer>'
    else:
        top = f'<div class="topbar"><div class="wrap"><span class="brand"><a href="/">{e(spec.name)}</a></span></div></div><div class="wrap">'
        bottom = f'</div><div class="bottom"><nav>{nav}</nav><small>{e(spec.name)} · fictional · <a href="/privacy/">Privacy</a></small></div>'
    return (
        f'<!doctype html><html lang="{spec.locale}"><head><meta charset="utf-8"><title>{e(title)}</title>'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">{head}</head>'
        f"<body>{top}<main>{main}</main>{bottom}{scripts}</body></html>"
    )


def _section_label(spec: SiteSpec) -> str:
    return {
        "ecommerce": "Shop",
        "jobs": "Jobs",
        "realestate": "Listings",
        "events": "What's on",
        "restaurant": "Menu",
        "saas": "Pricing",
        "registry": "Registers",
    }[spec.vertical]


# --------------------------------------------------------------------------------------------- cards


def _image(style: Style, src: str, alt: str) -> str:
    if style.lazy_images:
        return f'<img class="{style.c("image")}" src="/static/placeholder.svg" data-src="{src}" alt="{e(alt)}" loading="lazy">'
    return f'<img class="{style.c("image")}" src="{src}" alt="{e(alt)}">'


def _rating(style: Style, rating: float | None) -> str:
    c = style.c("rating")
    if rating is None:
        return f'<span class="{c}">No reviews yet</span>'
    if style.rating_style == "text":
        text = f"{rating:g} out of 5" if style.v == "v1" else f"Rated {rating:g}/5"
        return f'<span class="{c}">{text}</span>'
    if style.rating_style == "stars":
        return f'<span class="{c}" title="Customer rating">{stars_text(rating)}</span>'
    if style.rating_style == "class":
        icons = "".join('<i class="star"></i>' for _ in range(int(rating)))
        return f'<div class="{c}" data-rating="{rating:g}">{icons}</div>'
    icons = '<svg width="80" height="16" aria-hidden="true"><use href="#stars"></use></svg>'
    return f'<span class="{c}" role="img" aria-label="Rated {rating:g} out of 5 stars">{icons}</span>'


def ecommerce_card(
    spec: SiteSpec, style: Style, item: dict[str, Any], *, injection: str | None = None
) -> dict[str, str]:
    v = style.v
    title = (
        f'<h3 class="{style.c("title")}"><a href="{item["path"]}">{e(item["name"])}</a></h3>'
        if v == "v1"
        else f'<a class="{style.c("title")}" href="{item["path"]}"><h2>{e(item["name"])}</h2></a>'
    )
    price = fmt_money(item["amount"], item["currency"], spec.locale, v)
    if item.get("old_amount"):
        old = fmt_money(item["old_amount"], item["currency"], spec.locale, v)
        if v == "v1":
            price_html = f'<span class="{style.c("old_price")}"><s>{old}</s></span> <span class="{style.c("price")}">{price}</span>'
        else:
            price_html = f'<p class="{style.c("price")}">Was <s>{old}</s> Now {price}</p>'
    else:
        price_html = (
            f'<span class="{style.c("price")}">{price}</span>'
            if v == "v1"
            else f'<p class="{style.c("price")}">{price}</p>'
        )
    stock = STOCK_WORDS[v][item["stock"]][item["stock_word"] % 3]
    variants = ""
    if item["variants"]:
        if v == "v1":
            chips = "".join(
                f'<li class="{style.c("variant")}">{e(x["name"])}</li>'
                if x["in_stock"]
                else f'<li class="{style.c("variant")} is-soldout" aria-disabled="true"><s>{e(x["name"])}</s></li>'
                for x in item["variants"]
            )
            variants = f'<ul class="{style.c("variants")}">{chips}</ul>'
        else:
            options = "".join(
                f'<option value="{e(x["name"])}">{e(x["name"])}</option>'
                if x["in_stock"]
                else f'<option value="{e(x["name"])}" disabled>{e(x["name"])} - sold out</option>'
                for x in item["variants"]
            )
            variants = f'<label class="{style.c("variants")}">Size <select name="size">{options}</select></label>'
    desc = item["desc"]
    extra = ""
    alt = item["name"]
    if injection == "visible":
        desc = f"{desc} {INJECTION_TEXT}"
    elif injection == "hidden":
        extra = f'<span style="display:none">{INJECTION_TEXT}</span>'
    elif injection == "alt":
        alt = INJECTION_TEXT
    return {
        "image": _image(style, item["image"], alt),
        "title": title,
        "price": price_html,
        "rating": _rating(style, item["rating"]),
        "stock": f'<span class="{style.c("stock")}">{e(stock)}</span>',
        "variants": variants,
        "desc": f'<p class="{style.c("desc")}">{e(desc)}</p>{extra}',
        "button": f'<button class="{style.c("button")}" type="button">{"Add to cart" if v == "v1" else "Buy now"}</button>',
    }


def jobs_card(spec: SiteSpec, style: Style, item: dict[str, Any], *, injection: str | None = None) -> dict[str, str]:
    v = style.v
    title = (
        f'<h3 class="{style.c("title")}"><a href="{item["path"]}">{e(item["title"])}</a></h3>'
        if v == "v1"
        else f'<a class="{style.c("title")}" href="{item["path"]}">{e(item["title"])}</a>'
    )
    contact = item.get("contact", "")
    if injection == "visible":
        contact = (contact + " " if contact else "") + INJECTION_TEXT.replace(
            "price of every product", "salary of every job"
        ).replace("in stock", "remote")
    return {
        "title": title,
        "company": f'<span class="{style.c("company")}">{e(item["company"])}</span>',
        "location": f'<span class="{style.c("location")}">{e(item["location"])}</span>',
        "salary": f'<span class="{style.c("salary")}">{e(fmt_salary(item["lo"], item["hi"], item["currency"], v))}</span>',
        "type": f'<span class="{style.c("type")}">{e(ETYPE_WORDS[v][item["etype"]])}</span>',
        "remote": f'<span class="{style.c("remote")}">{e(REMOTE_WORDS[v][item["remote"]])}</span>',
        "posted": f'<span class="{style.c("posted")}">{e(posted_text(item["days"], v))}</span>',
        "contact": f'<p class="{style.c("desc")}">{e(contact)}</p>' if contact else "",
    }


def realestate_card(
    spec: SiteSpec, style: Style, item: dict[str, Any], *, injection: str | None = None
) -> dict[str, str]:
    v = style.v
    price = fmt_money(item["amount"], item["currency"], spec.locale, v)
    if item["rent"]:
        price += " / month" if v == "v1" else (" pcm" if item["currency"] == "GBP" else " per month")
    unit_text = {"sqft": ("sq ft", "ft²"), "sqm": ("m²", "sqm")}[item["unit"]][0 if v == "v1" else 1]
    area = f"{item['area']:,} {unit_text}" if v == "v1" else f"{item['area']} {unit_text}"
    if v == "v1":
        facts = (
            f'<div class="{style.c("meta")}"><span class="{style.c("beds")}">{item["beds"]} bd</span> · '
            f'<span class="{style.c("baths")}">{item["baths"]:g} ba</span> · <span class="{style.c("area")}">{area}</span></div>'
        )
    else:
        facts = (
            f'<dl class="{style.c("meta")}"><dt>Bedrooms</dt><dd>{item["beds"]}</dd><dt>Bathrooms</dt><dd>{item["baths"]:g}</dd>'
            f"<dt>Floor area</dt><dd>{area}</dd></dl>"
        )
    agent = ""
    if item.get("agent_phone"):
        agent = f'<p class="{style.c("agent")}">Agent: Laura Pike · <a href="tel:{item["agent_phone"]}">{item["agent_phone"]}</a></p>'
    listing = ("For rent" if item["rent"] else "For sale") if v == "v1" else ("To let" if item["rent"] else "Buy")
    title = (
        f'<h3 class="{style.c("address")}"><a href="{item["path"]}">{e(item["address"])}</a></h3>'
        if v == "v1"
        else f'<a class="{style.c("address")}" href="{item["path"]}">{e(item["address"])}</a>'
    )
    return {
        "image": _image(style, f"/img/{slug(item['address'])}.jpg", item["address"]),
        "address": title,
        "city": f'<span class="{style.c("city")}">{e(item["city"])}</span>',
        "price": f'<span class="{style.c("price")}">{e(price)}</span>',
        "listing": f'<span class="{style.c("listing")}">{listing}</span>',
        "facts": facts,
        "agent": agent,
    }


def event_date_html(style: Style, item: dict[str, Any], locale: str) -> str:
    date = item["date"]
    days = item["days"]
    c = style.c("date")
    if style.v == "v1":
        if days == 0:
            text = "Today, 19:30"
        elif days == 1:
            text = "Tomorrow, 20:00"
        elif days < 7:
            text = f"This {date.strftime('%A')}"
        else:
            text = date.strftime("%a, %b %-d") if locale.startswith("en-US") else date.strftime("%a %-d %b")
        return f'<span class="{c}">{text}</span>'
    text = date.strftime("%d.%m.%Y") if locale.startswith("de") else date.strftime("%-d %B %Y")
    return f'<time class="{c}" datetime="{date.isoformat()}">{text}</time>'


def events_card(spec: SiteSpec, style: Style, item: dict[str, Any], *, injection: str | None = None) -> dict[str, str]:
    v = style.v
    price = "Free" if item["amount"] == 0 else fmt_money(item["amount"], item["currency"], spec.locale, v)
    if v == "v2" and item["amount"]:
        price = f"Tickets {price}"
    title = (
        f'<h3 class="{style.c("title")}"><a href="{item["path"]}">{e(item["name"])}</a></h3>'
        if v == "v1"
        else f'<a class="{style.c("title")}" href="{item["path"]}"><strong>{e(item["name"])}</strong></a>'
    )
    venue = e(item["venue"])
    if item.get("contact"):
        venue += f' <small class="{style.c("desc")}">{e(item["contact"])}</small>'
    return {
        "title": title,
        "date": event_date_html(style, item, spec.locale),
        "venue": f'<span class="{style.c("venue")}">{venue}</span>',
        "price": f'<span class="{style.c("price")}">{e(price)}</span>',
        "category": f'<span class="{style.c("category")}">{e(CATEGORY_WORDS[v][item["category"]])}</span>',
    }


CARD_RENDERERS: dict[str, Callable[..., dict[str, str]]] = {
    "ecommerce": ecommerce_card,
    "jobs": jobs_card,
    "realestate": realestate_card,
    "events": events_card,
}


def card(spec: SiteSpec, style: Style, item: dict[str, Any], *, injection: str | None = None, attrs: str = "") -> str:
    parts = CARD_RENDERERS[spec.vertical](spec, style, item, injection=injection)
    ordered = [parts[role] for role in style.order if parts.get(role)]
    if style.wrap:
        media = [parts[r] for r in style.order if r == "image" and parts.get(r)]
        text = [parts[r] for r in style.order if r != "image" and parts.get(r)]
        inner = "".join(media) + f'<div class="{style.c("body")}">' + "".join(text) + "</div>"
    else:
        inner = "".join(ordered)
    return f'<{style.item_tag} class="{style.c("card")}"{attrs}>{inner}</{style.item_tag}>'


# --------------------------------------------------------------------------------------------- pagination


def page_url(style: Style, base: str, n: int, per_page: int) -> str:
    if n == 1:
        return base
    scheme = style.pagination
    if scheme == "query" or scheme == "show_more":
        return f"{base}?page={n}"
    if scheme == "path":
        return f"{base}page/{n}/"
    if scheme == "rel_next":
        return f"{base}?p={n}"
    if scheme == "numbered":
        return f"{base}?pg={n}"
    if scheme == "offset":
        return f"{base}?start={(n - 1) * per_page}"
    return base


def pager_html(style: Style, base: str, n: int, pages: int, per_page: int) -> tuple[str, str]:
    """(html in the page, extra <head> tags)."""
    if pages <= 1:
        return "", ""
    c = style.c("pager")
    scheme = style.pagination
    head = ""
    links: list[str] = []
    if scheme in {"query", "path", "offset"}:
        if n > 1:
            links.append(f'<a href="{page_url(style, base, n - 1, per_page)}">← Previous</a>')
        links.append(f"<span>Page {n} of {pages}</span>")
        if n < pages:
            label = {"query": "Next →", "path": "Older »", "offset": f"Next {per_page} →"}[scheme]
            links.append(f'<a class="{style.c("next")}" href="{page_url(style, base, n + 1, per_page)}">{label}</a>')
    elif scheme == "rel_next":
        if n < pages:
            head = f'<link rel="next" href="{page_url(style, base, n + 1, per_page)}">'
            links.append(f'<a rel="next" href="{page_url(style, base, n + 1, per_page)}">More results</a>')
        if n > 1:
            links.append(f'<a rel="prev" href="{page_url(style, base, n - 1, per_page)}">Back</a>')
    elif scheme == "numbered":
        for k in range(1, pages + 1):
            links.append(f"<b>{k}</b>" if k == n else f'<a href="{page_url(style, base, k, per_page)}">{k}</a>')
    elif scheme == "show_more" and n < pages:
        links.append(
            f'<a class="{style.c("next")} load-more" href="{page_url(style, base, n + 1, per_page)}">Show more</a>'
        )
    return f'<nav class="{c}" aria-label="Pagination">{"".join(links)}</nav>', head


# --------------------------------------------------------------------------------------------- list pages


def list_main(spec: SiteSpec, style: Style, chunk: list[dict[str, Any]], *, first: bool, heading: str) -> str:
    body: list[str] = []
    for item in chunk:
        index = spec.items.index(item)
        injection = None
        if spec.injection and index == 2:
            injection = spec.injection
        body.append(card(spec, style, item, injection=injection))
    if first:
        for hidden in spec.hidden:
            if "hidden_template" in spec.traps:
                body.insert(1, f"<template>{card(spec, style, hidden)}</template>")
            elif style.v == "v1":
                body.insert(1, card(spec, style, hidden, attrs=" hidden"))
            else:
                body.insert(1, card(spec, style, hidden, attrs=' style="display:none" aria-hidden="true"'))
    featured = ""
    if spec.featured is not None:
        label = "Staff pick" if style.v == "v1" else "Featured"
        featured = f'<section class="{style.c("featured")}"><h2>{label}</h2>{card(spec, style, spec.items[spec.featured])}</section>'
    return (
        f'<h1>{e(heading)}</h1>{featured}<{style.list_tag} class="{style.c("list")}">{"".join(body)}</{style.list_tag}>'
    )


def render_list_pages(spec: SiteSpec, style: Style, files: Files) -> None:
    per = spec.per_page
    chunks = [spec.items[i : i + per] for i in range(0, len(spec.items), per)]
    base = spec.start_path
    heading = {
        "ecommerce": "All products",
        "jobs": "Open positions",
        "realestate": "Properties",
        "events": "Upcoming events",
    }[spec.vertical]
    if style.v == "v2":
        heading = {
            "ecommerce": "Shop everything",
            "jobs": "Jobs board",
            "realestate": "Homes available",
            "events": "Calendar",
        }[spec.vertical]
    for n, chunk in enumerate(chunks, start=1):
        pager, head = pager_html(style, base, n, len(chunks), per)
        main = list_main(spec, style, chunk, first=n == 1, heading=heading) + pager
        if spec.archived:
            main += '<p class="archive-link"><a href="/archive/">Archived products</a></p>'
        url = urlsplit(page_url(style, base, n, per))
        files[storage_path(url.path, url.query)] = (
            "text/html",
            page(spec, style, f"{heading} - {spec.name}", main, head=head).encode(),
        )
    if spec.archived:
        main = list_main(
            spec.__class__(
                **{**spec.__dict__, "items": spec.archived, "hidden": [], "featured": None, "injection": None}
            ),
            style,
            spec.archived,
            first=False,
            heading="Archive",
        )
        files[storage_path("/archive/")] = ("text/html", page(spec, style, "Archive", main).encode())


def render_detail_pages(spec: SiteSpec, style: Style, files: Files) -> None:
    for item in spec.items + spec.hidden + spec.archived:
        path = item.get("path")
        if not path:
            continue
        if spec.vertical == "ecommerce":
            price = fmt_money(item["amount"], item["currency"], spec.locale, style.v)
            sku = (
                f'<dl class="specs"><dt>SKU</dt><dd>{item["sku"]}</dd><dt>Brand</dt><dd>{e(spec.name)}</dd></dl>'
                if style.v == "v1"
                else f'<p class="item-meta">Item no. <span>{item["sku"]}</span> · Sold by {e(spec.name)}</p>'
            )
            crumbs = f'<nav class="crumbs"><a href="/">Home</a> / <a href="{spec.start_path}">Shop</a> / {e(item["name"])}</nav>'
            main = (
                f'{crumbs}<article class="product-detail"><h1>{e(item["name"])}</h1>{_image(style, item["image"], item["name"])}'
                f'<p class="detail-price">{price}</p>{sku}<p>{e(item["desc"])}</p></article>'
            )
            title = item["name"]
        else:
            title = item.get("title") or item.get("name") or item.get("address") or ""
            main = f"<article><h1>{e(str(title))}</h1><p>Details for {e(str(title))}. Fictional listing generated for testing.</p></article>"
        files[storage_path(path)] = ("text/html", page(spec, style, f"{title} - {spec.name}", main).encode())


# --------------------------------------------------------------------------------------------- JavaScript sites


def render_spa(spec: SiteSpec, style: Style, files: Files) -> None:
    per = spec.per_page
    chunks = [spec.items[i : i + per] for i in range(0, len(spec.items), per)]
    endpoint = "/api/catalog.json" if style.v == "v1" else "/api/v2/items.json"
    param = "page" if style.v == "v1" else "p"
    for n, chunk in enumerate(chunks, start=1):
        cards = "".join(card(spec, style, item) for item in chunk)
        payload = {
            "html": f'<{style.list_tag} class="{style.c("list")}">{cards}</{style.list_tag}>',
            "page": n,
            "pages": len(chunks),
        }
        files[storage_path(endpoint, f"{param}={n}")] = ("application/json", json.dumps(payload).encode())
    script = (
        "<script>(function(){var q=new URLSearchParams(location.search);var n=Number(q.get('" + param + "')||1);"
        "fetch('" + endpoint + "?" + param + "='+n).then(function(r){return r.json()}).then(function(d){"
        "var app=document.getElementById('app');app.innerHTML=d.html;var nav=document.createElement('nav');nav.className='pager';"
        "for(var i=1;i<=d.pages;i++){var a=document.createElement('a');a.href='?"
        + param
        + "='+i;a.textContent=String(i);if(i===n){a.setAttribute('aria-current','page')}nav.appendChild(a)}"
        "if(n<d.pages){var nx=document.createElement('a');nx.href='?"
        + param
        + "='+(n+1);nx.rel='next';nx.textContent='Next';nav.appendChild(nx)}"
        "app.appendChild(nav)})})();</script>"
    )
    shell = f'<h1>{"Catalog" if style.v == "v1" else "Browse"}</h1><div id="app" class="app-root"><p class="loading">Loading…</p></div>'
    files[storage_path(spec.start_path)] = (
        "text/html",
        page(spec, style, f"{spec.name}", shell, scripts=script).encode(),
    )


def render_infinite(spec: SiteSpec, style: Style, files: Files) -> None:
    per = spec.per_page
    chunks = [spec.items[i : i + per] for i in range(0, len(spec.items), per)]
    button_mode = (spec.mode == "infinite_button") == (style.v == "v1")
    endpoint = "/api/more.json" if style.v == "v1" else "/api/feed.json"
    for n, chunk in enumerate(chunks[1:], start=1):
        cards = "".join(card(spec, style, item) for item in chunk)
        nxt = n * per + per if n + 1 < len(chunks) else None
        files[storage_path(endpoint, f"cursor={n * per}")] = (
            "application/json",
            json.dumps({"html": cards, "next": nxt}).encode(),
        )
    first = "".join(card(spec, style, item) for item in chunks[0])
    listing = f'<{style.list_tag} id="feed" class="{style.c("list")}">{first}</{style.list_tag}>'
    if button_mode:
        control = f'<button id="more" class="{style.c("next")}" type="button" data-cursor="{per}">{"Load more" if style.v == "v1" else "Show more results"}</button>'
        trigger = "document.getElementById('more').addEventListener('click',load);"
        done = "document.getElementById('more').remove();"
    else:
        control = '<div id="sentinel" style="height:1px"></div>'
        trigger = "new IntersectionObserver(function(es){if(es[0].isIntersecting){load()}}).observe(document.getElementById('sentinel'));"
        done = "document.getElementById('sentinel').remove();"
    script = (
        "<script>(function(){var cursor="
        + str(per)
        + ";var busy=false;function load(){if(busy||cursor===null)return;busy=true;"
        "fetch('" + endpoint + "?cursor='+cursor).then(function(r){return r.json()}).then(function(d){"
        "document.getElementById('feed').insertAdjacentHTML('beforeend',d.html);cursor=d.next;busy=false;if(cursor===null){"
        + done
        + "}})}"
        + trigger
        + "})();</script>"
    )
    heading = "Latest" if style.v == "v1" else "Everything we have"
    files[storage_path(spec.start_path)] = (
        "text/html",
        page(spec, style, spec.name, f"<h1>{heading}</h1>{listing}{control}", scripts=script).encode(),
    )


# --------------------------------------------------------------------------------------------- one-page sites


def _tags_html(tags: list[str], v: str) -> str:
    if v == "v1":
        return "".join(f' <abbr class="diet" title="{TAG_WORDS[t][1]}">{TAG_WORDS[t][0]}</abbr>' for t in tags)
    return "".join(f' <img class="icon" src="/icons/{t}.svg" alt="{TAG_WORDS[t][1]}" width="14">' for t in tags)


def render_menu_html(spec: SiteSpec, style: Style, files: Files) -> None:
    sections: dict[str, list[dict[str, Any]]] = {}
    for item in spec.items:
        sections.setdefault(item["section"], []).append(item)
    blocks = []
    for section, dishes in sections.items():
        if style.v == "v1":
            rows = "".join(
                f'<li class="dish"><span class="dish-name">{e(d["dish"])}</span>{_tags_html(d["tags"], "v1")}'
                f'<span class="dish-price">{fmt_money(d["amount"], d["currency"], spec.locale, "v1")}</span></li>'
                for d in dishes
            )
            blocks.append(f'<section class="menu-section"><h2>{e(section)}</h2><ul>{rows}</ul></section>')
        else:
            rows = "".join(
                f'<div class="row"><div class="cell price">{fmt_money(d["amount"], d["currency"], spec.locale, "v2")}</div>'
                f'<div class="cell"><b>{e(d["dish"])}</b>{_tags_html(d["tags"], "v2")}</div></div>'
                for d in dishes
            )
            blocks.append(
                f'<div class="course" data-course="{slug(section)}"><h3>{e(section.upper())}</h3>{rows}</div>'
            )
    legend = '<p class="legend">VG vegan · V vegetarian · GF gluten free · S spicy</p>' if style.v == "v1" else ""
    main = f"<h1>{'Our menu' if style.v == 'v1' else 'Food & drink'}</h1>{''.join(blocks)}{legend}"
    files[storage_path(spec.start_path)] = ("text/html", page(spec, style, f"Menu - {spec.name}", main).encode())


def render_menu_pdf(spec: SiteSpec, style: Style, files: Files) -> None:
    lines: list[Line] = [
        Line(spec.name, 18, True, gap=24),
        Line("Menu" if style.v == "v1" else "Seasonal menu", 12, gap=22),
    ]
    current = None
    for item in spec.items:
        if item["section"] != current:
            current = item["section"]
            lines.append(Line(current if style.v == "v1" else current.upper(), 14, True, gap=26))
        price = fmt_money(item["amount"], item["currency"], spec.locale, style.v)
        tags = ", ".join(TAG_WORDS[t][0] for t in item["tags"])
        if style.v == "v1":
            text = f"{item['dish']}{f' ({tags})' if tags else ''} .......... {price}"
        else:
            words = ", ".join(TAG_WORDS[t][1].lower() for t in item["tags"])
            text = f"{price}   {item['dish']}{f'  [{words}]' if words else ''}"
        lines.append(Line(text))
    lines.append(Line("VG vegan, V vegetarian, GF gluten free, S spicy. Prices include VAT.", 9, gap=28))
    pdf_path = "/menu.pdf" if style.v == "v1" else "/files/menu-autumn.pdf"
    files[storage_path(pdf_path)] = ("application/pdf", make_pdf(lines))
    link = "Download our menu (PDF)" if style.v == "v1" else "See the seasonal menu"
    main = (
        f"<h1>Welcome to {e(spec.name)}</h1><p>Seasonal cooking, open Tuesday to Sunday.</p>"
        f'<p><a class="menu-link" href="{pdf_path}">{link}</a></p><p><a href="/about/">Our story</a> · <a href="/contact/">Book a table</a></p>'
    )
    files[storage_path("/")] = ("text/html", page(spec, style, spec.name, main).encode())


def render_saas(spec: SiteSpec, style: Style, files: Files) -> None:
    def price_text(amount: float | None, annual: bool) -> str:
        if amount is None:
            return "Custom pricing - contact sales"
        if amount == 0:
            return "Free" if not annual else "Free forever"
        money = fmt_money(amount, spec.currency, spec.locale, style.v)
        return f"{money} per month, billed annually" if annual else f"{money}/month"

    def seats_text(seats: int | None) -> str:
        if seats is None:
            return "Unlimited users"
        return "1 user" if seats == 1 else f"Up to {seats} users"

    if style.v == "v1":
        cards = []
        for item in spec.items:
            features = "".join(f"<li>{e(f)}</li>" for f in item["features"])
            cards.append(
                f'<div class="plan-card"><h3 class="plan-name">{e(item["plan"])}</h3><p class="plan-price">{e(price_text(item["monthly"], False))}</p>'
                f'<p class="plan-annual">{e(price_text(item["annual"], True))}</p><p class="plan-seats">{seats_text(item["seats"])}</p>'
                f'<ul class="plan-features">{features}</ul><a class="cta" href="/signup/">Get started</a></div>'
            )
        main = f'<h1>Simple pricing</h1><div class="plans">{"".join(cards)}</div>'
    else:
        head = "".join(f"<th>{e(i['plan'])}</th>" for i in spec.items)
        all_features: list[str] = []
        for item in spec.items:
            all_features.extend(f for f in item["features"] if f not in all_features)
        rows = [
            "<tr><th>Monthly</th>"
            + "".join(f"<td>{e(price_text(i['monthly'], False))}</td>" for i in spec.items)
            + "</tr>",
            "<tr><th>Annual billing</th>"
            + "".join(f"<td>{e(price_text(i['annual'], True))}</td>" for i in spec.items)
            + "</tr>",
            "<tr><th>Seats</th>" + "".join(f"<td>{seats_text(i['seats'])}</td>" for i in spec.items) + "</tr>",
        ]
        for feature in all_features:
            rows.append(
                f"<tr><th>{e(feature)}</th>"
                + "".join(f"<td>{'✓' if feature in i['features'] else '—'}</td>" for i in spec.items)
                + "</tr>"
            )
        main = f'<h1>Plans &amp; pricing</h1><table class="compare"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
    files[storage_path(spec.start_path)] = ("text/html", page(spec, style, f"Pricing - {spec.name}", main).encode())


def render_registry(spec: SiteSpec, style: Style, files: Files) -> None:
    per = spec.per_page
    chunks = [spec.items[i : i + per] for i in range(0, len(spec.items), per)]
    grouped = "group_header_rows" in spec.traps and style.v == "v1"
    columns = ["facility", "district", "type", "capacity", "opened", "phone"]
    labels = {
        "facility": "Facility",
        "district": "District",
        "type": "Type",
        "capacity": "Capacity",
        "opened": "Opened",
        "phone": "Telephone",
    }
    if style.v == "v2":
        columns = ["facility", "type", "district", "phone", "opened", "capacity"]
        labels = {
            "facility": "Name of facility",
            "district": "Area",
            "type": "Category",
            "capacity": "Max. visitors",
            "opened": "Opening date",
            "phone": "Public line",
        }
    if grouped:
        columns = [c for c in columns if c != "district"]
    total_capacity = sum(i["capacity"] for i in spec.items)
    for n, chunk in enumerate(chunks, start=1):
        rows: list[str] = []
        current = None
        for item in chunk:
            if grouped and item["district"] != current:
                current = item["district"]
                rows.append(f'<tr class="group"><th colspan="{len(columns)}">{e(current)} district</th></tr>')
            cells = []
            for col in columns:
                if col == "facility":
                    value = e(item["facility"]) + ("<sup>1</sup>" if item["footnote"] else "")
                elif col == "type":
                    value = e(item["label"])
                elif col == "opened":
                    value = (
                        item["opened"].strftime("%d/%m/%Y") if style.v == "v1" else item["opened"].strftime("%-d %B %Y")
                    )
                elif col == "capacity":
                    value = f"{item['capacity']:,}"
                else:
                    value = e(str(item[col]))
                cells.append(f"<td>{value}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        if n == len(chunks):
            span = len(columns) - 1
            rows.append(
                f'<tr class="total"><td>Total capacity (all facilities)</td><td colspan="{span}">{total_capacity:,}</td></tr>'
            )
        header = "".join(f'<th scope="col">{labels[c]}</th>' for c in columns)
        pager, head = pager_html(style, spec.start_path, n, len(chunks), per)
        notes = '<p class="notes"><sup>1</sup> Temporarily closed for refurbishment.</p>'
        caption = "Register of public facilities" if style.v == "v1" else "Public facilities: official register"
        main = (
            f"<h1>{caption}</h1><p>Published under the open government licence. Last updated 1 October 2026.</p>"
            f'<table class="{"registry" if style.v == "v1" else "data-table striped"}"><thead><tr>{header}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
            f"{notes}{pager}"
        )
        url = urlsplit(page_url(style, spec.start_path, n, per))
        files[storage_path(url.path, url.query)] = (
            "text/html",
            page(spec, style, f"{caption} - {spec.name}", main, head=head).encode(),
        )


# --------------------------------------------------------------------------------------------- site


def robots_txt(spec: SiteSpec) -> str:
    lines = ["User-agent: *"]
    lines += [f"Disallow: {path}" for path in spec.robots_disallow]
    if spec.crawl_delay:
        lines.append(f"Crawl-delay: {spec.crawl_delay:g}")
    lines += ["", "User-agent: BadBot", "Disallow: /", "", f"Sitemap: https://{spec.domain}/sitemap.xml"]
    return "\n".join(lines) + "\n"


def render_site(spec: SiteSpec, layout: str) -> Files:
    style = styles_for(spec)[layout]
    files: Files = {}
    files["robots.txt"] = ("text/plain", robots_txt(spec).encode())
    if spec.mode == "static":
        render_list_pages(spec, style, files)
        render_detail_pages(spec, style, files)
    elif spec.mode == "spa":
        render_spa(spec, style, files)
        render_detail_pages(spec, style, files)
    elif spec.mode in {"infinite_button", "infinite_scroll"}:
        render_infinite(spec, style, files)
        render_detail_pages(spec, style, files)
    elif spec.mode == "pdf":
        render_menu_pdf(spec, style, files)
    elif spec.mode == "onepage" and spec.vertical == "restaurant":
        render_menu_html(spec, style, files)
    elif spec.mode == "onepage":
        render_saas(spec, style, files)
    elif spec.mode == "table":
        render_registry(spec, style, files)
    home = storage_path("/")
    if home not in files:
        main = f'<h1>{e(spec.name)}</h1><p>Welcome. <a href="{spec.start_path}">{e(_section_label(spec))}</a></p>'
        files[home] = ("text/html", page(spec, style, spec.name, main).encode())
    files[storage_path("/about/")] = (
        "text/html",
        page(spec, style, "About", f"<h1>About {e(spec.name)}</h1><p>A fictional organisation.</p>").encode(),
    )
    files[storage_path("/contact/")] = (
        "text/html",
        page(
            spec,
            style,
            "Contact",
            f'<h1>Contact</h1><p>Email <a href="mailto:info@{spec.domain}">info@{spec.domain}</a>.</p>',
        ).encode(),
    )
    files[storage_path("/static/placeholder.svg")] = (
        "image/svg+xml",
        b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>',
    )
    return files
