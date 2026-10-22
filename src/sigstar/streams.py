"""Turn sigstar.yml into the list of streams to collect, each with its own cursor.

A cursor is the last thing safely stored (a candle's open time, a finished news day). The
next run asks only for what comes after it, plus a short overlap where a source revises
recent values (GDELT timelines, FRED); merge loads make the overlap harmless.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sigstar import licences
from sigstar.config import Config, fred_api_key
from sigstar.http import PoliteClient
from sigstar.sources import Row, Stream, binance, coinbase, feargreed, fred, gdelt, kraken, okx, rss
from sigstar.timeutil import INTERVALS, days_ago, parse_iso, start_of_day, utcnow

CandleFetch = Callable[[PoliteClient, str, str, str, datetime, datetime], list[Row]]
SeriesFetch = Callable[[PoliteClient, str, str, datetime, datetime], list[Row]]

MARKET_VENUES: dict[str, tuple[str, CandleFetch]] = {
    # config source name: (asset key holding the venue's market name, fetcher)
    "coinbase": ("coinbase", coinbase.fetch_candles),
    "kraken": ("kraken", kraken.fetch_ohlc),
    "binance": ("binance", binance.fetch_klines),
}
DERIVATIVES: dict[str, tuple[str, list[tuple[str, str, SeriesFetch]]]] = {
    "binance_futures": (
        "binance_futures",
        [
            ("funding_rates", "funding", binance.fetch_funding),
            ("open_interest", "oi", binance.fetch_open_interest),
        ],
    ),
    "okx": (
        "okx_swap",
        [
            ("funding_rates", "funding", okx.fetch_funding),
            ("open_interest", "oi", okx.fetch_open_interest),
        ],
    ),
}


def _next_start(cursor: str | None, default: datetime, step: timedelta) -> datetime:
    return parse_iso(cursor) + step if cursor else default


def _latest(rows: list[Row], field: str, cursor: str | None) -> str | None:
    values = [r[field] for r in rows if r.get(field) is not None]
    return max(values).isoformat() if values else cursor


def _candle_stream(
    source: str, fetch: CandleFetch, asset: str, market: str, interval: str, default: datetime
) -> Stream:
    def run(client: PoliteClient, cursor: str | None) -> tuple[list[Row], str | None]:
        now = utcnow()
        start = _next_start(cursor, default, INTERVALS[interval])
        rows = fetch(client, asset, market, interval, start, now)
        return rows, _latest(rows, "ts", cursor)

    return Stream(source, "candles", f"{source}:{market}:{interval}", run)


def _series_stream(
    source: str, table: str, kind: str, fetch: SeriesFetch, asset: str, inst: str, default: datetime
) -> Stream:
    def run(client: PoliteClient, cursor: str | None) -> tuple[list[Row], str | None]:
        now = utcnow()
        start = _next_start(cursor, default, timedelta(milliseconds=1))
        rows = fetch(client, asset, inst, start, now)
        return rows, _latest(rows, "ts", cursor)

    return Stream(source, table, f"{source}:{kind}:{inst}", run)


def _fear_greed_stream(default: datetime) -> Stream:
    def run(client: PoliteClient, cursor: str | None) -> tuple[list[Row], str | None]:
        # Re-read the last stored day too: today's value can be published late.
        start = parse_iso(cursor) if cursor else default
        rows = feargreed.fetch(client, start, utcnow())
        return rows, _latest(rows, "day", cursor)

    return Stream("fear_greed", "fear_greed", "fear_greed", run)


def _gdelt_timeline_stream(query_key: str, query: str, default: datetime) -> Stream:
    def run(client: PoliteClient, cursor: str | None) -> tuple[list[Row], str | None]:
        # GDELT keeps adding articles to recent days, so re-read the recent past. The window
        # is kept above a week because GDELT switches to hourly or 15-minute steps for
        # shorter windows, and the daily marts are built from daily steps only.
        start = parse_iso(cursor) - timedelta(days=gdelt.MIN_WINDOW_DAYS) if cursor else default
        rows = gdelt.fetch_timeline(client, query_key, query, start, utcnow())
        return rows, _latest(rows, "ts", cursor)

    return Stream("gdelt", "gdelt_timeline", f"gdelt:timeline:{query_key}", run, refresh_hours=6)


def _gdelt_articles_stream(
    query_key: str, query: str, first_days: int, chunk_days: int, per_request: int
) -> Stream:
    def run(client: PoliteClient, cursor: str | None) -> tuple[list[Row], str | None]:
        today = start_of_day(utcnow())
        day = parse_iso(cursor) + timedelta(days=1) if cursor else today - timedelta(first_days)
        day = max(day, today - timedelta(days=gdelt.MAX_HISTORY_DAYS))
        rows: list[Row] = []
        last_done = cursor
        # Only finished days, a few days per request: GDELT allows few requests, and a
        # finished day's list does not change, so each day is asked for once.
        while day < today:
            end = min(day + timedelta(days=chunk_days), today)
            rows.extend(gdelt.fetch_articles(client, query_key, query, day, end, per_request))
            last_done = (end - timedelta(days=1)).isoformat()
            day = end
        return rows, last_done

    return Stream("gdelt", "news_items", f"gdelt:articles:{query_key}", run)


def _rss_stream(name: str, url: str) -> Stream:
    def run(client: PoliteClient, cursor: str | None) -> tuple[list[Row], str | None]:
        # A feed is a window of recent items; merging on url keeps every item ever seen.
        return rss.fetch(client, name, url), None

    return Stream("rss", "news_items", f"rss:{name}", run)


def _fred_stream(series_id: str, api_key: str, default: datetime) -> Stream:
    def run(client: PoliteClient, cursor: str | None) -> tuple[list[Row], str | None]:
        # FRED revises recent observations; re-read the last 10 days.
        start = parse_iso(cursor) - timedelta(days=10) if cursor else default
        rows = fred.fetch(client, series_id, api_key, start, utcnow())
        return rows, _latest(rows, "day", cursor)

    return Stream("fred", "fred_observations", f"fred:{series_id}", run)


def plan_streams(cfg: Config, only: set[str] | None = None) -> tuple[list[Stream], list[str]]:
    """Every stream to collect, and notices about sources that were skipped."""
    notices: list[str] = []
    streams: list[Stream] = []
    daily_start = days_ago(cfg.daily_days)
    hourly_start = days_ago(cfg.hourly_days)

    def wanted(source: str) -> bool:
        return cfg.source_enabled(source) and (only is None or source in only)

    for source, terms in licences.TERMS.items():
        if cfg.licence_blocked(source) and (only is None or source in only):
            notices.append(f"{source}: off in commercial mode ({terms.note})")

    for source, (venue_key, fetch) in MARKET_VENUES.items():
        if not wanted(source):
            continue
        for asset in cfg.assets.values():
            market = asset.venues.get(venue_key)
            if market:
                streams.append(
                    _candle_stream(source, fetch, asset.symbol, market, "1d", daily_start)
                )
                streams.append(
                    _candle_stream(source, fetch, asset.symbol, market, "1h", hourly_start)
                )

    for source, (venue_key, kinds) in DERIVATIVES.items():
        if not wanted(source):
            continue
        for asset in cfg.assets.values():
            inst = asset.venues.get(venue_key)
            if inst:
                for table, kind, fetch in kinds:
                    streams.append(
                        _series_stream(source, table, kind, fetch, asset.symbol, inst, daily_start)
                    )

    if wanted("fear_greed"):
        streams.append(_fear_greed_stream(daily_start))

    if wanted("gdelt"):
        gdelt_cfg = cfg.sources.get("gdelt", {})
        queries = {a.symbol: a.gdelt_query for a in cfg.assets.values()}
        queries |= {t.key: t.gdelt_query for t in cfg.topics.values()}
        for key, query in queries.items():
            if not query:
                continue
            streams.append(_gdelt_timeline_stream(key, query, daily_start))
            streams.append(
                _gdelt_articles_stream(
                    key,
                    query,
                    int(gdelt_cfg.get("article_days", 14)),
                    int(gdelt_cfg.get("article_chunk_days", 7)),
                    int(gdelt_cfg.get("articles_per_request", 250)),
                )
            )

    if wanted("rss"):
        for feed in cfg.sources.get("rss", {}).get("feeds") or []:
            streams.append(_rss_stream(feed["name"], feed["url"]))

    if wanted("fred"):
        key = fred_api_key()
        configured = cfg.sources.get("fred", {}).get("series") or []
        left_out = [str(x) for x in configured if str(x) not in cfg.fred_series()]
        if left_out:
            notices.append(
                f"fred: {', '.join(left_out)} left out in commercial mode (only the "
                f"public-domain series {', '.join(licences.FRED_PUBLIC_DOMAIN)} are kept)"
            )
        if key is None:
            notices.append(
                "fred: skipped, SIGSTAR_FRED_API_KEY is not set (get a free key at "
                "https://fred.stlouisfed.org/docs/api/api_key.html)"
            )
        else:
            for series_id in cfg.fred_series():
                # A month's lead so the first day in the window has a previous value.
                streams.append(_fred_stream(series_id, key, days_ago(cfg.daily_days + 30)))

    return streams, notices
