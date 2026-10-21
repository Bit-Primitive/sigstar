"""Kraken public OHLC: GET /0/public/OHLC?pair=&interval=&since=.

Rows are [time, open, high, low, close, vwap, volume, count] with prices as strings and
time in Unix seconds (open time). Kraken keeps only the latest 720 candles per interval
(720 days daily, 30 days hourly) whatever `since` says, and the last row is the candle
still forming. The result is keyed by Kraken's internal pair name (XBTUSD comes back as
XXBTZUSD), so the key is taken from the response rather than assumed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sigstar.http import PoliteClient, SourceError
from sigstar.sources import Row
from sigstar.timeutil import from_seconds, is_closed

BASE_URL = "https://api.kraken.com/0/public/OHLC"
INTERVAL_MINUTES = {"1d": 1440, "1h": 60}
QUOTES = ("USD", "EUR", "USDT", "USDC", "GBP")


def _quote(market: str) -> str:
    return next((q for q in QUOTES if market.endswith(q)), "")


def parse_ohlc(
    payload: dict[str, Any], asset: str, market: str, interval: str, now: datetime
) -> list[Row]:
    if payload.get("error"):
        raise SourceError(
            f"Kraken refused {market}: {'; '.join(payload['error'])}",
            "check the kraken: market name for this asset in sigstar.yml",
        )
    result = payload.get("result", {})
    series = next((v for k, v in result.items() if k != "last"), [])
    rows: dict[datetime, Row] = {}
    for t, open_, high, low, close, vwap, volume, count in series:
        ts = from_seconds(t)
        if not is_closed(ts, interval, now):
            continue
        # Kraken sometimes repeats the newest row; keying by time keeps one.
        rows[ts] = {
            "venue": "kraken",
            "asset": asset,
            "market": market,
            "quote": _quote(market),
            "interval": interval,
            "ts": ts,
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
            "quote_volume": float(vwap) * float(volume),
            "vwap": float(vwap),
            "trades": int(count),
        }
    return [rows[k] for k in sorted(rows)]


def fetch_ohlc(
    client: PoliteClient, asset: str, market: str, interval: str, start: datetime, now: datetime
) -> list[Row]:
    payload = client.get_json(
        BASE_URL,
        {"pair": market, "interval": INTERVAL_MINUTES[interval], "since": int(start.timestamp())},
    )
    return [r for r in parse_ohlc(payload, asset, market, interval, now) if r["ts"] >= start]
