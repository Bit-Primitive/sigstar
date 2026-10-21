"""Crypto Fear & Greed Index from alternative.me: GET https://api.alternative.me/fng/.

`limit=N` returns the latest N daily values (0 = all history), newest first, each with a
Unix-seconds `timestamp` at 00:00 UTC, a string `value` 0-100 and a `value_classification`.
alternative.me asks that the source be credited next to the data (see SOURCES.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sigstar.http import PoliteClient, SourceError
from sigstar.sources import Row
from sigstar.timeutil import from_seconds

URL = "https://api.alternative.me/fng/"
ATTRIBUTION = "Crypto Fear & Greed Index by alternative.me (https://alternative.me/crypto/)"


def parse(payload: dict[str, Any]) -> list[Row]:
    error = (payload.get("metadata") or {}).get("error")
    if error:
        raise SourceError(f"alternative.me answered: {error}", "try again later")
    return [
        {
            "day": from_seconds(item["timestamp"]).date(),
            "value": int(item["value"]),
            "classification": item["value_classification"],
        }
        for item in payload.get("data") or []
    ]


def fetch(client: PoliteClient, start: datetime, now: datetime) -> list[Row]:
    days = (now - start).days + 2
    payload = client.get_json(URL, {"limit": days, "format": "json"})
    return [r for r in parse(payload) if r["day"] >= start.date()]
