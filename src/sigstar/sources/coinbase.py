"""Coinbase Exchange public candles: GET /products/{id}/candles.

Response rows are [time, low, high, open, close, volume], newest first, time in Unix
seconds (the candle's open), volume in the base asset. At most 300 candles per request,
so long ranges are asked for in 300-candle windows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sigstar.http import PoliteClient
from sigstar.sources import Row
from sigstar.timeutil import INTERVALS, from_seconds, is_closed

BASE_URL = "https://api.exchange.coinbase.com"
GRANULARITY = {"1d": 86400, "1h": 3600}
MAX_CANDLES = 300


def parse_candles(
    payload: list[list[Any]], asset: str, market: str, interval: str, now: datetime
) -> list[Row]:
    rows = []
    for t, low, high, open_, close, volume in payload:
        ts = from_seconds(t)
        if not is_closed(ts, interval, now):
            continue
        rows.append(
            {
                "venue": "coinbase",
                "asset": asset,
                "market": market,
                "quote": market.split("-")[1],
                "interval": interval,
                "ts": ts,
                "open": float(open_),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
                "quote_volume": None,
                "vwap": None,
                "trades": None,
            }
        )
    return sorted(rows, key=lambda r: r["ts"])


def fetch_candles(
    client: PoliteClient, asset: str, market: str, interval: str, start: datetime, now: datetime
) -> list[Row]:
    step = INTERVALS[interval] * MAX_CANDLES
    rows: list[Row] = []
    window_start = start
    while window_start < now:
        # end is inclusive on Coinbase, so stop one second short of the next window.
        window_end = min(window_start + step - timedelta(seconds=1), now)
        payload = client.get_json(
            f"{BASE_URL}/products/{market}/candles",
            {
                "granularity": GRANULARITY[interval],
                "start": window_start.isoformat().replace("+00:00", "Z"),
                "end": window_end.isoformat().replace("+00:00", "Z"),
            },
        )
        rows.extend(parse_candles(payload, asset, market, interval, now))
        window_start += step
    return rows
