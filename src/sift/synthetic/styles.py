"""Layout styles for the synthetic web. A site's v1 and v2 differ the way a real redesign does: class names follow a
different convention (BEM, abbreviations, CSS-module hashes, utility classes with no semantic hooks), cards use other
tags, fields move and get wrapped, wording changes ("In stock" -> "Available now", "Posted 3 days ago" -> "3d ago")
and pagination switches scheme (?page=N, /page/N/, rel=next, numbered links, ?start=N, "Show more")."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

NAMINGS = ("bem", "short", "hashed", "utility")
PAGINATIONS = ("query", "path", "rel_next", "numbered", "offset", "show_more")

_ABBR = {
    "list": "lst",
    "card": "crd",
    "title": "ttl",
    "price": "prc",
    "old_price": "was",
    "rating": "rtg",
    "stock": "stk",
    "image": "img",
    "variants": "vrs",
    "variant": "vr",
    "desc": "dsc",
    "button": "btn",
    "company": "co",
    "location": "loc",
    "salary": "sal",
    "type": "typ",
    "remote": "rmt",
    "posted": "pst",
    "address": "adr",
    "city": "cty",
    "beds": "bd",
    "baths": "ba",
    "area": "ar",
    "listing": "lt",
    "agent": "agt",
    "date": "dt",
    "venue": "ven",
    "category": "cat",
    "body": "bdy",
    "meta": "mta",
    "pager": "pgr",
    "next": "nxt",
    "featured": "ftd",
}
# Utility-class soup (Tailwind-like): several roles share the same classes, so selectors must use structure.
_UTILITY = {
    "list": "grid grid-cols-3 gap-6",
    "card": "rounded-lg border p-4 shadow-sm",
    "title": "mt-2 text-lg font-semibold",
    "price": "mt-1 font-bold",
    "old_price": "mt-1 text-sm line-through text-gray-400",
    "rating": "text-sm text-amber-600",
    "stock": "text-sm text-gray-600",
    "image": "aspect-square w-full object-cover",
    "variants": "mt-2 flex gap-1",
    "variant": "rounded border px-2 text-xs",
    "desc": "mt-2 text-sm text-gray-500",
    "button": "mt-3 rounded bg-black px-3 py-1 text-white",
    "company": "text-sm text-gray-600",
    "location": "text-sm text-gray-600",
    "salary": "mt-1 font-bold",
    "type": "text-xs uppercase",
    "remote": "text-xs uppercase",
    "posted": "text-xs text-gray-400",
    "address": "mt-2 text-lg font-semibold",
    "city": "text-sm text-gray-600",
    "beds": "text-sm",
    "baths": "text-sm",
    "area": "text-sm",
    "listing": "text-xs uppercase",
    "agent": "text-xs text-gray-500",
    "date": "text-sm font-medium",
    "venue": "text-sm text-gray-600",
    "category": "text-xs uppercase",
    "body": "flex flex-col",
    "meta": "flex gap-2",
    "pager": "mt-8 flex gap-2",
    "next": "rounded border px-3",
    "featured": "mb-8 rounded-xl bg-amber-50 p-4",
}


@dataclass
class Style:
    naming: str
    block: str
    salt: str
    item_tag: str
    list_tag: str
    order: list[str]
    wrap: bool
    """Wrap the text fields of a card in an extra body element (moves elements one level down)."""
    pagination: str
    words: dict[str, str] = field(default_factory=dict)
    lazy_images: bool = False
    rating_style: str = "text"
    date_style: str = "relative"
    v: str = "v1"

    def c(self, role: str) -> str:
        if self.naming == "bem":
            return self.block if role == "card" else f"{self.block}__{role.replace('_', '-')}"
        if self.naming == "short":
            prefix = "".join(part[0] for part in self.block.split("-"))
            return f"{prefix}-{_ABBR.get(role, role[:3])}"
        if self.naming == "hashed":
            digest = hashlib.sha1(f"{self.salt}:{self.block}:{role}".encode()).hexdigest()[:5]
            component = "".join(part.title() for part in self.block.split("-"))
            return f"{component}_{role.replace('_', '')}__{digest}"
        return _UTILITY.get(role, "text-sm")


def make_style(rng: random.Random, *, block: str, roles: list[str], v1: Style | None = None) -> Style:
    """A v1 style, or (with `v1`) a v2 that differs from it in naming, tags, order, wrapping and pagination."""
    if v1 is None:
        naming = rng.choice(NAMINGS[:3]) if rng.random() < 0.8 else "utility"
        pagination = rng.choice(PAGINATIONS)
        item_tag = rng.choice(["div", "li", "article"])
        order = list(roles)
        return Style(
            naming=naming,
            block=block,
            salt=f"v1{rng.random():.6f}",
            item_tag=item_tag,
            list_tag="ul" if item_tag == "li" else rng.choice(["div", "section"]),
            order=order,
            wrap=rng.random() < 0.4,
            pagination=pagination,
            lazy_images=rng.random() < 0.3,
            rating_style=rng.choice(["text", "stars", "class", "aria"]),
            v="v1",
        )
    naming = rng.choice([n for n in NAMINGS if n != v1.naming])
    pagination = rng.choice([p for p in PAGINATIONS if p != v1.pagination])
    item_tag = rng.choice([t for t in ("div", "li", "article") if t != v1.item_tag])
    order = list(roles)
    # Move fields: the image after the text, the price before the title, the stock line to the top...
    for _ in range(3):
        i, j = rng.randrange(len(order)), rng.randrange(len(order))
        order[i], order[j] = order[j], order[i]
    if order == v1.order:
        order = list(reversed(order))
    new_block = {"product-card": "item-tile", "job-card": "posting", "listing-card": "property", "event-card": "happening"}.get(
        v1.block, v1.block + "-x"
    )
    return Style(
        naming=naming,
        block=new_block,
        salt=f"v2{rng.random():.6f}",
        item_tag=item_tag,
        list_tag="ul" if item_tag == "li" else ("section" if v1.list_tag != "section" else "div"),
        order=order,
        wrap=not v1.wrap,
        pagination=pagination,
        lazy_images=not v1.lazy_images,
        rating_style=rng.choice([s for s in ("text", "stars", "class", "aria") if s != v1.rating_style]),
        v="v2",
    )
