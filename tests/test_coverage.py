"""On-chain z-scores compare only days of comparable chain coverage.

A chainstar warehouse can hold some chains on some days only, or different amounts of a
chain on different days. A raw total then jumps because more was loaded, not because more
happened; sigstar must not rank that as a driver. These tests build a chainstar warehouse
with Solana sampled evenly for 14 days (synthetic rows, laid out as chainstar writes them)
and check the artifact, a genuine spike, and the coverage tolerance.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from sigstar.analysis import anomalies, data, explain
from sigstar.build import build
from sigstar.config import load_config
from sigstar.sources.tables import TABLES, dlt_columns

START = date(2026, 10, 1)
DAYS = 14
LAST = START + timedelta(days=DAYS - 1)
USDC = {
    "solana": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "ethereum": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
}


def _chainstar(path: Path, chain_days: list[tuple[str, date, int, float]]) -> Path:
    """(chain, day, blocks loaded, stablecoin amount): one block per 10 s from noon, a few
    fee-paying transactions per block, one USDC transfer per block."""
    fees, transfers = [], []
    for chain, day, blocks, stable in chain_days:
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
        for b in range(blocks):
            number = (day - START).days * 1000 + b
            ts = noon + timedelta(seconds=10 * b)
            for t in range(1 + (b + day.day) % 3):
                fees.append((chain, number, ts, day, f"payer{(b * 7 + t) % 11}", 0.0001))
            transfers.append((chain, number, ts, day, USDC[chain], "sender", stable / blocks))
    with duckdb.connect(str(path)) as con:
        con.execute("create schema chainstar")
        con.execute(
            "create table chainstar.fct_fees (chain varchar, block_number bigint, "
            "block_timestamp timestamp, date_day date, from_address varchar, fee_native double)"
        )
        con.execute(
            "create table chainstar.fct_transfers (chain varchar, block_number bigint, "
            "block_timestamp timestamp, date_day date, token_address varchar, "
            "from_address varchar, amount double)"
        )
        con.executemany(
            "insert into chainstar.fct_fees values (?, ?, ?, ?, ?, ?)",
            [(c, n, ts.replace(tzinfo=None), d, a, f) for c, n, ts, d, a, f in fees],
        )
        con.executemany(
            "insert into chainstar.fct_transfers values (?, ?, ?, ?, ?, ?, ?)",
            [(c, n, ts.replace(tzinfo=None), d, t, a, x) for c, n, ts, d, t, a, x in transfers],
        )
    return path


def _project(  # type: ignore[no-untyped-def]
    tmp_path: Path, chain_days: list[tuple[str, date, int, float]], days: int = DAYS
):
    import dlt

    from sigstar.collect import pipeline

    warehouse = _chainstar(tmp_path / "slices.duckdb", chain_days)
    (tmp_path / "sigstar.yml").write_text(
        "assets:\n  SOL: {coinbase: SOL-USD, chains: [solana]}\n"
        f"chainstar:\n  warehouse: {warehouse}\n"
    )
    cfg = load_config(tmp_path / "sigstar.yml")
    candles = []
    for i in range(days):
        close = 100 * (1.01 if i % 2 else 0.995) ** i
        candles.append(
            {
                "venue": "coinbase",
                "asset": "SOL",
                "market": "SOL-USD",
                "quote": "USD",
                "interval": "1d",
                "ts": datetime.combine(START + timedelta(days=i), datetime.min.time(), UTC),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 10.0,
                "quote_volume": None,
                "vwap": None,
                "trades": None,
            }
        )
    pipeline(cfg).run(
        dlt.resource(
            candles,
            name="candles",
            table_name="candles",
            primary_key=TABLES["candles"]["primary_key"],
            write_disposition="merge",
            columns=dlt_columns("candles"),
        )
    )
    build(cfg)
    return cfg


def _stable(i: int) -> float:
    return 1000.0 * (1 + 0.05 * math.sin(i))  # varies a little day to day


def _even_solana(blocks: int = 40, days: int = DAYS) -> list[tuple[str, date, int, float]]:
    return [("solana", START + timedelta(days=i), blocks, _stable(i)) for i in range(days)]


def _last(cfg):  # type: ignore[no-untyped-def]
    df = data.features(cfg)
    return df, df[df["day"] == LAST].iloc[0]


def test_a_chain_loaded_only_on_the_last_day_is_not_ranked(tmp_path: Path) -> None:
    """The artifact: Ethereum appears only on the last day, carrying 5m of USDC, so the
    market-wide stablecoin total jumps. Compared with plain trailing days it is a huge
    outlier; compared like with like it is not comparable at all."""
    cfg = _project(tmp_path, [*_even_solana(), ("ethereum", LAST, 10, 5_000_000.0)])
    df, row = _last(cfg)
    history = df[(df["day"] < LAST) & (df["day"] >= LAST - timedelta(days=30))]
    naive = (row["stablecoin_transfer_amount"] - history["stablecoin_transfer_amount"].mean()) / (
        history["stablecoin_transfer_amount"].std()
    )
    assert naive > 100  # what a plain trailing z-score would have ranked
    assert pd_isna(row["stablecoin_transfer_z"])
    note = row["stablecoin_transfer_z_note"]
    assert note == (
        "not comparable: ethereum loaded with similar coverage (blocks within 10%) "
        "on 0 of 13 earlier days (10 needed)"
    )
    # SOL's own chain is unaffected: its activity is still compared and scored.
    assert not pd_isna(row["onchain_tx_count_z"]) and pd_isna(row["onchain_tx_count_z_note"])

    e = explain.explain_day(df, "SOL", LAST)
    # The day before (Solana only, like its own past) is still compared; the last day is not.
    ranked = {(d.feature, d.timing) for d in e.drivers}
    assert ("stablecoin_transfer_z", "same day") not in ranked
    assert any(
        n.startswith("Stablecoin transfer volume (same day) not ranked, not comparable: ethereum")
        for n in e.notes
    )
    found = anomalies.find(df, 3.0, LAST, LAST)
    assert not any(a.feature == "stablecoin_transfer_z" for a in found)
    skipped = anomalies.not_comparable(df, LAST, LAST)
    assert [(n.label, n.assets) for n in skipped] == [("Stablecoin transfer volume", ["SOL"])]


def test_a_genuine_spike_under_even_coverage_still_ranks(tmp_path: Path) -> None:
    days = _even_solana()
    chain, day, blocks, _ = days[-1]
    days[-1] = (chain, day, blocks, 50_000.0)  # 50 times the usual, same coverage
    cfg = _project(tmp_path, days)
    df, row = _last(cfg)
    assert row["stablecoin_transfer_z"] > 10 and pd_isna(row["stablecoin_transfer_z_note"])
    e = explain.explain_day(df, "SOL", LAST)
    assert e.drivers[0].feature == "stablecoin_transfer_z" and e.drivers[0].unusual
    found = anomalies.find(df, 3.0, LAST, LAST)
    top = next(a for a in found if a.feature == "stablecoin_transfer_z")
    assert top.robust_z is None  # gated: the dbt z alone, which compared like with like
    assert anomalies.not_comparable(df, LAST, LAST) == []


def test_the_same_chain_loaded_differently_is_not_compared(tmp_path: Path) -> None:
    """Half as many Solana blocks on the last day: fewer transactions because less was
    loaded. Outside the tolerance, so not scored and the note says why."""
    days = _even_solana()
    chain, day, _, stable = days[-1]
    days[-1] = (chain, day, 20, stable)
    _, row = _last(_project(tmp_path, days))
    assert pd_isna(row["onchain_tx_count_z"])
    assert row["onchain_tx_count_z_note"].startswith(
        "not comparable: solana loaded with similar coverage (blocks within 10%) on 0 of 13"
    )


def test_coverage_within_the_tolerance_is_compared(tmp_path: Path) -> None:
    days = _even_solana()
    chain, day, _, stable = days[-1]
    days[-1] = (chain, day, 43, stable)  # 7.5% more blocks: within 10%
    df, row = _last(_project(tmp_path, days))
    assert not pd_isna(row["onchain_tx_count_z"]) and pd_isna(row["onchain_tx_count_z_note"])
    # The baseline is the 13 comparable days, as a plain trailing z-score would use here.
    history = df[df["day"] < LAST]["onchain_tx_count"]
    expected = (row["onchain_tx_count"] - history.mean()) / history.std()
    assert row["onchain_tx_count_z"] == pytest.approx(expected)


def pd_isna(value: object) -> bool:
    import pandas as pd

    return bool(pd.isna(value))


# Percentile thresholds need 20 days of history, so the alert scenarios run longer.
ALERT_DAYS = 26
ALERT_LAST = START + timedelta(days=ALERT_DAYS - 1)
P95_RULE = """lookback_days: 90
rules:
  - name: stablecoin_flood
    description: Stablecoin transfers above their 95th percentile
    assets: [SOL]
    all:
      - {feature: stablecoin_transfer_amount, above: p95}
"""


def _alerts(cfg):  # type: ignore[no-untyped-def]
    from sigstar import workflow

    (cfg.base_dir / "sigstar-rules.yml").write_text(P95_RULE)
    df = data.features(cfg)
    return df, workflow.evaluate_alerts(cfg, df, send=False)


def test_a_percentile_alert_does_not_fire_on_uneven_coverage(tmp_path: Path) -> None:
    """Ethereum appears only on the last day: against the plain history the total is far
    above its 95th percentile, so the rule would fire. Against comparable days there are
    none, so the condition is not evaluated, and the skip is logged, printed and briefed."""
    from sigstar import report, workflow

    days = _even_solana(days=ALERT_DAYS)
    cfg = _project(tmp_path, [*days, ("ethereum", ALERT_LAST, 10, 5_000_000.0)], ALERT_DAYS)
    df, (fired, new, problems, skips) = _alerts(cfg)
    sol = df[(df["asset"] == "SOL") & df["ret_1d"].notna()]
    value = sol[sol["day"] == ALERT_LAST]["stablecoin_transfer_amount"].iloc[0]
    plain = sol[sol["day"] < ALERT_LAST]["stablecoin_transfer_amount"].quantile(0.95)
    assert value > 100 * plain  # the false signal a plain percentile would fire on
    assert fired == [] and new == []
    (skip,) = skips
    assert (skip.rule, skip.asset, skip.day, skip.feature) == (
        "stablecoin_flood",
        "SOL",
        ALERT_LAST.isoformat(),
        "stablecoin_transfer_amount",
    )
    assert skip.reason.startswith("not comparable: ethereum loaded with similar coverage")
    assert "(20 needed)" in skip.reason
    assert any("not evaluated" in p and "ethereum" in p for p in problems)
    logged = (tmp_path / "alerts-skipped.jsonl").read_text().splitlines()
    assert len(logged) == 1 and "stablecoin_flood" in logged[0]
    _alerts(cfg)  # a second evaluation does not log the same skip twice
    assert len((tmp_path / "alerts-skipped.jsonl").read_text().splitlines()) == 1
    md, _ = report.write(workflow.build_brief(cfg), tmp_path / "reports")
    text = md.read_text()
    assert "Conditions not evaluated, because on-chain coverage was not comparable" in text
    assert "stablecoin_flood" in text


def test_a_genuine_percentile_breach_under_even_coverage_still_fires(tmp_path: Path) -> None:
    days = _even_solana(days=ALERT_DAYS)
    chain, day, blocks, _ = days[-1]
    days[-1] = (chain, day, blocks, 50_000.0)
    cfg = _project(tmp_path, days, ALERT_DAYS)
    df, (fired, _, _, skips) = _alerts(cfg)
    assert skips == []
    (alert,) = fired
    assert (alert.rule, alert.asset, alert.day) == ("stablecoin_flood", "SOL", day.isoformat())
    sol = df[(df["asset"] == "SOL") & df["ret_1d"].notna()]
    history = sol[sol["day"] < day]["stablecoin_transfer_amount"]
    threshold = float(alert.evidence[0]["condition"].split("= ")[1])
    assert threshold == pytest.approx(history.quantile(0.95), rel=1e-5)
