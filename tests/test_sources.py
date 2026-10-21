"""Parsers on recorded real responses: field order, units, closed-candle filtering."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from conftest import NOW, load
from sigstar.http import SourceError
from sigstar.sources import binance, coinbase, feargreed, fred, gdelt, kraken, okx, rss


def test_coinbase_maps_low_high_open_close_and_drops_forming_candle() -> None:
    payload = load("coinbase_candles_btc_1d.json")
    rows = coinbase.parse_candles(payload, "BTC", "BTC-USD", "1d", NOW)
    # The first row (2026-10-21 00:00) was still forming at capture time.
    assert len(rows) == len(payload) - 1
    assert all(r["ts"] < datetime(2026, 10, 21, tzinfo=UTC) for r in rows)
    newest = rows[-1]
    raw = next(p for p in payload if p[0] == int(newest["ts"].timestamp()))
    assert (newest["low"], newest["high"], newest["open"], newest["close"]) == tuple(raw[1:5])
    assert newest["low"] <= newest["open"] <= newest["high"]
    assert newest["quote"] == "USD" and newest["quote_volume"] is None
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)


def test_kraken_uses_response_pair_key_and_drops_forming_candle() -> None:
    payload = load("kraken_ohlc_eth_1d.json")
    assert "XETHZUSD" in payload["result"]  # asked for ETHUSD, keyed by Kraken's name
    rows = kraken.parse_ohlc(payload, "ETH", "ETHUSD", "1d", NOW)
    assert len(rows) == 4
    r = rows[-1]
    assert r["close"] == pytest.approx(2446.73)
    assert r["quote_volume"] == pytest.approx(r["vwap"] * r["volume"])
    assert r["trades"] == 52345


def test_kraken_error_is_a_source_error() -> None:
    with pytest.raises(SourceError, match="Unknown asset pair"):
        kraken.parse_ohlc(load("kraken_error.json"), "X", "NOTAPAIR", "1d", NOW)


def test_binance_klines_fields() -> None:
    payload = load("binance_klines_sol_1h.json")
    rows = binance.parse_klines(payload, "SOL", "SOLUSDT", "1h", NOW)
    assert len(rows) == len(payload) - 1  # the current hour was forming
    assert rows[0]["quote_volume"] == pytest.approx(float(payload[0][7]))
    assert rows[0]["trades"] == payload[0][8]
    assert rows[0]["quote"] == "USDT"


def test_binance_funding_and_open_interest_units() -> None:
    funding = binance.parse_funding(load("binance_funding_btc.json"), "BTC")
    assert all(-0.01 < r["funding_rate"] < 0.01 for r in funding)
    oi = binance.parse_open_interest(load("binance_oi_btc.json"), "BTC", "1d")
    implied_price = oi[-1]["oi_usd"] / oi[-1]["oi_base"]
    assert 10_000 < implied_price < 1_000_000  # USDT value / BTC contracts = a BTC price
    assert all(r["ts"].hour == 0 for r in oi)


def test_okx_stores_realized_rate_not_predicted() -> None:
    payload = load("okx_funding_eth.json")
    rows = okx.parse_funding(payload, "ETH")
    assert [r["funding_rate"] for r in rows] == [
        float(item["realizedRate"]) for item in payload["data"]
    ]


def test_okx_open_interest_array_order_and_utc_days() -> None:
    rows = okx.parse_open_interest(load("okx_oi_btc.json"), "BTC", "BTC-USDT-SWAP")
    assert all(r["ts"].hour == 0 for r in rows)  # period 1Dutc
    implied_price = rows[0]["oi_usd"] / rows[0]["oi_base"]
    assert 10_000 < implied_price < 1_000_000  # [ts, oi, oiCcy, oiUsd]


def test_okx_error_code() -> None:
    with pytest.raises(SourceError, match="doesn't exist"):
        okx.parse_funding(load("okx_error.json"), "X")


def test_fear_greed() -> None:
    rows = feargreed.parse(load("fear_greed.json"))
    assert rows[0]["day"] == date(2026, 10, 21)
    assert all(0 <= r["value"] <= 100 for r in rows)


def test_fred_docs_example_and_missing_values() -> None:
    rows = fred.parse(load("fred_observations_docs_example.json"), "GNPCA")
    assert rows[0] == {"series_id": "GNPCA", "day": date(1929, 1, 1), "value": 1065.9}
    holiday = {"observations": [{"date": "2026-10-12", "value": "."}]}
    assert fred.parse(holiday, "DGS10") == []
    with pytest.raises(SourceError):
        fred.parse({"error_message": "Bad Request"}, "NOPE")


def test_rss_keeps_headline_link_and_short_snippet_never_full_text() -> None:
    rows = rss.parse_feed(load("rss_bitcoinmagazine.xml"), "bitcoinmagazine")
    assert len(rows) == 2
    for r in rows:
        assert r["url"].startswith("https://bitcoinmagazine.com/")
        assert r["title"] and r["published_at"].tzinfo is not None
        assert r["snippet"] is None or len(r["snippet"]) <= rss.SNIPPET_CHARS
        assert "FULL ARTICLE TEXT" not in (r["snippet"] or "")
        assert "<" not in (r["snippet"] or "")


def test_rss_atom_entries() -> None:
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>A &amp; B</title><link rel="alternate" href="https://ex.org/a"/>
      <updated>2026-10-20T10:00:00Z</updated><summary>Short.</summary></entry></feed>"""
    (row,) = rss.parse_feed(atom, "ex")
    assert row["title"] == "A & B" and row["url"] == "https://ex.org/a"
    assert row["published_at"] == datetime(2026, 10, 20, 10, tzinfo=UTC)


def test_snippet_is_cut_at_a_word() -> None:
    text = "<p>" + "word " * 200 + "</p>"
    snippet = rss.clean_snippet(text)
    assert snippet is not None and len(snippet) <= rss.SNIPPET_CHARS
    assert snippet.endswith("…")


def test_gdelt_volume_timeline_keeps_counts_and_monitored_totals() -> None:
    payload = load("gdelt_timelinevolraw.json")
    rows = gdelt.parse_timeline(payload, "timelinevolraw", "BTC", "bitcoin")
    counts = [r for r in rows if r["metric"] == "article_count"]
    totals = [r for r in rows if r["metric"] == "all_articles"]
    assert len(counts) == len(totals) == 4
    assert all(r["resolution"] == "day" and r["ts"].hour == 0 for r in rows)
    assert all(c["value"] < t["value"] for c, t in zip(counts, totals, strict=True))


def test_gdelt_tone_timeline() -> None:
    rows = gdelt.parse_timeline(load("gdelt_timelinetone.json"), "timelinetone", "BTC", "b")
    assert rows and all(r["metric"] == "tone" and -100 <= r["value"] <= 100 for r in rows)


def test_gdelt_dates() -> None:
    assert gdelt.parse_gdelt_date("20261020T213000Z") == datetime(2026, 10, 20, 21, 30, tzinfo=UTC)


def test_gdelt_article_list_keeps_headline_metadata_only() -> None:
    rows = gdelt.parse_articles(load("gdelt_artlist.json"), "BTC")
    assert len(rows) == 3
    for r in rows:
        assert r["url"].startswith("http") and r["title"]
        assert r["provider"] == "gdelt" and r["snippet"] is None
        assert r["published_at"].tzinfo is not None
    assert gdelt.parse_articles({}, "BTC") == []  # GDELT answers {} when nothing matched
