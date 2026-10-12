"""URL canonicalization: one spelling per page, so the frontier fetches it once.

Lower-cased scheme and host, no default port, no fragment, no tracking parameters (utm_*, gclid, fbclid, ...),
query parameters sorted, "index.html" folded into its directory, "/./" and "//" collapsed."""

from __future__ import annotations

import posixpath
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "dclid",
        "yclid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "ref",
        "ref_src",
        "igshid",
        "sessionid",
        "sid",
        "phpsessid",
        "jsessionid",
    }
)
SKIP_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".css",
    ".js",
    ".mp4",
    ".mp3",
    ".zip",
    ".woff",
    ".woff2",
    ".ttf",
    ".avif",
)


def canonicalize(url: str, base: str | None = None) -> str:
    if base is not None:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    netloc = host if port is None or (scheme, port) in {("http", 80), ("https", 443)} else f"{host}:{port}"
    path = parts.path or "/"
    trailing = path.endswith("/")
    path = posixpath.normpath(path.replace("//", "/"))
    if path == ".":
        path = "/"
    if trailing and not path.endswith("/"):
        path += "/"
    if path.endswith(("/index.html", "/index.htm")):
        path = path.rsplit("/", 1)[0] + "/"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    query.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def same_site(a: str, b: str) -> bool:
    """Same registrable-ish host: www.x.com and x.com count as one site."""
    ha, hb = host_of(a), host_of(b)

    def strip(h: str) -> str:
        return h[4:] if h.startswith("www.") else h

    return bool(ha) and strip(ha) == strip(hb)


def is_resource(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(SKIP_SUFFIXES)


def is_http(url: str) -> bool:
    return urlsplit(url).scheme in {"http", "https"}
