"""Binance public market data: spot klines, USDⓈ-M futures funding and open interest.

Spot klines come from data-api.binance.vision, Binance's documented market-data-only host:
api.binance.com's robots.txt disallows all automated agents, and sigstar obeys robots.txt.
Futures data comes from fapi.binance.com, whose robots.txt only names search engines.

- klines: [openTime ms, open, high, low, close, volume, closeTime, quoteVolume, trades, ...],
  at most 1000 per request.
- fundingRate: {"fundingTime" ms, "fundingRate", "markPrice"}, at most 1000 per request.
  fundingTime can carry a few stray milliseconds (…400001), kept as reported.
- openInterestHist: {"timestamp" ms, "sumOpenInterest" (base), "sumOpenInterestValue"
  (USDT)}; only the latest 30 days exist (older startTime is rejected), at most 500 rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sigstar.http import PoliteClient
from sigstar.sources import Row
from sigstar.timeutil import INTERVALS, from_millis, is_closed, to_millis

SPOT_URL = "https://data-api.binance.vision/api/v3/klines"
FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
OI_URL = "https://fapi.binance.com/futures/data/openInterestHist"
KLINE_LIMIT = 1000
FUNDING_LIMIT = 1000
OI_HISTORY_DAYS = 30


def parse_klines(
    payload: list[list[Any]], asset: str, market: str, interval: str, now: datetime
) -> list[Row]:
    rows = []
    for k in payload:
        ts = from_millis(k[0])
        if not is_closed(ts, interval, now):
            continue
        rows.append(
            {
                "venue": "binance",
                "asset": asset,
                "market": market,
                "quote": "USDT" if market.endswith("USDT") else market[-3:],
                "interval": interval,
                "ts": ts,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_volume": float(k[7]),
                "vwap": None,
                "trades": int(k[8]),
            }
        )
    return rows


def fetch_klines(
    client: PoliteClient, asset: str, market: str, interval: str, start: datetime, now: datetime
) -> list[Row]:
    rows: list[Row] = []
    cursor = start
    while cursor < now:
        payload = client.get_json(
            SPOT_URL,
            {
                "symbol": market,
                "interval": interval,
                "startTime": to_millis(cursor),
                "limit": KLINE_LIMIT,
            },
        )
        if not payload:
            break
        rows.extend(parse_klines(payload, asset, market, interval, now))
        cursor = from_millis(payload[-1][0]) + INTERVALS[interval]
        if len(payload) < KLINE_LIMIT:
            break
    return rows


def parse_funding(payload: list[dict[str, Any]], asset: str) -> list[Row]:
    return [
        {
            "venue": "binance",
            "asset": asset,
            "instrument": item["symbol"],
            "ts": from_millis(item["fundingTime"]),
            "funding_rate": float(item["fundingRate"]),
            "mark_price": float(item["markPrice"]) if item.get("markPrice") else None,
        }
        for item in payload
    ]


def fetch_funding(
    client: PoliteClient, asset: str, symbol: str, start: datetime, now: datetime
) -> list[Row]:
    rows: list[Row] = []
    cursor = start
    while cursor < now:
        payload = client.get_json(
            FUNDING_URL,
            {"symbol": symbol, "startTime": to_millis(cursor), "limit": FUNDING_LIMIT},
        )
        rows.extend(parse_funding(payload, asset))
        if len(payload) < FUNDING_LIMIT:
            break
        cursor = rows[-1]["ts"] + timedelta(milliseconds=1)
    return rows


def parse_open_interest(payload: list[dict[str, Any]], asset: str, period: str) -> list[Row]:
    return [
        {
            "venue": "binance",
            "asset": asset,
            "instrument": item["symbol"],
            "period": period,
            "ts": from_millis(item["timestamp"]),
            "oi_base": float(item["sumOpenInterest"]),
            "oi_usd": float(item["sumOpenInterestValue"]),
        }
        for item in payload
    ]


def fetch_open_interest(
    client: PoliteClient, asset: str, symbol: str, start: datetime, now: datetime
) -> list[Row]:
    # Older history does not exist on this endpoint; asking for it is an error.
    earliest = now - timedelta(days=OI_HISTORY_DAYS - 1)
    days = max(1, min(OI_HISTORY_DAYS, (now - max(start, earliest)).days + 1))
    payload = client.get_json(OI_URL, {"symbol": symbol, "period": "1d", "limit": days})
    return [r for r in parse_open_interest(payload, asset, "1d") if r["ts"] >= start]
