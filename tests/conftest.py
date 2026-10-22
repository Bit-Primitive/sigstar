"""Shared test helpers.

Fixtures in tests/fixtures are small slices of real responses captured on 2026-10-21 with
sigstar's own polite client (only the first few rows kept). gdelt_timelinetone.json came
from a curl probe with sigstar's User-Agent and gdelt_artlist.json from the real run's
HTTP cache, because GDELT refused most other requests that day. Two exceptions:
fred_observations_docs_example.json is the example from FRED's API documentation (no FRED
key was available), and in rss_bitcoinmagazine.xml the publisher's full article text
(content:encoded) was replaced with a placeholder, since sigstar never keeps it.

fixtures/chainstar/ holds CSV slices of a real chainstar warehouse, built on 2026-10-22 by
running chainstar itself (`chainstar quickstart` on Ethereum, Base, Solana and Bitcoin, then
one small sample per UTC day from 2026-10-08 with chainstar's own extractors: 40 Solana slots
and one Bitcoin block near 12:00 UTC each day, and 10 Ethereum blocks on 2026-10-21; older
Ethereum and Base blocks were not served by the free public endpoints). Only the columns
sigstar reads are kept: fct_token_prices is every row for the chains' native assets
(priced or not), fct_swaps the swaps touching sigstar's default DEX tokens in the first
block (slot) of each chain's sample per day, fct_fees the first fee row of every loaded
block (so block counts per day are real) and the last per chain and day, fct_transfers
the first three native or stablecoin transfers per chain and day.
`chainstar_warehouse()` rebuilds a DuckDB file with schema chainstar from them.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
CHAINSTAR_FIXTURES = FIXTURES / "chainstar"
# The capture time: candles opening after NOW minus their interval were still forming.
NOW = datetime(2026, 10, 21, 17, 0, tzinfo=UTC)


def load(name: str):  # type: ignore[no-untyped-def]
    path = FIXTURES / name
    return path.read_text() if path.suffix == ".xml" else json.loads(path.read_text())


def chainstar_warehouse(path: Path) -> Path:
    """A chainstar DuckDB file (schema chainstar) with the real rows in fixtures/chainstar,
    typed as chainstar wrote them. Only the columns sigstar reads are kept."""
    schema = json.loads((CHAINSTAR_FIXTURES / "schema.json").read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as con:
        con.execute("create schema chainstar")
        for table, columns in schema.items():
            cols = ", ".join(f'"{name}" {kind}' for name, kind in columns.items())
            con.execute(f"create table chainstar.{table} ({cols})")
            csv = str(CHAINSTAR_FIXTURES / f"{table}.csv").replace("'", "''")
            con.execute(f"copy chainstar.{table} from '{csv}' (header true)")
    return path


def synthetic_features(days: int = 90, seed: int = 7) -> pd.DataFrame:
    """A features_daily-shaped frame for analysis tests (synthetic, not market data)."""
    rng = np.random.default_rng(seed)
    start = date(2026, 7, 1)
    frames = []
    for asset, vol in (("BTC", 0.02), ("ETH", 0.03), ("SOL", 0.04)):
        ret = rng.normal(0, vol, days)
        close = 100 * np.exp(np.cumsum(ret))
        frames.append(
            pd.DataFrame(
                {
                    "asset": asset,
                    "day": [start + timedelta(days=i) for i in range(days)],
                    "ref_venue": "coinbase",
                    "close": close,
                    "ret_1d": ret,
                    "ret_1d_z": ret / vol,
                    "range_pct": np.abs(ret) + 0.01,
                    "range_z": rng.normal(0, 1, days),
                    "volume_quote": np.exp(rng.normal(20, 0.3, days)),
                    "volume_z": rng.normal(0, 1, days),
                    "funding_rate": rng.normal(0.0001, 0.00005, days),
                    "funding_z": rng.normal(0, 1, days),
                    "news_volume": rng.integers(50, 150, days).astype(float),
                    "news_volume_z": rng.normal(0, 1, days),
                    "news_tone": rng.normal(-1, 0.5, days),
                    "news_tone_z": rng.normal(0, 1, days),
                    "fear_greed_change": rng.normal(0, 3, days),
                    "fear_greed_change_z": rng.normal(0, 1, days),
                    "oi_change_pct": rng.normal(0, 0.02, days),
                    "oi_change_z": rng.normal(0, 1, days),
                    "rv_7d": np.abs(rng.normal(0.5, 0.05, days)),
                    "ret_7d": rng.normal(0, 0.05, days),
                    "rv_30d": np.abs(rng.normal(0.5, 0.05, days)),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def features() -> pd.DataFrame:
    return synthetic_features()
