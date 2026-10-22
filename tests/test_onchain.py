"""On-chain prices and DEX activity from a real chainstar warehouse (CSV slices in
fixtures/chainstar), in commercial mode end to end and in personal mode next to exchange
candles. Expected values are recomputed from the fixture rows themselves."""

from __future__ import annotations

import math
import subprocess
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import duckdb
import pytest

from conftest import NOW, chainstar_warehouse, load
from sigstar import licences, workflow
from sigstar.analysis import anomalies, data, explain
from sigstar.build import build
from sigstar.config import load_config
from sigstar.disclaimer import DISCLAIMER
from sigstar.sources import binance, coinbase, feargreed, okx, rss
from sigstar.sources.tables import TABLES, dlt_columns

DEX_TOKENS = {
    "BTC": [
        ("ethereum", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"),
        ("ethereum", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"),
        ("base", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"),
    ],
    "ETH": [
        ("ethereum", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"),
        ("base", "0x4200000000000000000000000000000000000006"),
    ],
    "SOL": [("solana", "native")],
}
PRICE_CHAINS = {"BTC": ["bitcoin"], "ETH": ["ethereum", "base"], "SOL": ["solana"]}


def _load_raw(cfg, table_rows: dict[str, list[dict]]) -> None:  # type: ignore[no-untyped-def]
    import dlt

    from sigstar.collect import pipeline

    pipe = pipeline(cfg)
    for table, rows in table_rows.items():
        pipe.run(
            dlt.resource(
                rows,
                name=table,
                table_name=table,
                primary_key=TABLES[table]["primary_key"],
                write_disposition="merge",
                columns=dlt_columns(table),
            )
        )


def _exchange_rows() -> dict[str, list[dict]]:
    """Real exchange, derivatives, sentiment and RSS rows, as an earlier personal-mode run
    would have left them in the warehouse."""
    return {
        "candles": coinbase.parse_candles(
            load("coinbase_candles_btc_1d.json"), "BTC", "BTC-USD", "1d", NOW
        ),
        "funding_rates": binance.parse_funding(load("binance_funding_btc.json"), "BTC")
        + okx.parse_funding(load("okx_funding_eth.json"), "ETH"),
        "fear_greed": feargreed.parse(load("fear_greed.json")),
        "news_items": rss.parse_feed(load("rss_bitcoinmagazine.xml"), "bitcoinmagazine"),
    }


def q(path: Path, sql: str, params: list | None = None) -> list[tuple]:
    with duckdb.connect(str(path), read_only=True) as con:
        con.execute("set TimeZone = 'UTC'")
        return con.execute(sql, params or []).fetchall()


@pytest.fixture(scope="module")
def chainstar(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return chainstar_warehouse(tmp_path_factory.mktemp("cs") / "slices.duckdb")


@pytest.fixture(scope="module")
def commercial(tmp_path_factory: pytest.TempPathFactory, chainstar: Path) -> Path:
    """A commercial-mode project over the chainstar slices, with exchange rows already in the
    raw tables from an earlier personal-mode run. Built through the CLI (`run`)."""
    base = tmp_path_factory.mktemp("commercial")
    (base / "sigstar.yml").write_text(
        f"sources:\n  licence_mode: commercial\nchainstar:\n  warehouse: {chainstar}\n"
    )
    _load_raw(load_config(base / "sigstar.yml"), _exchange_rows())
    (base / "sigstar-rules.yml").write_text("lookback_days: 90\nrules: []\n")
    result = subprocess.run(
        [sys.executable, "-m", "sigstar", "run", "--skip-collect"],
        cwd=base,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (base / "run.out").write_text(result.stdout)
    return base


def test_prices_are_chainstars_trusted_rows_by_address(commercial: Path, chainstar: Path) -> None:
    wh = commercial / "warehouse.duckdb"
    rows = q(
        wh,
        "select asset, chain, token_address, price_id, usd_price, confidence, price_method "
        "from sigstar.fct_onchain_prices",
    )
    assert rows
    with duckdb.connect(str(chainstar), read_only=True) as con:
        source = {
            r[0]: r[1:]
            for r in con.execute(
                "select price_id, usd_price, confidence, price_method, chain, granularity, "
                "bucket_start from chainstar.fct_token_prices"
            ).fetchall()
        }
        ends = dict(
            con.execute(
                "select chain, max(block_timestamp) from chainstar.fct_fees group by chain"
            ).fetchall()
        )
        untrusted = con.execute(
            "select count(*) from chainstar.fct_token_prices where usd_price is null"
        ).fetchone()[0]
    assert untrusted > 0  # the slices hold low/none rows, and none of them comes through
    assert {r[0] for r in rows} == {"BTC", "ETH", "SOL"}
    for asset, chain, token, price_id, usd, confidence, method in rows:
        assert chain in PRICE_CHAINS[asset] and token == "native"
        chain_usd, chain_conf, chain_method, _, granularity, start = source[price_id]
        assert (usd, confidence, method) == (chain_usd, chain_conf, chain_method)
        assert confidence in ("high", "medium") and usd is not None
        step = timedelta(hours=1) if granularity == "hour" else timedelta(days=1)
        assert start + step <= ends[chain]  # closed buckets only
        if asset == "BTC":
            assert method == "cross_chain_wrapped_btc" and confidence == "medium"


def test_fct_prices_has_one_onchain_row_per_bucket_with_provenance(commercial: Path) -> None:
    wh = commercial / "warehouse.duckdb"
    venues = {v for (v,) in q(wh, "select distinct venue from sigstar.fct_prices")}
    assert venues == {"onchain"}  # the Coinbase rows collected earlier are dropped
    rows = q(
        wh,
        "select p.asset, p.interval, p.ts, p.close, p.price_chain, p.price_confidence, "
        "p.price_observations, p.log_return, "
        "(select max(case confidence when 'high' then 2 else 1 end) "
        " from sigstar.fct_onchain_prices o "
        " where o.asset = p.asset and o.interval = p.interval and o.ts = p.ts) "
        "from sigstar.fct_prices p order by p.asset, p.interval, p.ts",
    )
    previous: dict[tuple, tuple] = {}
    for asset, interval, ts, close, chain, confidence, obs, log_return, best in rows:
        assert chain and obs > 0
        assert (2 if confidence == "high" else 1) == best  # the best chain is picked
        step = timedelta(hours=1) if interval == "1h" else timedelta(days=1)
        prev = previous.get((asset, interval))
        if prev and prev[0] == ts - step:
            assert log_return == pytest.approx(math.log(close / prev[1]))
        else:
            assert log_return is None  # never across a gap between samples
        previous[(asset, interval)] = (ts, close)


def test_dex_volume_matches_the_swaps_by_address(commercial: Path, chainstar: Path) -> None:
    wh = commercial / "warehouse.duckdb"
    got = {
        (a, d, c): (n, v)
        for a, d, c, n, v in q(
            wh,
            "select asset, day, chain, swap_count, volume_usd from sigstar.fct_onchain_dex_daily",
        )
    }
    assert got
    expected = {}
    with duckdb.connect(str(chainstar), read_only=True) as con:
        for asset, tokens in DEX_TOKENS.items():
            values = ", ".join(f"('{c}', '{t}')" for c, t in tokens)
            for day, chain, n, volume in con.execute(
                f"""
                select date_day, chain, count(*), coalesce(sum(usd_value), 0)
                from chainstar.fct_swaps s
                where (chain = 'solana' or pool_verified)
                    and exists (select 1 from (values {values}) as w(chain, token)
                        where w.chain = s.chain
                        and w.token in (s.token_in_address, s.token_out_address))
                group by all
                """
            ).fetchall():
                expected[(asset, day, chain)] = (n, volume)
    assert set(got) == set(expected)
    for key, (n, volume) in expected.items():
        assert got[key][0] == n
        assert got[key][1] == pytest.approx(volume)


def test_commercial_mode_drops_restricted_rows_collected_earlier(commercial: Path) -> None:
    wh = commercial / "warehouse.duckdb"
    (raw_candles,) = q(wh, "select count(*) from sigstar_raw.candles")[0]
    assert raw_candles > 0  # still in the raw table ...
    assert q(wh, "select count(*) from sigstar.fct_derivatives")[0][0] == 0  # ... not in marts
    assert q(wh, "select count(*) from sigstar.fct_news where provider = 'rss'")[0][0] == 0
    assert q(wh, "select count(*) from sigstar.fct_sentiment")[0][0] > 0  # allowed: kept


def test_features_explain_and_anomalies_use_onchain_drivers(commercial: Path) -> None:
    cfg = load_config(commercial / "sigstar.yml")
    df = data.features(cfg)
    priced = df[df["close"].notna()]
    assert set(priced["ref_venue"]) == {"onchain"}
    assert priced["onchain_price_gap_bps"].isna().all()  # no exchange prices to compare
    assert priced["volume_quote"].isna().all()  # exchange volume: not in commercial mode
    # SOL has the longest real history in the fixture (two weeks of Solana samples).
    sol = df[(df["asset"] == "SOL") & df["onchain_dex_volume_z"].notna()]
    assert not sol.empty, "DEX volume z-scores need 10 earlier days in the fixture"
    day = workflow.latest_day(df, "SOL")
    e = workflow.explain_one(cfg, df, "SOL", day)
    assert e.move.venue == "onchain"
    assert e.move.price_source and e.move.price_source.startswith("chainstar on-chain DEX price")
    assert {d.feature for d in e.drivers} & {"onchain_dex_volume_z", "onchain_swap_count_z"}
    assert any("not an exchange closing price" in n for n in e.notes)
    # A planted outlier is found by anomalies, under its on-chain label.
    df.loc[(df["asset"] == "SOL") & (df["day"] == day), "onchain_dex_volume_z"] = 9.0
    found = anomalies.find(df, 3.0, day, day)
    assert any(a.feature == "onchain_dex_volume_z" and a.asset == "SOL" for a in found)


def test_commercial_report_states_mode_and_sources(commercial: Path) -> None:
    out = (commercial / "run.out").read_text()
    reports = sorted((commercial / "reports").glob("sigstar-brief-*.md"))
    assert reports, out
    md = reports[-1].read_text()
    html = reports[-1].with_suffix(".html").read_text()
    for text in (md, html, out):
        assert "Licence mode: commercial" in text
        assert "Sources used: fear_greed, chainstar" in text
    for text in (md, html):
        assert "chainstar" in text and "alternative.me" in text
        assert "Coinbase" not in text and "Binance" not in text and "Bitcoin Magazine" not in text
        assert "Commercial mode:" in text
        assert DISCLAIMER in text or DISCLAIMER.replace("'", "&#x27;") in text
    explained = subprocess.run(
        [sys.executable, "-m", "sigstar", "explain", "SOL"],
        cwd=commercial,
        capture_output=True,
        text=True,
    )
    assert explained.returncode == 0, explained.stderr
    assert "on onchain" in explained.stdout and "chainstar on-chain DEX price" in explained.stdout
    assert explained.stdout.strip().splitlines()[-1].startswith("Licence mode: commercial")


def test_personal_mode_gap_against_exchange_hours(tmp_path: Path, chainstar: Path) -> None:
    """Exchange hourly candles at the hours chainstar priced ETH, 10 and 20 bp above it in
    turn: the day's gap is the median of the hourly gaps against (high + low + close) / 3,
    and in personal mode the exchange stays the reference price."""
    (tmp_path / "sigstar.yml").write_text(
        "assets:\n"
        "  ETH:\n    coinbase: ETH-USD\n    chains: [ethereum, base]\n"
        "    onchain:\n      prices: [{chain: ethereum, token: native}, "
        "{chain: base, token: native}]\n"
        f"chainstar:\n  warehouse: {chainstar}\n"
    )
    cfg = load_config(tmp_path / "sigstar.yml")
    with duckdb.connect(str(chainstar), read_only=True) as con:
        first, last = con.execute(
            "select min(date_day), max(date_day) from chainstar.fct_token_prices"
        ).fetchone()
    daily = [
        _candle("1d", datetime.combine(first + timedelta(days=i), time(), UTC), 2400.0)
        for i in range((last - first).days + 1)
    ]
    _load_raw(cfg, {"candles": daily})
    build(cfg)
    priced = q(
        cfg.warehouse,
        "select ts, close from sigstar.fct_prices where venue = 'onchain' and asset = 'ETH' "
        "and interval = '1h' order by ts",
    )
    assert len(priced) >= 2
    factors = [1 - (0.001 if i % 2 == 0 else 0.002) for i in range(len(priced))]
    hourly = [
        _candle("1h", ts.replace(tzinfo=UTC), close / f)
        for (ts, close), f in zip(priced, factors, strict=True)
    ]
    _load_raw(cfg, {"candles": hourly})
    build(cfg)
    got = q(
        cfg.warehouse,
        "select day, ref_venue, onchain_price_gap_bps, onchain_price_gap_hours "
        "from sigstar.features_daily where asset = 'ETH' and onchain_price_gap_bps is not null",
    )
    assert got
    by_day: dict[date, list[float]] = {}
    for (ts, close), f in zip(priced, factors, strict=True):
        typical = close / f
        by_day.setdefault(ts.date(), []).append((close - typical) / typical * 1e4)
    for day, ref_venue, gap, n in got:
        assert ref_venue == "coinbase"  # exchanges stay the reference in personal mode
        assert n == len(by_day[day])
        assert gap == pytest.approx(_median(sorted(by_day[day])))
    assert licences.allows(cfg.licence_mode, "coinbase")


def _median(values: list[float]) -> float:
    n = len(values)
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


def _candle(interval: str, ts, close: float) -> dict:  # type: ignore[no-untyped-def]
    return {
        "venue": "coinbase",
        "asset": "ETH",
        "market": "ETH-USD",
        "quote": "USD",
        "interval": interval,
        "ts": ts,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
        "quote_volume": None,
        "vwap": None,
        "trades": None,
    }


def test_explain_onchain_price_source_text() -> None:
    import pandas as pd

    row = pd.Series(
        {
            "onchain_price_chain": "ethereum",
            "onchain_price_method": "swaps_vs_stablecoins",
            "onchain_price_confidence": "high",
            "onchain_price_observations": 97.0,
        }
    )
    assert explain.onchain_price_source(row) == (
        "chainstar on-chain DEX price: ethereum, swaps_vs_stablecoins, high confidence, 97 swaps"
    )
    assert explain.onchain_price_source(pd.Series({"onchain_price_chain": None})) is None
