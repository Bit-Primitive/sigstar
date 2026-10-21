"""FRED (Federal Reserve Bank of St. Louis) series observations.

GET https://api.stlouisfed.org/fred/series/observations?series_id=&api_key=&file_type=json
&observation_start=YYYY-MM-DD returns {"observations": [{"date", "value", ...}]}, values
as strings. Days without a value (market holidays) carry a non-number such as ".", which
is skipped rather than stored as zero. Needs a free API key in SIGSTAR_FRED_API_KEY; the
key is passed as a query parameter and redacted from every message.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sigstar.http import PoliteClient, SourceError
from sigstar.sources import Row

URL = "https://api.stlouisfed.org/fred/series/observations"
ATTRIBUTION = "Source: FRED, Federal Reserve Bank of St. Louis (https://fred.stlouisfed.org)"

# What the default series measure, for reports. Units are FRED's own.
SERIES_LABELS: dict[str, str] = {
    "DFF": "Effective federal funds rate (percent, daily)",
    "DGS10": "10-year Treasury constant maturity yield (percent, daily)",
    "DTWEXBGS": "Nominal broad US dollar index (index Jan 2006=100, daily)",
}


def parse(payload: dict[str, Any], series_id: str) -> list[Row]:
    if "observations" not in payload:
        message = payload.get("error_message") or "no observations in the response"
        raise SourceError(
            f"FRED refused {series_id}: {message}",
            "check the series id at https://fred.stlouisfed.org and that "
            "SIGSTAR_FRED_API_KEY is a valid key",
        )
    rows = []
    for obs in payload["observations"]:
        try:
            value = float(obs["value"])
        except (TypeError, ValueError):
            continue
        rows.append(
            {"series_id": series_id, "day": date.fromisoformat(obs["date"]), "value": value}
        )
    return rows


def fetch(
    client: PoliteClient, series_id: str, api_key: str, start: datetime, now: datetime
) -> list[Row]:
    payload = client.get_json(
        URL,
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start.date().isoformat(),
        },
    )
    return parse(payload, series_id)
