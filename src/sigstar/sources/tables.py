"""Raw table layouts, given to dlt as explicit column hints.

Explicit types matter: dlt drops a column it has only ever seen as null, and dbt would then
fail on it. Units are part of each column's meaning and are written down here once.
"""

from __future__ import annotations

from typing import Any

TABLES: dict[str, dict[str, Any]] = {
    # One OHLCV candle, closed candles only. ts is the candle's open time (UTC).
    # volume is in the base asset (BTC for BTC-USD); quote_volume in the quote currency
    # when the venue reports it (Binance), else null; vwap when the venue reports it.
    "candles": {
        "primary_key": ["venue", "market", "interval", "ts"],
        "columns": {
            "venue": "text",
            "asset": "text",
            "market": "text",
            "quote": "text",
            "interval": "text",
            "ts": "timestamp",
            "open": "double",
            "high": "double",
            "low": "double",
            "close": "double",
            "volume": "double",
            "quote_volume": "double",
            "vwap": "double",
            "trades": "bigint",
        },
    },
    # One funding settlement. funding_rate is the fraction paid per funding period
    # (0.0001 = 0.01%), positive when longs pay shorts.
    "funding_rates": {
        "primary_key": ["venue", "instrument", "ts"],
        "columns": {
            "venue": "text",
            "asset": "text",
            "instrument": "text",
            "ts": "timestamp",
            "funding_rate": "double",
            "mark_price": "double",
        },
    },
    # Open interest snapshot at ts. oi_base in the asset's own units, oi_usd in USD (OKX)
    # or USDT (Binance reports sumOpenInterestValue in the contract's quote, USDT).
    "open_interest": {
        "primary_key": ["venue", "instrument", "period", "ts"],
        "columns": {
            "venue": "text",
            "asset": "text",
            "instrument": "text",
            "period": "text",
            "ts": "timestamp",
            "oi_base": "double",
            "oi_usd": "double",
        },
    },
    # Crypto Fear & Greed Index, 0 (extreme fear) to 100 (extreme greed), one per day.
    "fear_greed": {
        "primary_key": ["day"],
        "columns": {"day": "date", "value": "bigint", "classification": "text"},
    },
    # GDELT DOC 2.0 timeline point. metric: article_count (timelinevolraw: matching
    # articles), all_articles (the same response's total articles monitored in that
    # step), tone (timelinetone: average tone, roughly -10 to +10).
    "gdelt_timeline": {
        "primary_key": ["query_key", "metric", "ts"],
        "columns": {
            "query_key": "text",
            "query": "text",
            "metric": "text",
            "ts": "timestamp",
            "resolution": "text",
            "value": "double",
        },
    },
    # A news item: headline, link, source and time only. Never article text; `snippet` is
    # at most the short description the feed itself publishes, cut to 280 characters.
    "news_items": {
        "primary_key": ["url"],
        "columns": {
            "url": "text",
            "title": "text",
            "published_at": "timestamp",
            "provider": "text",
            "source_name": "text",
            "domain": "text",
            "language": "text",
            "snippet": "text",
            "query_key": "text",
        },
    },
    # One FRED observation, value in the series' own units (percent, index points).
    "fred_observations": {
        "primary_key": ["series_id", "day"],
        "columns": {"series_id": "text", "day": "date", "value": "double"},
    },
}


def dlt_columns(table: str) -> dict[str, dict[str, Any]]:
    return {
        name: {"data_type": data_type, "nullable": True}
        for name, data_type in TABLES[table]["columns"].items()
    }
