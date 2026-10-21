"""GDELT DOC 2.0 API: news coverage volume, tone and headline lists.

GET https://api.gdeltproject.org/api/v2/doc/doc?query=&mode=&format=json
&startdatetime=YYYYMMDDHHMMSS&enddatetime=... covers the last three months only.

- timelinevolraw: {"timeline": [{"series": "Article Count", "data": [{"date",
  "value", "norm"}]}]}: matching articles per step and, in `norm`, all articles GDELT
  monitored in that step.
- timelinetone: same layout, series "Average Tone", value = mean tone of matching articles
  (roughly -10 very negative to +10 very positive; 0 also when there were no articles).
- artlist: {"articles": [{"url", "title", "seendate", "domain", "language",
  "sourcecountry", ...}]}, at most 250 per request. Only url, title, time, domain and
  language are kept: GDELT does not provide article text and sigstar does not fetch it.

Dates look like 20261020T213000Z. Step size depends on the window (15 minutes for 3 days,
daily for 90 days), so windows are always longer than a week, each point keeps its
`date_resolution`, and the daily marts use daily steps only. Days without coverage can be
missing from a timeline.
GDELT asks for at most one request every 5 seconds; the client paces this host at 6.
Queries are restricted to English-language sources so the headline keyword rules apply.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sigstar.http import PoliteClient, SourceError
from sigstar.sources import Row

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
ATTRIBUTION = "News coverage data from the GDELT Project (https://www.gdeltproject.org)"
MAX_HISTORY_DAYS = 89  # the DOC API's rolling three-month window, with a day's margin
# Windows longer than a week come back in daily steps (observed: 3 days -> 15-minute steps,
# 90 days -> daily steps); 9 days keeps a margin.
MIN_WINDOW_DAYS = 9
MAX_RECORDS = 250
_TIMELINE_METRICS = {"timelinevolraw": "article_count", "timelinetone": "tone"}


def _gdelt_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%d%H%M%S")


def parse_gdelt_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def full_query(query: str) -> str:
    return f"{query} sourcelang:english"


def _json(client: PoliteClient, params: dict[str, Any], cache_ttl: float) -> dict[str, Any]:
    payload = client.get_json(URL, {**params, "format": "json"}, cache_ttl=cache_ttl)
    if not isinstance(payload, dict):
        raise SourceError("GDELT returned an unexpected answer", "try again later")
    return payload


def parse_timeline(payload: dict[str, Any], mode: str, query_key: str, query: str) -> list[Row]:
    metric = _TIMELINE_METRICS[mode]
    resolution = (payload.get("query_details") or {}).get("date_resolution", "")
    rows: list[Row] = []
    for series in payload.get("timeline") or []:
        for point in series.get("data") or []:
            ts = parse_gdelt_date(point["date"])
            base = {"query_key": query_key, "query": query, "ts": ts, "resolution": resolution}
            rows.append({**base, "metric": metric, "value": float(point["value"])})
            if mode == "timelinevolraw" and "norm" in point:
                rows.append({**base, "metric": "all_articles", "value": float(point["norm"])})
    return rows


def fetch_timeline(
    client: PoliteClient, query_key: str, query: str, start: datetime, now: datetime
) -> list[Row]:
    start = max(start, now - timedelta(days=MAX_HISTORY_DAYS))
    start = min(start, now - timedelta(days=MIN_WINDOW_DAYS))
    rows: list[Row] = []
    for mode in _TIMELINE_METRICS:
        payload = _json(
            client,
            {
                "query": full_query(query),
                "mode": mode,
                "startdatetime": _gdelt_time(start),
                "enddatetime": _gdelt_time(now),
                "timelinesmooth": 0,
            },
            cache_ttl=900,
        )
        rows.extend(parse_timeline(payload, mode, query_key, query))
    return rows


def parse_articles(payload: dict[str, Any], query_key: str) -> list[Row]:
    rows = []
    for article in payload.get("articles") or []:
        title = " ".join((article.get("title") or "").split())
        if not article.get("url") or not title:
            continue
        rows.append(
            {
                "url": article["url"],
                "title": title,
                "published_at": parse_gdelt_date(article["seendate"]),
                "provider": "gdelt",
                "source_name": article.get("domain"),
                "domain": article.get("domain"),
                "language": article.get("language"),
                "snippet": None,
                "query_key": query_key,
            }
        )
    return rows


def fetch_articles(
    client: PoliteClient,
    query_key: str,
    query: str,
    start: datetime,
    end: datetime,
    max_records: int,
) -> list[Row]:
    """Headlines seen in [start, end), GDELT's relevance order, at most max_records (250)."""
    payload = _json(
        client,
        {
            "query": full_query(query),
            "mode": "artlist",
            "maxrecords": min(max_records, MAX_RECORDS),
            "sort": "hybridrel",
            "startdatetime": _gdelt_time(start),
            "enddatetime": _gdelt_time(end - timedelta(seconds=1)),
        },
        # Finished days' lists do not change; cache them for a day.
        cache_ttl=86400,
    )
    return parse_articles(payload, query_key)
