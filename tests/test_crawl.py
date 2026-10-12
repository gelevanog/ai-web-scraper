from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from sift.crawl.discovery import needs_browser, pagination_links, relevant_links, request_words
from sift.crawl.fetch import DiskSource, HttpFetcher, SyntheticTransport
from sift.crawl.polite import RateLimiter, RequestLog, RobotsPolicy
from sift.crawl.urls import canonicalize, same_site
from sift.extract.dom import parse

UA = "SiftBot/0.1 (+https://github.com/gelevanog/ai-web-scraper; test)"


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


# ---------------------------------------------------------------------------------------------- canonical URLs


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("HTTPS://Shop.Example:443/catalog/index.html#top", "https://shop.example/catalog/"),
        ("https://shop.example/a/./b/../c?utm_source=x&page=2&b=1", "https://shop.example/a/c?b=1&page=2"),
        ("http://shop.example:80//p//x/", "http://shop.example/p/x/"),
        ("https://shop.example/p?gclid=abc&sessionid=1", "https://shop.example/p"),
        ("https://shop.example:8443/x", "https://shop.example:8443/x"),
    ],
)
def test_canonicalize(raw: str, want: str) -> None:
    assert canonicalize(raw) == want


def test_canonicalize_relative_and_same_site() -> None:
    assert canonicalize("../p/x/", "https://a.example/catalog/page/2/") == "https://a.example/catalog/page/p/x/"
    assert canonicalize("/p/x/", "https://a.example/catalog/page/2/") == "https://a.example/p/x/"
    assert same_site("https://www.a.example/x", "https://a.example/y")
    assert not same_site("https://a.example/x", "https://b.example/y")


# ---------------------------------------------------------------------------------------------- robots and rate


def test_robots_rfc9309_status_handling() -> None:
    answers = {
        "https://ok.example/robots.txt": (
            200,
            "User-agent: *\nDisallow: /private/\nCrawl-delay: 3\n\nUser-agent: SiftBot\nDisallow: /nosift/\n",
        ),
        "https://missing.example/robots.txt": (404, ""),
        "https://broken.example/robots.txt": (503, ""),
    }
    policy = RobotsPolicy(lambda url: answers[url], UA)
    assert policy.allowed("https://ok.example/catalog/")
    assert not policy.allowed("https://ok.example/nosift/x")  # agent-specific group wins
    assert policy.allowed("https://missing.example/anything")  # 4xx: no restrictions
    assert not policy.allowed("https://broken.example/anything")  # 5xx: assume disallowed
    generic = RobotsPolicy(lambda url: answers[url], "OtherBot/1.0")
    assert not generic.allowed("https://ok.example/private/x")
    assert generic.crawl_delay("https://ok.example/") == 3.0


def test_rate_limiter_per_host_with_fake_clock() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock.clock, sleep=clock.sleep)
    for _ in range(3):
        limiter.wait("a.example")
    limiter.wait("b.example")
    assert clock.slept == [1.0, 1.0]
    limiter.set_interval("a.example", 2.5)  # e.g. robots.txt Crawl-delay
    limiter.wait("a.example")
    assert clock.slept[-1] == pytest.approx(2.5)  # counted from the previous request
    limiter.wait("a.example")
    assert clock.slept[-1] == pytest.approx(2.5)


def test_request_log_rates() -> None:
    from sift.crawl.polite import RequestLogEntry

    log = RequestLog()
    for t in (0.0, 1.0, 2.5, 3.5):
        log.add(RequestLogEntry(t, "a.example", "u", 200))
    assert log.min_gap("a.example") == 1.0
    assert log.max_rate("a.example", window=10) == pytest.approx(0.4)
    assert log.min_gap("b.example") is None


def _fetcher(handler: Any, *, interval: float = 0.0) -> tuple[HttpFetcher, RequestLog]:
    log = RequestLog()
    fetcher = HttpFetcher(
        user_agent=UA, limiter=RateLimiter(interval), request_log=log, transport=httpx.MockTransport(handler), retries=2
    )
    return fetcher, log


def test_fetcher_sends_user_agent_and_retries_429(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if len([c for c in calls if c.url.path == "/p"]) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, html="<p>ok</p>")

    fetcher, log = _fetcher(handler)
    fetcher.limiter.penalize = lambda host, seconds: None  # type: ignore[method-assign]
    assert fetcher.allowed("https://site.example/p")
    result = fetcher.fetch("https://site.example/p")
    assert result.ok and result.text == "<p>ok</p>"
    assert all(c.headers["user-agent"] == UA for c in calls)
    assert [e.status for e in log.entries if e.kind == "page"] == [429, 200]


def test_fetcher_does_not_follow_offsite_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "site.example" and request.url.path == "/go":
            return httpx.Response(302, headers={"location": "https://elsewhere.example/landing"})
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, html="landing")

    fetcher, log = _fetcher(handler)
    result = fetcher.fetch("https://site.example/go")
    assert result.status == 310 and "off-site" in (result.error or "")
    assert not any(e.host == "elsewhere.example" for e in log.entries)


def test_synthetic_transport_serves_files_and_falls_back(web_root: Path, site: Any) -> None:
    shop = site("shop-01")
    transport = SyntheticTransport(
        DiskSource(web_root, "v1"), fallback=httpx.MockTransport(lambda r: httpx.Response(204))
    )
    with httpx.Client(transport=transport) as client:
        page = client.get(shop["start_url"])
        assert page.status_code == 200 and "Alpine Outfitters" in page.text
        assert client.get("https://alpine-outfitters.example/nope/").status_code == 404
        assert client.get("https://real.example.org/").status_code == 204  # not .example: the real transport
        robots = client.get("https://alpine-outfitters.example/robots.txt")
        assert robots.headers["content-type"].startswith("text/plain")


# ---------------------------------------------------------------------------------------------- discovery


@pytest.mark.parametrize(
    ("html", "want"),
    [
        (
            '<head><link rel="next" href="/c/?p=2"></head><body><a rel="next" href="/c/?p=2">More results</a></body>',
            ["https://s.example/c/?p=2"],
        ),
        ('<a href="/c/?page=2">Next →</a>', ["https://s.example/c/?page=2"]),
        ('<a href="/c/page/2/">Older »</a>', ["https://s.example/c/page/2/"]),
        (
            '<nav><b>1</b> <a href="/c/?pg=2">2</a> <a href="/c/?pg=3">3</a></nav>',
            ["https://s.example/c/?pg=2", "https://s.example/c/?pg=3"],
        ),
        ('<a class="load-more" href="/c/?page=2">Show more</a>', ["https://s.example/c/?page=2"]),
        ('<a href="https://other.example/c/?page=2">Next</a>', []),
        ('<a href="/about/">About us</a>', []),
    ],
)
def test_pagination_discovery(html: str, want: list[str]) -> None:
    assert pagination_links(parse(f"<html><body>{html}</body></html>"), "https://s.example/c/") == want


def test_pagination_on_every_synthetic_scheme(truth: dict[str, Any], web_root: Path) -> None:
    for s in truth["sites"]:
        if s["mode"] not in {"static", "table"} or len(s["records"]) <= 25:
            continue
        for layout in ("v1", "v2"):
            host = s["start_url"].split("/")[2]
            from sift.synthetic.render import url_to_storage

            html = (web_root / layout / host / url_to_storage(s["start_url"])).read_text()
            assert pagination_links(parse(html), s["start_url"]), (s["id"], layout, s["layouts"][layout]["pagination"])


def test_needs_browser() -> None:
    shell = '<html><body><div id="app"><p>Loading…</p></div><script>fetch("/api")</script></body></html>'
    assert needs_browser(parse(shell), shell) == "empty application shell"
    button = (
        "<html><body><ul>"
        + "<li>item text here</li>" * 30
        + '</ul><button id="more">Load more</button><script>x()</script></body></html>'
    )
    assert needs_browser(parse(button), button) == "load-more button"
    scroll = (
        "<html><body>"
        + "<p>lots of server-rendered text</p>" * 40
        + "<script>new IntersectionObserver(f)</script></body></html>"
    )
    assert needs_browser(parse(scroll), scroll) == "infinite scroll"
    static = "<html><body>" + "<p>plain page</p>" * 40 + "</body></html>"
    assert needs_browser(parse(static), static) is None


def test_relevant_links_find_the_pdf_menu() -> None:
    doc = parse(
        '<a href="/menu.pdf">Download our menu (PDF)</a> <a href="/about/">Our story</a> <a href="https://x.example/menu">menu</a>'
    )
    words = request_words("Every dish on the menu with its section and price", ["dish", "section", "price"])
    assert relevant_links(doc, "https://r.example/", words) == ["https://r.example/menu.pdf"]
