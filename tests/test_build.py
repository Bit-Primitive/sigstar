"""dbt build on fixtures: real recorded rows loaded through dlt, plus a synthetic series
for the window maths, then the marts and features checked against hand calculations."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from conftest import NOW, load
from sigstar.build import build, keyword_pattern
from sigstar.config import load_config
from sigstar.sources import binance, coinbase, feargreed, gdelt, kraken, okx, rss

CHAINSTAR = Path.home() / "Documents/chainstar-real-runs/demo-site-xs/site.duckdb"


def _load(cfg, table_rows: dict[str, list[dict]]) -> None:  # type: ignore[no-untyped-def]
    import dlt

    from sigstar.collect import pipeline
    from sigstar.sources.tables import TABLES, dlt_columns

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


def _synthetic_candles(venue: str, offset: float) -> list[dict]:
    """40 closed daily candles, close = 100 * 1.01^i (+ offset) so returns are exact, and a
    volume that cycles 10, 11, 12 then jumps by 50 on the last day."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    rows = []
    for i in range(40):
        close = 100 * 1.01**i + offset
        rows.append(
            {
                "venue": venue,
                "asset": "TST",
                "market": f"TST-{venue}",
                "quote": "USD",
                "interval": "1d",
                "ts": start + timedelta(days=i),
                "open": close,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": 10.0 + i % 3 + (50.0 if i == 39 else 0.0),
                "quote_volume": None,
                "vwap": None,
                "trades": None,
            }
        )
    return rows


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("wh")
    (base / "sigstar.yml").write_text(
        "assets:\n"
        "  BTC: {coinbase: BTC-USD, news_keywords: [bitcoin, btc], chains: [bitcoin]}\n"
        "  ETH: {kraken: ETHUSD, news_keywords: [ethereum], chains: [ethereum, base]}\n"
        "  SOL: {binance: SOLUSDT, news_keywords: [solana], chains: [solana]}\n"
        "  TST: {coinbase: TST-coinbase}\n"
        "topics:\n  etf: {news_keywords: [etf]}\n"
        + (f"chainstar: {{warehouse: {CHAINSTAR}}}\n" if CHAINSTAR.exists() else "")
    )
    cfg = load_config(base / "sigstar.yml")
    candles = (
        coinbase.parse_candles(load("coinbase_candles_btc_1d.json"), "BTC", "BTC-USD", "1d", NOW)
        + kraken.parse_ohlc(load("kraken_ohlc_eth_1d.json"), "ETH", "ETHUSD", "1d", NOW)
        + binance.parse_klines(load("binance_klines_sol_1h.json"), "SOL", "SOLUSDT", "1h", NOW)
        + _synthetic_candles("coinbase", 0.0)
        + _synthetic_candles("kraken", 0.5)
    )
    news = rss.parse_feed(load("rss_bitcoinmagazine.xml"), "bitcoinmagazine")
    if (Path(__file__).parent / "fixtures/gdelt_artlist.json").exists():
        news += gdelt.parse_articles(load("gdelt_artlist.json"), "BTC")
    _load(
        cfg,
        {
            "candles": candles,
            "funding_rates": binance.parse_funding(load("binance_funding_btc.json"), "BTC")
            + okx.parse_funding(load("okx_funding_eth.json"), "ETH"),
            "open_interest": binance.parse_open_interest(load("binance_oi_btc.json"), "BTC", "1d")
            + okx.parse_open_interest(load("okx_oi_btc.json"), "BTC", "BTC-USDT-SWAP"),
            "fear_greed": feargreed.parse(load("fear_greed.json")),
            "news_items": news,
        },
    )
    result = build(cfg)
    assert result.models >= 15 and result.tests_passed >= 30
    return cfg.warehouse


def q(path: Path, sql: str) -> list[tuple]:
    with duckdb.connect(str(path), read_only=True) as con:
        con.execute("set TimeZone = 'UTC'")
        return con.execute(sql).fetchall()


def test_prices_grain_and_returns(warehouse: Path) -> None:
    rows = q(
        warehouse,
        "select close, log_return from sigstar.fct_prices "
        "where asset = 'TST' and venue = 'coinbase' order by ts",
    )
    assert rows[0][1] is None
    assert rows[5][1] == pytest.approx(math.log(1.01))
    (n,) = q(warehouse, "select count(*) from sigstar.fct_prices where asset = 'BTC'")[0]
    assert n == 4  # five recorded rows minus the candle still forming


def test_features_zscore_excludes_the_day_itself(warehouse: Path) -> None:
    rows = q(
        warehouse,
        "select day, ret_1d_z, volume_quote, volume_z, spread_bps, ref_venue "
        "from sigstar.features_daily where asset = 'TST' and close is not null order by day",
    )
    assert all(r[5] == "coinbase" for r in rows)  # venue priority
    # Constant returns give a baseline without variation: no z-score, not a division by 0.
    assert rows[-1][1] is None
    logs = [math.log(r[2]) for r in rows]
    baseline = logs[-31:-1]  # the previous 30 days, today excluded
    mean = sum(baseline) / 30
    sd = math.sqrt(sum((x - mean) ** 2 for x in baseline) / 29)
    assert rows[-1][3] == pytest.approx((logs[-1] - mean) / sd)
    assert rows[5][3] is None  # fewer than 10 earlier days
    close_cb, close_kr = 100 * 1.01**39, 100 * 1.01**39 + 0.5
    assert rows[-1][4] == pytest.approx((close_kr - close_cb) / ((close_kr + close_cb) / 2) * 1e4)


def test_derivatives_daily_and_oi_change(warehouse: Path) -> None:
    rows = q(
        warehouse,
        "select venue, day, oi_base_open, oi_base_close, oi_change_pct "
        "from sigstar.fct_derivatives where asset = 'BTC' and oi_change_pct is not null "
        "order by venue, day",
    )
    assert rows
    for _, _, open_, close, change in rows:
        assert change == pytest.approx(close / open_ - 1)


def test_news_tagging_is_whole_word_and_traceable(warehouse: Path) -> None:
    rows = q(
        warehouse,
        "select n.title || ' ' || coalesce(n.snippet, ''), b.tag, "
        "b.matched_keyword from sigstar.fct_news n "
        "join sigstar.bridge_news_tags b using (article_id)",
    )
    assert rows
    for text, tag, keyword in rows:
        assert re.search(keyword_pattern(keyword), text.lower()), (tag, keyword, text)
    (bitcoins,) = q(warehouse, "select count(*) from sigstar.bridge_news_tags where tag='BTC'")[0]
    assert bitcoins > 0
    (bad,) = q(warehouse, "select count(*) from sigstar.fct_news where length(snippet) > 280")[0]
    assert bad == 0


def test_gdelt_titles_are_untokenised(warehouse: Path) -> None:
    # GDELT spaces out punctuation ("BTC ? Bitcoin Is Down Nearly 4 % ..."); the staging
    # model puts it back, and feed titles are kept exactly as published.
    titles = [
        t for (t,) in q(warehouse, "select title from sigstar.fct_news where provider = 'gdelt'")
    ]
    assert titles
    for t in titles:
        assert not re.search(r" [,.!?:;%]", t), t
        assert t == t.strip()
    assert any("Down Nearly 4% So Far" in t for t in titles)


def test_keyword_pattern_escapes_and_bounds() -> None:
    assert keyword_pattern("btc") == r"\bbtc\b"
    assert keyword_pattern("s&p 500") == r"\bs&p 500\b"
    assert keyword_pattern("a.b") == r"\ba\.b\b"


@pytest.mark.skipif(not CHAINSTAR.exists(), reason="real chainstar warehouse not present")
def test_onchain_stablecoins_by_address_not_symbol(warehouse: Path) -> None:
    (amount,) = q(
        warehouse,
        "select sum(value) from sigstar.fct_onchain_daily where chain = "
        "'ethereum' and metric = 'stablecoin_transfer_amount'",
    )[0]
    with duckdb.connect(str(CHAINSTAR), read_only=True) as con:
        (by_symbol,) = con.execute(
            "select sum(f.amount) from chainstar.fct_transfers f join chainstar.dim_tokens t "
            "using (token_id) where f.chain = 'ethereum' and t.symbol in ('USDC','USDT','DAI')"
        ).fetchone()
        (by_address,) = con.execute(
            "select sum(amount) from chainstar.fct_transfers where chain = 'ethereum' and "
            "lower(token_address) in ('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', "
            "'0xdac17f958d2ee523a2206206994597c13d831ec7', "
            "'0x6b175474e89094c44da98b954eedeac495271d0f')"
        ).fetchone()
    assert amount == pytest.approx(by_address)
    assert by_symbol > by_address  # tokens that merely call themselves USDC/USDT exist
