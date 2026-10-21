"""One polite HTTP client shared by every source.

Polite means: it says who it is (User-Agent with the project URL), it never asks a host
faster than that host's configured pace, it reads and obeys robots.txt, it backs off and
retries when a server says it is busy (honouring Retry-After), and it caches responses on
disk so repeated runs do not ask again for what they already have.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import requests

from sigstar import __version__
from sigstar.errors import SigstarError

PROJECT_URL = "https://bitprimitive.com/projects/sigstar"
USER_AGENT = f"sigstar/{__version__} (+{PROJECT_URL})"
ROBOTS_AGENT = "sigstar"

# Seconds between requests to one host. Each figure is at or below the host's published
# limit; see SOURCES.md for where each limit is documented.
HOST_PACE: dict[str, float] = {
    "api.exchange.coinbase.com": 0.35,
    "api.kraken.com": 1.5,
    "data-api.binance.vision": 0.25,
    "fapi.binance.com": 0.25,
    "www.okx.com": 0.25,
    "api.alternative.me": 1.0,
    # GDELT asks for at most one request every 5 seconds; it still answers 429 at that pace
    # at busy times, so sigstar waits twice as long.
    "api.gdeltproject.org": 10.0,
    "api.stlouisfed.org": 0.6,
}
DEFAULT_PACE = 2.0  # unknown hosts (RSS feeds): one request every 2 seconds
# First wait after a refused request, doubled on each retry. GDELT keeps refusing for a
# while once it has refused, so short waits only add to its load.
HOST_BACKOFF: dict[str, float] = {"api.gdeltproject.org": 30.0}

RETRY_STATUSES = {429, 500, 502, 503, 504}
_SECRET_PARAMS = re.compile(r"(api_key|apikey|key|token)=[^&\s]+", re.IGNORECASE)


class SourceError(SigstarError):
    """A source could not be read."""


def redact(text: str) -> str:
    """Remove credentials from URLs before they reach an error message or a log."""
    return _SECRET_PARAMS.sub(r"\1=***", text)


@dataclass
class PoliteClient:
    cache_dir: Path | None = None
    max_attempts: int = 5
    timeout: float = 30.0
    pace: dict[str, float] = field(default_factory=lambda: dict(HOST_PACE))
    session: requests.Session = field(default_factory=requests.Session)
    sleep: Any = time.sleep
    _last_request: dict[str, float] = field(default_factory=dict)
    _robots: dict[str, urllib.robotparser.RobotFileParser | None] = field(default_factory=dict)
    _crawl_delay: dict[str, float] = field(default_factory=dict)
    # Hosts that kept refusing (429/5xx) through every retry. Further requests to them in
    # this run fail at once instead of adding to an overloaded or rate-limiting server.
    _refusing: set[str] = field(default_factory=set)
    requests_made: int = 0

    def __post_init__(self) -> None:
        self.session.headers["User-Agent"] = USER_AGENT

    def get_json(self, url: str, params: dict[str, Any] | None = None, cache_ttl: float = 0) -> Any:
        text = self.get_text(url, params, cache_ttl)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            # Some APIs answer errors and maintenance pages as HTML or plain text.
            snippet = " ".join(text.split())[:160]
            raise SourceError(
                f"{_host(url)} did not return JSON: {snippet}",
                "the service may be down for maintenance or rate limiting; try again later",
            ) from exc

    def get_text(self, url: str, params: dict[str, Any] | None = None, cache_ttl: float = 0) -> str:
        full = f"{url}?{urlencode(params)}" if params else url
        cached = self._cache_read(full, cache_ttl)
        if cached is not None:
            return cached
        self._check_robots(url)
        response = self._request(full)
        self._cache_write(full, response.text, cache_ttl)
        return response.text

    # -- requests, pacing and retries ---------------------------------------------------

    def _wait_turn(self, host: str) -> None:
        pace = max(self.pace.get(host, DEFAULT_PACE), self._crawl_delay.get(host, 0.0))
        elapsed = time.monotonic() - self._last_request.get(host, float("-inf"))
        if elapsed < pace:
            self.sleep(pace - elapsed)
        self._last_request[host] = time.monotonic()

    def _request(self, full_url: str) -> requests.Response:
        host = _host(full_url)
        if host in self._refusing:
            raise SourceError(
                f"{host} kept refusing requests earlier in this run, so sigstar left it alone",
                "run the same command again later; what was collected is kept",
            )
        last_problem = ""
        for attempt in range(1, self.max_attempts + 1):
            self._wait_turn(host)
            try:
                response = self.session.get(full_url, timeout=self.timeout)
                self.requests_made += 1
            except requests.RequestException as exc:
                last_problem = f"could not reach {host}: {type(exc).__name__}"
            else:
                if response.status_code < 400:
                    return response
                if response.status_code == 451:
                    raise SourceError(
                        f"{host} refuses requests from your location (HTTP 451)",
                        "disable this source in sigstar.yml; another venue covers the same data",
                    )
                if response.status_code not in RETRY_STATUSES:
                    body = " ".join(response.text.split())[:160]
                    raise SourceError(
                        f"{host} answered HTTP {response.status_code}: {redact(body)}",
                        "check the market or series name in sigstar.yml",
                    )
                last_problem = f"{host} answered HTTP {response.status_code}"
                retry_after = _retry_after(response)
                if retry_after is not None and attempt < self.max_attempts:
                    self.sleep(retry_after)
                    continue
            if attempt < self.max_attempts:
                # Exponential backoff with jitter, starting from at least the host's pace.
                base = HOST_BACKOFF.get(host, max(self.pace.get(host, DEFAULT_PACE), 1.0))
                self.sleep(base * 2 ** (attempt - 1) + random.uniform(0, 1))
        self._refusing.add(host)
        raise SourceError(
            f"{last_problem} after {self.max_attempts} attempts",
            "the service is busy or down; wait a few minutes and run the same command again "
            "(what was already collected is kept)",
        )

    # -- robots.txt ---------------------------------------------------------------------

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        parser = self._robots[origin]
        if parser is not None and not parser.can_fetch(ROBOTS_AGENT, url):
            raise SourceError(
                f"{origin}/robots.txt does not allow automated access to {parts.path}",
                "sigstar respects robots.txt; disable this source or use another URL",
            )

    def _load_robots(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        """Fetch robots.txt with RFC 9309 semantics.

        urllib's own reader treats 401/403 as "disallow everything"; RFC 9309 says any 4xx
        means there are no rules. A 5xx or no answer means "assume everything is
        disallowed" until the server recovers.
        """
        robots_url = f"{origin}/robots.txt"
        cached = self._cache_read(robots_url, 86400)
        if cached is None:
            self._wait_turn(_host(origin))
            try:
                response = self.session.get(robots_url, timeout=self.timeout)
                self.requests_made += 1
            except requests.RequestException as exc:
                raise SourceError(
                    f"could not read {robots_url} ({type(exc).__name__})",
                    "check your internet connection and try again",
                ) from exc
            if 400 <= response.status_code < 500:
                return None
            if response.status_code >= 500:
                raise SourceError(
                    f"{robots_url} answered HTTP {response.status_code}",
                    "the site is having trouble; try again later",
                )
            cached = response.text
            self._cache_write(robots_url, cached, 86400)
        parser = urllib.robotparser.RobotFileParser(robots_url)
        parser.parse(cached.splitlines())
        delay = parser.crawl_delay(ROBOTS_AGENT)
        if delay:
            self._crawl_delay[_host(origin)] = float(delay)
        return parser

    # -- disk cache ---------------------------------------------------------------------

    def _cache_path(self, full_url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha256(full_url.encode()).hexdigest() + ".txt")

    def _cache_read(self, full_url: str, ttl: float) -> str | None:
        path = self._cache_path(full_url)
        if ttl <= 0 or path is None or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > ttl:
            return None
        return path.read_text(encoding="utf-8")

    def _cache_write(self, full_url: str, text: str, ttl: float) -> None:
        path = self._cache_path(full_url)
        if ttl <= 0 or path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _host(url: str) -> str:
    return urlsplit(url).netloc


def _retry_after(response: requests.Response) -> float | None:
    value = response.headers.get("Retry-After", "")
    try:
        return min(float(value), 300.0)
    except ValueError:
        return None
