"""Explain ranking, anomalies, forecast backtesting and relationships."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from sigstar.analysis import anomalies, explain, forecast, relationships


def test_explain_ranks_the_most_unusual_feature_first(features: pd.DataFrame) -> None:
    day = date(2026, 9, 15)
    mask = (features["asset"] == "ETH") & (features["day"] == day)
    features.loc[mask, "news_volume_z"] = 4.2
    features.loc[mask, "news_volume"] = 900.0

    calls = []

    def headlines(tag: str, d: date) -> list[dict]:
        calls.append((tag, d))
        return [{"title": "Big ETH news", "url": "https://ex.org/1", "source": "ex.org"}]

    result = explain.explain_day(features, "ETH", day, headlines=headlines)
    top = result.drivers[0]
    assert top.feature == "news_volume_z" and top.timing == "same day"
    assert top.unusual and top.z == 4.2
    assert top.evidence[0]["url"] == "https://ex.org/1"
    # Headlines are fetched as evidence for the unusual news driver, and once as context.
    assert calls == [("ETH", day), ("ETH", day)]
    assert result.headlines[0]["title"] == "Big ETH news"
    zs = [abs(d.z) for d in result.drivers]
    assert zs == sorted(zs, reverse=True)
    assert {c["asset"] for c in result.co_movements} == {"BTC", "SOL"}
    row = features[mask].iloc[0]
    assert result.move.return_pct == pytest.approx(math.exp(row["ret_1d"]) - 1)


def test_explain_unknown_day_has_hint(features: pd.DataFrame) -> None:
    with pytest.raises(Exception, match="no closed daily candle") as info:
        explain.explain_day(features, "BTC", date(2030, 1, 1))
    assert "choose a day" in info.value.hint  # type: ignore[attr-defined]


def test_biggest_moves_orders_by_size(features: pd.DataFrame) -> None:
    btc = features[features["asset"] == "BTC"]
    days = explain.biggest_moves(features, "BTC", btc["day"].min(), btc["day"].max(), 3)
    sizes = [abs(btc.loc[btc["day"] == d, "ret_1d_z"].iloc[0]) for d in days]
    assert sizes == sorted(sizes, reverse=True)
    assert sizes[0] == btc["ret_1d_z"].abs().max()


def test_robust_z_flags_an_outlier_and_ignores_the_day_itself() -> None:
    values = pd.Series([10.0, 11.0, 9.0, 10.5, 9.5] * 6 + [30.0])
    rz = anomalies.robust_z(values)
    assert rz.iloc[-1] > 10
    assert abs(rz.iloc[-2]) < 3


def test_anomalies_found_in_window(features: pd.DataFrame) -> None:
    day = date(2026, 9, 20)
    features.loc[(features["asset"] == "SOL") & (features["day"] == day), "funding_z"] = -5.0
    found = anomalies.find(features, 3.0, day, day)
    assert any(a.asset == "SOL" and a.feature == "funding_z" and a.z == -5.0 for a in found)


def test_backtest_mae_by_hand() -> None:
    y = np.arange(1.0, 41.0)  # 1..40, a straight line
    scores = forecast.backtest(y, h=2, origins=3, models={"naive": forecast.naive})
    # origins 36, 37, 38: naive misses by 1 then 2 each time -> MAE 1.5
    mae, mape, n = scores["naive"]
    assert n == 3 and mae == pytest.approx(1.5)
    expected_mape = np.mean([1 / 37, 2 / 38, 1 / 38, 2 / 39, 1 / 39, 2 / 40])
    assert mape == pytest.approx(expected_mape)


def test_naive_is_kept_on_a_random_walk() -> None:
    rng = np.random.default_rng(3)
    prices = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, 90))))
    days = pd.Series([date(2026, 7, 1) + timedelta(days=i) for i in range(90)])
    result = forecast.forecast_series("X", "price", prices, days, h=5, origins=15)
    assert result is not None
    naive = next(s for s in result.scores if s.model == "naive")
    assert naive.vs_naive == pytest.approx(1.0)
    if not result.beats_naive:
        assert result.best_model == "naive"
        assert result.point == pytest.approx([prices.iloc[-1]] * 5)
    assert result.lower and result.upper  # naive carries an empirical interval
    assert all(
        lo <= p <= hi for lo, p, hi in zip(result.lower, result.point, result.upper, strict=True)
    )


def test_a_model_beats_naive_on_a_mean_reverting_series() -> None:
    rng = np.random.default_rng(5)
    y = np.empty(120)
    y[0] = 0.5
    for t in range(1, 120):
        y[t] = 0.5 + 0.3 * (y[t - 1] - 0.5) + rng.normal(0, 0.05)
    days = pd.Series([date(2026, 6, 1) + timedelta(days=i) for i in range(120)])
    result = forecast.forecast_series("X", "volatility", pd.Series(y), days, h=7, origins=20)
    assert result is not None and result.beats_naive
    best = next(s for s in result.scores if s.model == result.best_model)
    assert best.vs_naive < 0.98


def test_too_little_history_gives_no_forecast() -> None:
    days = pd.Series([date(2026, 10, 1) + timedelta(days=i) for i in range(20)])
    assert forecast.forecast_series("X", "price", pd.Series(np.ones(20)), days, 7, 20) is None


def test_relationships_betas_and_lead_lag(features: pd.DataFrame) -> None:
    eth = features["asset"] == "ETH"
    btc = features[features["asset"] == "BTC"].set_index("day")["ret_1d"]
    # Make ETH = 1.5 x BTC + noise, and let ETH funding lead ETH returns by one day.
    features.loc[eth, "ret_1d"] = 1.5 * btc.reindex(
        features.loc[eth, "day"]
    ).to_numpy() + np.random.default_rng(1).normal(0, 0.002, eth.sum())
    features.loc[eth, "funding_rate"] = (
        features.loc[eth, "ret_1d"].shift(-1).fillna(0)
        + np.random.default_rng(2).normal(0, 0.003, eth.sum())
    ).to_numpy()
    result = relationships.analyse(features)
    beta = next(b for b in result.betas if b["asset"] == "ETH")
    assert beta["full"] == pytest.approx(1.5, abs=0.05)
    ll = next(
        x
        for x in result.lead_lag
        if x.asset == "ETH" and x.feature == "funding_rate" and x.target == "return"
    )
    assert ll.best_lag == 1 and ll.best_corr > 0.9
    g = next(
        x
        for x in result.granger
        if x.asset == "ETH" and x.feature == "funding_rate" and x.target == "return"
    )
    assert g.min_p < 0.001 and g.p_bonferroni >= g.min_p
    assert {r.asset for r in result.regimes} == {"BTC", "ETH", "SOL"}
