"""Politeness: robots.txt (RFC 9309) per host, a per-host rate limit, and a request log to prove both afterwards.

* robots.txt is fetched once per host and cached. A missing file (4xx) allows everything; a server error or an
  unreachable host means "assume disallowed" (RFC 9309, section 2.3.1.4). `Crawl-delay` is honoured when it is longer
  than the configured interval.
* The rate limiter allows one request per host every `min_interval` seconds (Sift's default: 1 s), across threads.
* Every request (robots.txt included) is logged with its time, so the evaluation can compute the real request rate
  per host from the log instead of trusting the configuration."""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser


@dataclass
class RequestLogEntry:
    t: float
    host: str
    url: str
    status: int
    kind: str = "page"
    """page | robots | browser | subresource"""


@dataclass
class RequestLog:
    entries: list[RequestLogEntry] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, entry: RequestLogEntry) -> None:
        with self._lock:
            self.entries.append(entry)

    def per_host(self) -> dict[str, list[RequestLogEntry]]:
        hosts: dict[str, list[RequestLogEntry]] = {}
        for entry in sorted(self.entries, key=lambda e: e.t):
            hosts.setdefault(entry.host, []).append(entry)
        return hosts

    def min_gap(self, host: str) -> float | None:
        """The shortest interval between two consecutive requests to `host` (None with fewer than two)."""
        times = [e.t for e in self.per_host().get(host, [])]
        gaps = [b - a for a, b in itertools.pairwise(times)]
        return min(gaps) if gaps else None

    def mean_rate(self, host: str) -> float:
        """Requests per second to `host` from its first to its last request."""
        times = [e.t for e in self.per_host().get(host, [])]
        if len(times) < 2 or times[-1] <= times[0]:
            return 0.0
        return (len(times) - 1) / (times[-1] - times[0])

    def max_rate(self, host: str, window: float = 10.0) -> float:
        """Most requests to `host` in any half-open `window`-second window, divided by the window (requests/s)."""
        times = [e.t for e in self.per_host().get(host, [])]
        best, start = 0, 0
        for end, t in enumerate(times):
            while t - times[start] >= window:
                start += 1
            best = max(best, end - start + 1)
        return best / window if times else 0.0


class RateLimiter:
    """At most one request per `min_interval` seconds per host; per-host overrides (robots Crawl-delay)."""

    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._next: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._override: dict[str, float] = {}
        self._lock = threading.Lock()
        self.waited = 0.0

    def set_interval(self, host: str, seconds: float) -> None:
        """Changes a host's interval; a longer one also applies to the next request (a Crawl-delay read from robots.txt
        counts from the robots.txt request itself)."""
        with self._lock:
            self._override[host] = max(seconds, 0.0)
            if host in self._last:
                self._next[host] = max(self._next.get(host, 0.0), self._last[host] + seconds)

    def interval(self, host: str) -> float:
        return self._override.get(host, self.min_interval)

    def wait(self, host: str) -> None:
        interval = self.interval(host)
        if interval <= 0:
            with self._lock:
                self._last[host] = self._clock()
            return
        with self._lock:
            now = self._clock()
            start = max(now, self._next.get(host, 0.0))
            self._next[host] = start + interval
            self._last[host] = start
        if start > now:
            self.waited += start - now
            self._sleep(start - now)

    def penalize(self, host: str, seconds: float) -> None:
        """After a 429 or 503: push this host's next slot back (Retry-After)."""
        with self._lock:
            self._next[host] = max(self._next.get(host, 0.0), self._clock() + seconds)


RobotsFetch = Callable[[str], tuple[int, str]]


class RobotsPolicy:
    def __init__(self, fetch: RobotsFetch, user_agent: str, *, enabled: bool = True) -> None:
        self.fetch = fetch
        self.user_agent = user_agent
        self.enabled = enabled
        self._cache: dict[str, RobotFileParser | bool] = {}
        self._lock = threading.Lock()
        self.crawl_delays: dict[str, float] = {}

    def _robots_for(self, url: str) -> RobotFileParser | bool:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        robots_url = urlunsplit((parts.scheme or "https", parts.netloc, "/robots.txt", "", ""))
        status, text = self.fetch(robots_url)
        entry: RobotFileParser | bool
        if 200 <= status < 300:
            parser = RobotFileParser()
            parser.parse(text.splitlines())
            entry = parser
            delay = parser.crawl_delay(self.user_agent)
            if delay:
                self.crawl_delays[parts.hostname or ""] = float(delay)
        elif 400 <= status < 500:
            entry = True
        else:
            entry = False
        with self._lock:
            self._cache[key] = entry
        return entry

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        robots = self._robots_for(url)
        if isinstance(robots, bool):
            return robots
        return robots.can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float | None:
        if self.enabled:
            self._robots_for(url)
        return self.crawl_delays.get(urlsplit(url).hostname or "")
