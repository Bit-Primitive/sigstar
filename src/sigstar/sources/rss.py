"""RSS 2.0 and Atom news feeds: headline, link, time and the feed's own short snippet.

Only what the publisher puts in its feed is read, and only the feed URL itself is fetched
(never the article pages). The snippet is the feed's description/summary with markup
removed, cut to SNIPPET_CHARS; full article text is never stored even if a feed embeds it.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from sigstar.http import PoliteClient, SourceError
from sigstar.sources import Row
from sigstar.timeutil import parse_iso

SNIPPET_CHARS = 280

# Feeds whose robots.txt and terms were checked and allow this use; see SOURCES.md for the
# feeds that were checked and left out (their terms forbid automated collection).
DEFAULT_FEEDS: tuple[dict[str, str], ...] = (
    {
        "name": "bitcoinmagazine",
        "url": "https://bitcoinmagazine.com/feed",
        "attribution": "Bitcoin Magazine",
    },
)

_ATOM = "{http://www.w3.org/2005/Atom}"
_TAGS = re.compile(r"<[^>]+>")


def clean_snippet(text: str | None) -> str | None:
    if not text:
        return None
    plain = " ".join(html.unescape(_TAGS.sub(" ", text)).split())
    if len(plain) <= SNIPPET_CHARS:
        return plain or None
    return plain[: SNIPPET_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)  # RSS: RFC 822 dates
    except (TypeError, ValueError):
        try:
            parsed = parse_iso(value)  # Atom: RFC 3339 dates
        except ValueError:
            return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(element: ET.Element | None) -> str | None:
    return element.text.strip() if element is not None and element.text else None


def parse_feed(xml_text: str, name: str) -> list[Row]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(f"the {name} feed is not valid XML ({exc})", "try again later") from exc
    rows: list[Row] = []
    for item in root.iter("item"):  # RSS 2.0
        link = _text(item.find("link"))
        rows.append(
            _row(
                name,
                link,
                _text(item.find("title")),
                _parse_time(_text(item.find("pubDate"))),
                _text(item.find("description")),
            )
        )
    for entry in root.iter(f"{_ATOM}entry"):  # Atom
        # Element truthiness means "has children", so compare with None explicitly.
        link_el = entry.find(f"{_ATOM}link[@rel='alternate']")
        if link_el is None:
            link_el = entry.find(f"{_ATOM}link")
        rows.append(
            _row(
                name,
                link_el.get("href") if link_el is not None else None,
                _text(entry.find(f"{_ATOM}title")),
                _parse_time(
                    _text(entry.find(f"{_ATOM}published")) or _text(entry.find(f"{_ATOM}updated"))
                ),
                _text(entry.find(f"{_ATOM}summary")),
            )
        )
    return [r for r in rows if r["url"] and r["title"] and r["published_at"]]


def _row(
    name: str, link: str | None, title: str | None, at: datetime | None, summary: str | None
) -> Row:
    return {
        "url": link,
        "title": " ".join(html.unescape(title).split()) if title else None,
        "published_at": at,
        "provider": "rss",
        "source_name": name,
        "domain": urlsplit(link).netloc.removeprefix("www.") if link else None,
        "language": None,
        "snippet": clean_snippet(summary),
        "query_key": None,
    }


def fetch(client: PoliteClient, name: str, url: str) -> list[Row]:
    # Read a feed at most every 15 minutes, however often sigstar runs.
    return parse_feed(client.get_text(url, cache_ttl=900), name)
