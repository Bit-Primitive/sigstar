"""The polite client: robots.txt (RFC 9309), pacing, retries, cache, redaction."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sigstar.http import USER_AGENT, PoliteClient, SourceError, redact


@dataclass
class FakeResponse:
    status_code: int
    text: str = ""
    headers: dict = field(default_factory=dict)


class FakeSession:
    def __init__(self, routes: dict[str, list[FakeResponse]]) -> None:
        self.routes = routes
        self.calls: list[str] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: float) -> FakeResponse:
        self.calls.append(url)
        key = next(k for k in self.routes if url.startswith(k))
        queue = self.routes[key]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def client(routes: dict[str, list[FakeResponse]], **kw) -> tuple[PoliteClient, FakeSession, list]:  # type: ignore[no-untyped-def]
    session = FakeSession(routes)
    sleeps: list[float] = []
    c = PoliteClient(session=session, sleep=sleeps.append, **kw)  # type: ignore[arg-type]
    return c, session, sleeps


def test_user_agent_names_the_project() -> None:
    _, session, _ = client({"https://a.example": [FakeResponse(404)]})
    assert session.headers["User-Agent"] == USER_AGENT
    assert "bitprimitive.com/projects/sigstar" in USER_AGENT


def test_robots_disallow_is_obeyed() -> None:
    robots = FakeResponse(200, "User-agent: *\nDisallow: /")
    c, session, _ = client({"https://api.binance.com/robots.txt": [robots]})
    with pytest.raises(SourceError, match=r"robots\.txt does not allow"):
        c.get_text("https://api.binance.com/api/v3/klines")
    assert session.calls == ["https://api.binance.com/robots.txt"]


def test_robots_rules_for_other_agents_do_not_apply() -> None:
    robots = FakeResponse(200, "User-agent: Googlebot\nDisallow: /")
    c, _, _ = client(
        {
            "https://fapi.example/robots.txt": [robots],
            "https://fapi.example/x": [FakeResponse(200, "ok")],
        }
    )
    assert c.get_text("https://fapi.example/x") == "ok"


@pytest.mark.parametrize("status", [401, 403, 404])
def test_robots_4xx_means_no_rules(status: int) -> None:
    c, _, _ = client(
        {
            "https://h.example/robots.txt": [FakeResponse(status)],
            "https://h.example/api": [FakeResponse(200, "{}")],
        }
    )
    assert c.get_json("https://h.example/api") == {}


def test_robots_5xx_blocks() -> None:
    c, _, _ = client({"https://h.example/robots.txt": [FakeResponse(503)]})
    with pytest.raises(SourceError, match="503"):
        c.get_text("https://h.example/api")


def test_crawl_delay_slows_the_pace() -> None:
    robots = FakeResponse(200, "User-agent: *\nCrawl-delay: 5\n")
    c, _, sleeps = client(
        {
            "https://feed.example/robots.txt": [robots],
            "https://feed.example/feed": [FakeResponse(200, "<rss/>")],
        }
    )
    c.get_text("https://feed.example/feed")
    c.get_text("https://feed.example/feed")
    assert max(sleeps) > 4.5


def test_retry_after_is_honoured_then_succeeds() -> None:
    c, _, sleeps = client(
        {
            "https://h.example/robots.txt": [FakeResponse(404)],
            "https://h.example/api": [
                FakeResponse(429, "slow down", {"Retry-After": "7"}),
                FakeResponse(200, "[1]"),
            ],
        }
    )
    assert c.get_json("https://h.example/api") == [1]
    assert 7.0 in sleeps


def test_gives_up_with_a_hint_after_max_attempts() -> None:
    c, _, _ = client(
        {
            "https://h.example/robots.txt": [FakeResponse(404)],
            "https://h.example/api": [FakeResponse(503)],
        },
        max_attempts=3,
    )
    with pytest.raises(SourceError, match="after 3 attempts") as info:
        c.get_text("https://h.example/api")
    assert info.value.hint and "run the same command again" in info.value.hint


def test_451_says_location() -> None:
    c, _, _ = client(
        {
            "https://h.example/robots.txt": [FakeResponse(404)],
            "https://h.example/api": [FakeResponse(451)],
        }
    )
    with pytest.raises(SourceError, match="location"):
        c.get_text("https://h.example/api")


def test_non_json_maintenance_page_is_explained() -> None:
    c, _, _ = client(
        {
            "https://h.example/robots.txt": [FakeResponse(404)],
            "https://h.example/api": [FakeResponse(200, "<html>We'll be back soon!</html>")],
        }
    )
    with pytest.raises(SourceError, match="did not return JSON"):
        c.get_json("https://h.example/api")


def test_cache_serves_repeat_requests(tmp_path) -> None:  # type: ignore[no-untyped-def]
    c, session, _ = client(
        {
            "https://h.example/robots.txt": [FakeResponse(404)],
            "https://h.example/api": [FakeResponse(200, "first"), FakeResponse(200, "second")],
        },
        cache_dir=tmp_path,
    )
    assert c.get_text("https://h.example/api", {"a": 1}, cache_ttl=60) == "first"
    assert c.get_text("https://h.example/api", {"a": 1}, cache_ttl=60) == "first"
    assert session.calls.count("https://h.example/api?a=1") == 1


def test_redact_hides_keys() -> None:
    text = "GET https://api.stlouisfed.org/x?series_id=DFF&api_key=abc123&file_type=json"
    assert "abc123" not in redact(text)
    assert "series_id=DFF" in redact(text)


def test_a_host_that_keeps_refusing_is_left_alone_for_the_run() -> None:
    c, session, _ = client(
        {
            "https://h.example/robots.txt": [FakeResponse(404)],
            "https://h.example/api": [FakeResponse(429)],
        },
        max_attempts=2,
    )
    with pytest.raises(SourceError, match="after 2 attempts"):
        c.get_text("https://h.example/api")
    calls = len(session.calls)
    with pytest.raises(SourceError, match="kept refusing"):
        c.get_text("https://h.example/api", {"other": 1})
    assert len(session.calls) == calls
