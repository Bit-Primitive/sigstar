"""OKX public data: perpetual swap funding history and open interest history.

- /api/v5/public/funding-rate-history: newest first, `after` pages to older settlements,
  at most 400 per request, about three months of history. `realizedRate` is the rate
  actually charged ("Actual funding rate"); `fundingRate` is the predicted one, so
  sigstar stores realizedRate.
- /api/v5/rubik/stat/contracts/open-interest-history: rows [ts, oi (contracts),
  oiCcy (coin), oiUsd (USD)], newest first, at most 100 per request, `end` pages older.
  Period 1Dutc aligns days to UTC (plain 1D is aligned to UTC+8).
Both answer {"code": "0", "data": [...]} on success and a non-zero code on failure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sigstar.http import PoliteClient, SourceError
from sigstar.sources import Row
from sigstar.timeutil import from_millis, to_millis

FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate-history"
OI_URL = "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
FUNDING_LIMIT = 400
OI_LIMIT = 100
OI_PERIOD = "1Dutc"


def _data(payload: dict[str, Any], what: str) -> list[Any]:
    if str(payload.get("code")) != "0":
        raise SourceError(
            f"OKX refused the {what} request: {payload.get('msg') or payload.get('code')}",
            "check the okx_swap: instrument name in sigstar.yml (for example BTC-USDT-SWAP)",
        )
    return payload.get("data") or []


def parse_funding(payload: dict[str, Any], asset: str) -> list[Row]:
    return [
        {
            "venue": "okx",
            "asset": asset,
            "instrument": item["instId"],
            "ts": from_millis(item["fundingTime"]),
            "funding_rate": float(item["realizedRate"]),
            "mark_price": None,
        }
        for item in _data(payload, "funding history")
    ]


def fetch_funding(
    client: PoliteClient, asset: str, inst_id: str, start: datetime, now: datetime
) -> list[Row]:
    rows: list[Row] = []
    after: int | None = None
    while True:
        params: dict[str, Any] = {"instId": inst_id, "limit": FUNDING_LIMIT}
        if after is not None:
            params["after"] = after
        page = parse_funding(client.get_json(FUNDING_URL, params), asset)
        rows.extend(r for r in page if r["ts"] >= start)
        if len(page) < FUNDING_LIMIT or page[-1]["ts"] < start:
            break
        after = to_millis(page[-1]["ts"])
    return sorted(rows, key=lambda r: r["ts"])


def parse_open_interest(payload: dict[str, Any], asset: str, inst_id: str) -> list[Row]:
    return [
        {
            "venue": "okx",
            "asset": asset,
            "instrument": inst_id,
            "period": "1d",
            "ts": from_millis(ts),
            "oi_base": float(oi_ccy),
            "oi_usd": float(oi_usd),
        }
        for ts, _oi_contracts, oi_ccy, oi_usd in _data(payload, "open interest")
    ]


def fetch_open_interest(
    client: PoliteClient, asset: str, inst_id: str, start: datetime, now: datetime
) -> list[Row]:
    rows: list[Row] = []
    end: int | None = None
    while True:
        params: dict[str, Any] = {"instId": inst_id, "period": OI_PERIOD, "limit": OI_LIMIT}
        if end is not None:
            params["end"] = end
        page = parse_open_interest(client.get_json(OI_URL, params), asset, inst_id)
        rows.extend(r for r in page if r["ts"] >= start)
        if len(page) < OI_LIMIT or page[-1]["ts"] < start:
            break
        end = to_millis(page[-1]["ts"])
    return sorted(rows, key=lambda r: r["ts"])
