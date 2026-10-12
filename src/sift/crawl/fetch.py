"""Fetching pages: plain HTTP (httpx) for most pages, a headless browser (Playwright) for pages that need JavaScript.

The synthetic web (reserved `.example` domains) is served without any server: an httpx transport and a Playwright
route handler answer `https://<domain>.example/...` from the generated files (or from the static server named by
SIFT_SYNTHETIC_WEB_URL in Docker). The rest of the stack, robots.txt, the rate limiter, retries and the request log,
runs exactly as it does for a real site."""

from __future__ import annotations

import datetime as dt
import hashlib
import mimetypes
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from sift.crawl.polite import RateLimiter, RequestLog, RequestLogEntry, RobotsPolicy
from sift.crawl.urls import host_of, same_site
from sift.logging_config import get_logger
from sift.synthetic.render import url_to_storage

log = get_logger(__name__)

SYNTHETIC_TLD = ".example"


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str = "text/html"
    body: bytes = b""
    final_url: str = ""
    via: str = "http"
    """http | browser | cache"""
    fetched_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    elapsed_ms: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type or "xml" in self.content_type

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


class SyntheticSource(Protocol):
    def get(self, host: str, path: str, query: str) -> tuple[int, str, bytes]: ...


class DiskSource:
    """Serves <root>/<layout>/<host>/<storage path>."""

    def __init__(self, root: Path, layout: str = "v1") -> None:
        self.root = root
        self.layout = layout

    def get(self, host: str, path: str, query: str) -> tuple[int, str, bytes]:
        rel = url_to_storage(f"https://{host}{path}{'?' + query if query else ''}")
        base = (self.root / self.layout / host).resolve()
        target = (base / rel).resolve()
        if not str(target).startswith(str(base)) or not target.is_file():
            # "/catalog" -> "/catalog/" like a static server would.
            alt = (base / (rel + "/index.html")).resolve() if not query else None
            if alt is not None and str(alt).startswith(str(base)) and alt.is_file():
                target = alt
            else:
                return 404, "text/html", b"<h1>404 Not Found</h1>"
        clean = path.split("?")[0]
        ctype = mimetypes.guess_type(clean)[0] or ("text/plain" if clean.endswith(".txt") else "text/html")
        if clean.endswith(".json"):
            ctype = "application/json"
        return 200, ctype, target.read_bytes()


class HttpSource:
    """The synthetic web from a static server that routes by Host header (Docker: the synthetic-web service)."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    def get(self, host: str, path: str, query: str) -> tuple[int, str, bytes]:
        url = f"{self.base_url}{path}{'?' + query if query else ''}"
        try:
            response = self._client.get(url, headers={"Host": host})
        except httpx.HTTPError as exc:
            return 599, "text/plain", str(exc).encode()
        return response.status_code, response.headers.get("content-type", "text/html").split(";")[0], response.content


def is_synthetic(url: str) -> bool:
    return host_of(url).endswith(SYNTHETIC_TLD)


class SyntheticTransport(httpx.BaseTransport):
    """httpx transport: *.example from the synthetic source, anything else through the real network transport."""

    def __init__(self, source: SyntheticSource, fallback: httpx.BaseTransport | None = None) -> None:
        self.source = source
        self.fallback = fallback or httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host.endswith(SYNTHETIC_TLD):
            status, ctype, body = self.source.get(host, request.url.path, request.url.query.decode())
            return httpx.Response(status, headers={"content-type": ctype}, content=body, request=request)
        return self.fallback.handle_request(request)


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


class HttpFetcher:
    """Polite HTTP: robots.txt first, the per-host rate limit before every request, retries with backoff on 429/5xx
    and timeouts (honouring Retry-After), same-site redirects only, a size cap, and every request in the log."""

    def __init__(
        self,
        *,
        user_agent: str,
        limiter: RateLimiter,
        request_log: RequestLog,
        respect_robots: bool = True,
        timeout: float = 20.0,
        retries: int = 2,
        max_bytes: int = 3_000_000,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.user_agent = user_agent
        self.limiter = limiter
        self.log = request_log
        self.retries = retries
        self.max_bytes = max_bytes
        self._sleep = sleep
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5"},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )
        self._lock = threading.Lock()
        self.robots = RobotsPolicy(self._fetch_robots, user_agent, enabled=respect_robots)

    def _fetch_robots(self, url: str) -> tuple[int, str]:
        host = host_of(url)
        self.limiter.wait(host)
        result = self._get(url, kind="robots")
        return result.status, result.text if result.ok else ""

    def allowed(self, url: str) -> bool:
        allowed = self.robots.allowed(url)
        delay = self.robots.crawl_delay(url)
        if delay:
            self.limiter.set_interval(host_of(url), delay)
        return allowed

    def _get(self, url: str, *, kind: str = "page") -> FetchResult:
        started = time.monotonic()
        try:
            response = self._client.get(url)
        except httpx.TimeoutException as exc:
            self.log.add(RequestLogEntry(time.monotonic(), host_of(url), url, 598, kind))
            return FetchResult(url, 598, error=f"timeout: {exc}")
        except httpx.HTTPError as exc:
            self.log.add(RequestLogEntry(time.monotonic(), host_of(url), url, 599, kind))
            return FetchResult(url, 599, error=type(exc).__name__)
        self.log.add(RequestLogEntry(started, host_of(url), url, response.status_code, kind))
        ctype = response.headers.get("content-type", "text/html").split(";")[0].strip().lower() or "text/html"
        result = FetchResult(
            url,
            response.status_code,
            ctype,
            response.content[: self.max_bytes],
            final_url=str(response.url),
            elapsed_ms=(time.monotonic() - started) * 1000,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            result.error = response.headers.get("location", "")
        if response.status_code in {429, 503}:
            retry_after = response.headers.get("retry-after", "")
            result.error = f"retry-after:{retry_after}"
        return result

    def fetch(self, url: str) -> FetchResult:
        host = host_of(url)
        target = url
        for attempt in range(self.retries + 1):
            for _ in range(4):  # same-site redirects
                self.limiter.wait(host)
                result = self._get(target)
                if result.status in {301, 302, 303, 307, 308} and result.error:
                    nxt = str(httpx.URL(target).join(result.error))
                    if not same_site(url, nxt):
                        result.status, result.error = 310, f"off-site redirect to {host_of(nxt)} not followed"
                        return result
                    if not self.allowed(nxt):
                        return FetchResult(url, 403, error="redirect target disallowed by robots.txt")
                    target = nxt
                    continue
                break
            if result.status in {429, 503} or result.status >= 500:
                if attempt < self.retries:
                    delay = 2.0 * (2**attempt)
                    if result.error and result.error.startswith("retry-after:"):
                        try:
                            delay = max(delay, float(result.error.split(":", 1)[1]))
                        except ValueError:
                            pass
                    self.limiter.penalize(host, min(delay, 60.0))
                    log.info("fetch.retry", url=url, status=result.status, delay=delay)
                    continue
            result.url = url
            result.final_url = result.final_url or target
            return result
        return result

    def close(self) -> None:
        self._client.close()


class BrowserFetcher:
    """Playwright Chromium for JavaScript-rendered pages. Renders the page, then (with `expand=True`) clicks
    "load more" buttons and scrolls until no new content appears, up to `max_scrolls` rounds.

    Images, media and fonts are not downloaded (lighter on the site). The synthetic web is served by a route handler;
    real sites go through the network. One browser per fetcher, created lazily in the calling thread."""

    LOAD_MORE = re.compile(r"(load|show|see|view)\s+more|more\s+results|weitere|plus de", re.I)

    def __init__(
        self,
        *,
        user_agent: str,
        limiter: RateLimiter,
        request_log: RequestLog,
        source: SyntheticSource | None = None,
        executable: str = "",
        max_scrolls: int = 12,
        timeout_ms: int = 20_000,
    ) -> None:
        self.user_agent = user_agent
        self.limiter = limiter
        self.log = request_log
        self.source = source
        self.executable = executable
        self.max_scrolls = max_scrolls
        self.timeout_ms = timeout_ms
        self._pw: Any = None
        self._browser: Any = None
        self.actions: list[str] = []

    def _ensure(self) -> Any:
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            kwargs: dict[str, Any] = {"headless": True}
            if self.executable:
                kwargs["executable_path"] = self.executable
            self._browser = self._pw.chromium.launch(**kwargs)
        return self._browser

    def _route(self, route: Any) -> None:
        request = route.request
        if request.resource_type in {"image", "media", "font"}:
            route.abort()
            return
        parts = urlsplit(request.url)
        host = parts.hostname or ""
        if host.endswith(SYNTHETIC_TLD) and self.source is not None:
            status, ctype, body = self.source.get(host, parts.path or "/", parts.query)
            self.log.add(RequestLogEntry(time.monotonic(), host, request.url, status, "subresource"))
            route.fulfill(status=status, content_type=ctype, body=body)
            return
        if request.resource_type not in {"document"}:
            self.log.add(RequestLogEntry(time.monotonic(), host, request.url, 0, "subresource"))
        route.continue_()

    def fetch(self, url: str, *, expand: bool = False, item_selector: str | None = None) -> FetchResult:
        host = host_of(url)
        self.limiter.wait(host)
        started = time.monotonic()
        try:
            browser = self._ensure()
            context = browser.new_context(user_agent=self.user_agent, java_script_enabled=True)
            page = context.new_page()
            page.route("**/*", self._route)
            response = page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
            status = response.status if response is not None else 200
            self.log.add(RequestLogEntry(started, host, url, status, "browser"))
            if expand:
                self._expand(page, host, item_selector)
            html = page.content()
            context.close()
        except Exception as exc:  # Playwright raises its own error types; any failure is a failed fetch
            log.warning("browser.error", url=url, error=type(exc).__name__)
            return FetchResult(url, 599, via="browser", error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return FetchResult(
            url, status, "text/html", html.encode("utf-8"), final_url=url, via="browser", elapsed_ms=(time.monotonic() - started) * 1000
        )

    def _expand(self, page: Any, host: str, item_selector: str | None) -> None:
        def size() -> int:
            if item_selector:
                try:
                    return int(page.locator(item_selector).count())
                except Exception:
                    return 0
            return int(page.evaluate("document.body ? document.body.innerHTML.length : 0"))

        stable = 0
        last = size()
        for _ in range(self.max_scrolls):
            clicked = False
            button = page.locator("button, a[role=button], [data-load-more]").filter(has_text=self.LOAD_MORE)
            if button.count() > 0 and button.first.is_visible():
                self.limiter.wait(host)
                button.first.click()
                clicked = True
                self.actions.append("click")
            else:
                self.limiter.wait(host)
                page.mouse.wheel(0, 20_000)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                self.actions.append("scroll")
            try:
                page.wait_for_load_state("networkidle", timeout=5_000)
            except Exception:
                pass
            page.wait_for_timeout(250)
            now = size()
            if now <= last:
                stable += 1
                if stable >= (1 if clicked else 2):
                    break
            else:
                stable = 0
            last = now

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
                self._pw.stop()
            except Exception:
                pass
            self._browser = None


def browser_available(executable: str = "") -> bool:
    """True when Playwright can launch a Chromium (the system one named by `executable`, or its own)."""
    if executable:
        return Path(executable).exists()
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False
