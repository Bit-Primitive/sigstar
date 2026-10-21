"""Sources: what each public API returns, turned into rows for the raw tables.

Each source module has pure `parse_*` functions (tested on recorded real responses) and
`fetch_*` functions that page through the API with the shared polite client. A `Stream` is
one thing to keep up to date (one market on one venue at one interval, one news query, ...)
with its own cursor, so adding an asset later backfills just that asset.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sigstar.http import PoliteClient

Row = dict[str, Any]
# Given the client and the stream's saved cursor (None on the first run), return the new
# rows and the cursor to save. Cursors are ISO strings so they survive dlt's JSON state.
Fetch = Callable[[PoliteClient, str | None], tuple[list[Row], str | None]]


@dataclass(frozen=True)
class Stream:
    source: str  # the key under sources: in sigstar.yml
    table: str  # raw table it loads into (schema sigstar_raw)
    key: str  # unique name, also the cursor's key in dlt state
    fetch: Fetch
    # Skip the stream when it was read less than this many hours ago (0: always read).
    # For sources that allow very few requests, so recent re-reads do not use them up.
    refresh_hours: float = 0.0
