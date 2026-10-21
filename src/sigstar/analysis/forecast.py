"""Forecasts with statistical baselines, always shown next to their backtest error.

Targets per asset: price (daily close), volatility (7-day realised, annualised) and
volume (daily, all venues). Models: naive (tomorrow = today), ETS (exponential smoothing,
the specification chosen by AIC) and ARIMA (a small order grid chosen by AIC), all from
statsmodels. Price and volume are modelled on the log scale.

Every model is scored by a rolling-origin backtest: refit at each of the last N days, forecast
the next h days, compare with what happened. MAE and MAPE are averaged over all origins and
horizons; `vs_naive` is the model's MAE divided by naive's, so below 1 beats naive. A model
is preferred only if it is at least 2% better than naive; otherwise the naive forecast is
the one shown, and the report says so. For crypto prices that is the usual result.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

TARGETS: dict[str, tuple[str, bool]] = {
    # target: (features_daily column, model on log scale)
    "price": ("close", True),
    "volatility": ("rv_7d", False),
    "volume": ("volume_quote", True),
}
MIN_TRAIN = 30
# A model must cut naive's backtest MAE by at least this share to be preferred; smaller
# differences are noise over 20 origins, so naive is kept.
MIN_IMPROVEMENT = 0.02
INTERVAL = 0.8  # central 80% prediction interval

Forecaster = Callable[[np.ndarray, int], tuple[np.ndarray, np.ndarray | None, np.ndarray | None]]


def naive(y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Tomorrow equals today. The interval comes from the series' own past k-day changes
    (an empirical random-walk band), so naive gets an honest band too."""
    point = np.repeat(y[-1], h)
    tail = (1 - INTERVAL) / 2
    lower, upper = [], []
    for k in range(1, h + 1):
        changes = y[k:] - y[:-k]
        if len(changes) < 10:
            return point, None, None
        lower.append(y[-1] + np.quantile(changes, tail))
        upper.append(y[-1] + np.quantile(changes, 1 - tail))
    return point, np.array(lower), np.array(upper)


def ets(y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """Additive-error ETS; trend none or damped additive, whichever has the lower AIC."""
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    best = None
    for trend, damped in ((None, False), ("add", True)):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                # A pandas Series: ETS prediction intervals need an index to extend.
                fit = ETSModel(pd.Series(y), error="add", trend=trend, damped_trend=damped).fit(
                    disp=False
                )
            except (ValueError, np.linalg.LinAlgError):
                continue
        if np.isfinite(fit.aic) and (best is None or fit.aic < best.aic):
            best = fit
    if best is None:
        return naive(y, h)
    pred = best.get_prediction(start=len(y), end=len(y) + h - 1)
    frame = pred.summary_frame(alpha=1 - INTERVAL)
    return frame["mean"].to_numpy(), frame["pi_lower"].to_numpy(), frame["pi_upper"].to_numpy()


def arima(y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """ARIMA from a small grid, by AIC: random-walk style orders for trending series
    (d=1) and mean-reverting ones (d=0) are both tried."""
    from statsmodels.tsa.arima.model import ARIMA

    best = None
    for order, trend in (((0, 1, 1), "n"), ((1, 1, 0), "n"), ((1, 0, 0), "c"), ((1, 0, 1), "c")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                fit = ARIMA(y, order=order, trend=trend).fit()
            except (ValueError, np.linalg.LinAlgError):
                continue
        if np.isfinite(fit.aic) and (best is None or fit.aic < best.aic):
            best = fit
    if best is None:
        return naive(y, h)
    forecast = best.get_forecast(h)
    interval = forecast.conf_int(alpha=1 - INTERVAL)
    return forecast.predicted_mean, interval[:, 0], interval[:, 1]


MODELS: dict[str, Forecaster] = {"naive": naive, "ets": ets, "arima": arima}


@dataclass
class Score:
    model: str
    mae: float
    mape: float
    vs_naive: float
    origins: int


@dataclass
class Forecast:
    asset: str
    target: str
    column: str
    last_day: date
    last_value: float
    horizon: int
    best_model: str
    beats_naive: bool
    scores: list[Score]
    days: list[date]
    point: list[float]
    lower: list[float | None]
    upper: list[float | None]
    history: list[tuple[date, float]] = field(default_factory=list)


def backtest(
    y: np.ndarray, h: int, origins: int, models: dict[str, Forecaster] = MODELS
) -> dict[str, tuple[float, float, int]]:
    """Rolling-origin backtest on the original scale of y. Returns model -> (MAE, MAPE, n).

    Origins are the last `origins` positions that leave h actual values to compare with
    and at least MIN_TRAIN values to train on.
    """
    errors: dict[str, list[float]] = {m: [] for m in models}
    pct: dict[str, list[float]] = {m: [] for m in models}
    last_origin = len(y) - h
    first_origin = max(MIN_TRAIN, last_origin - origins + 1)
    used = 0
    for origin in range(first_origin, last_origin + 1):
        actual = y[origin : origin + h]
        used += 1
        for name, model in models.items():
            point, _, _ = model(y[:origin], h)
            diff = np.abs(point - actual)
            errors[name].extend(diff.tolist())
            pct[name].extend((diff / np.abs(actual)).tolist())
    return {
        m: (float(np.mean(errors[m])), float(np.mean(pct[m])), used)
        if errors[m]
        else (math.nan, math.nan, 0)
        for m in models
    }


def _to_scale(values: np.ndarray | None, log: bool) -> list[float | None]:
    if values is None:
        return []
    return [float(math.exp(v)) if log else float(v) for v in values]


def forecast_series(
    asset: str, target: str, series: pd.Series, days: pd.Series, h: int, origins: int
) -> Forecast | None:
    column, log = TARGETS[target]
    mask = (series.notna() & (series > 0)) if log else series.notna()
    y_raw = series[mask].to_numpy(dtype=float)
    day_list = list(days[mask])
    if len(y_raw) < MIN_TRAIN + h + 1:
        return None
    y = np.log(y_raw) if log else y_raw

    def on_scale(model: Forecaster) -> Forecaster:
        # Backtest errors are measured in the target's own units, not log units.
        def run(train: np.ndarray, steps: int) -> tuple[np.ndarray, None, None]:
            point, _, _ = model(np.log(train) if log else train, steps)
            return (np.exp(point) if log else point), None, None

        return run

    raw = backtest(y_raw, h, origins, {m: on_scale(f) for m, f in MODELS.items()})
    naive_mae = raw["naive"][0]
    scores = [
        Score(m, mae, mape, mae / naive_mae if naive_mae else math.nan, n)
        for m, (mae, mape, n) in raw.items()
    ]
    best = min(scores, key=lambda s: s.mae if not math.isnan(s.mae) else math.inf)
    if best.vs_naive > 1 - MIN_IMPROVEMENT:
        best = next(s for s in scores if s.model == "naive")
    point, lower, upper = MODELS[best.model](y, h)
    last_day = day_list[-1]
    return Forecast(
        asset=asset,
        target=target,
        column=column,
        last_day=last_day,
        last_value=float(y_raw[-1]),
        horizon=h,
        best_model=best.model,
        beats_naive=best.model != "naive",
        scores=scores,
        days=[last_day + timedelta(days=i + 1) for i in range(h)],
        point=_to_scale(point, log),
        lower=_to_scale(lower, log),
        upper=_to_scale(upper, log),
        history=list(zip(day_list[-60:], y_raw[-60:].tolist(), strict=True)),
    )


def forecast_asset(
    df: pd.DataFrame, asset: str, targets: list[str], h: int, origins: int
) -> list[Forecast]:
    rows = df[df["asset"] == asset].sort_values("day")
    out = []
    for target in targets:
        column, _ = TARGETS[target]
        result = forecast_series(asset, target, rows[column], rows["day"], h, origins)
        if result is not None:
            out.append(result)
    return out
