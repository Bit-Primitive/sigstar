"""Relationships: correlations, cross-asset betas, lead/lag, Granger tests and vol regimes.

All of these describe the collected window only. With 90 days of daily data a correlation
needs to be above roughly 0.2 in size to be distinguishable from zero, and Granger tests
(does past X help predict Y beyond Y's own past?) are run for many pairs, so p-values are
shown with a Bonferroni correction. Predictive in-sample is not causal and not a promise.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MIN_OBS = 30
# Features tested for leading returns: each roughly stationary (rates, changes, tone).
LEAD_CANDIDATES = {
    "funding_rate": "Perp funding",
    "oi_change_pct": "Open interest change",
    "news_volume_z": "News volume (z)",
    "news_tone": "News tone",
    "fear_greed_change": "Fear & Greed change",
}


@dataclass
class LeadLag:
    asset: str
    feature: str
    label: str
    target: str  # "return" or "abs_return"
    best_lag: int  # positive: feature leads the target by this many days
    best_corr: float
    corrs: dict[int, float]
    n: int


@dataclass
class Granger:
    asset: str
    feature: str
    label: str
    target: str
    min_p: float
    best_lag: int
    p_bonferroni: float
    n: int


@dataclass
class Regime:
    asset: str
    method: str
    current: str
    prob_high_vol: float | None
    high_vol_share: float
    high_vol_ann: float
    low_vol_ann: float


@dataclass
class Relationships:
    correlations_full: pd.DataFrame
    correlations_30d: pd.DataFrame
    betas: list[dict]
    lead_lag: list[LeadLag]
    granger: list[Granger]
    regimes: list[Regime]
    notes: list[str] = field(default_factory=list)


def returns_wide(df: pd.DataFrame) -> pd.DataFrame:
    return df.pivot(index="day", columns="asset", values="ret_1d").sort_index()


def betas(wide: pd.DataFrame, benchmark: str) -> list[dict]:
    """OLS beta of each asset's daily return on the benchmark's, full window and last 30d."""
    out = []
    for asset in wide.columns:
        if asset == benchmark:
            continue
        row: dict = {"asset": asset, "benchmark": benchmark}
        for name, frame in (("full", wide), ("30d", wide.tail(30))):
            pair = frame[[asset, benchmark]].dropna()
            if len(pair) < 10 or pair[benchmark].var() == 0:
                row[name] = None
                continue
            row[name] = float(pair[asset].cov(pair[benchmark]) / pair[benchmark].var())
            row[f"n_{name}"] = len(pair)
        out.append(row)
    return out


def lead_lag(df: pd.DataFrame, max_lag: int = 3) -> list[LeadLag]:
    """Correlation of feature(t - k) with return(t) for k in -max_lag..max_lag."""
    results = []
    for asset, rows in df.groupby("asset"):
        rows = rows.sort_values("day")
        for feature, label in LEAD_CANDIDATES.items():
            if feature not in rows or rows[feature].notna().sum() < MIN_OBS:
                continue
            for target, series in (
                ("return", rows["ret_1d"]),
                ("abs_return", rows["ret_1d"].abs()),
            ):
                corrs = {}
                for k in range(-max_lag, max_lag + 1):
                    pair = pd.concat([rows[feature].shift(k), series], axis=1).dropna()
                    if len(pair) >= MIN_OBS:
                        corrs[k] = float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
                if not corrs:
                    continue
                best = max(corrs, key=lambda k: abs(corrs[k]))
                results.append(
                    LeadLag(
                        asset=str(asset),
                        feature=feature,
                        label=label,
                        target=target,
                        best_lag=best,
                        best_corr=corrs[best],
                        corrs=corrs,
                        n=int(rows[[feature, "ret_1d"]].dropna().shape[0]),
                    )
                )
    return results


def granger(df: pd.DataFrame, max_lag: int = 3) -> list[Granger]:
    """Does the feature's past add to the return's own past (F-test, lags 1..max_lag)?"""
    from statsmodels.tools.sm_exceptions import InfeasibleTestError
    from statsmodels.tsa.stattools import grangercausalitytests

    raw = []
    for asset, rows in df.groupby("asset"):
        rows = rows.sort_values("day")
        for feature, label in LEAD_CANDIDATES.items():
            if feature not in rows:
                continue
            for target, series in (
                ("return", rows["ret_1d"]),
                ("abs_return", rows["ret_1d"].abs()),
            ):
                data = pd.concat([series, rows[feature]], axis=1).dropna()
                if len(data) < MIN_OBS or data.iloc[:, 1].std() == 0:
                    continue
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    try:
                        tests = grangercausalitytests(data.values, maxlag=max_lag)
                    except (ValueError, np.linalg.LinAlgError, InfeasibleTestError):
                        # A perfect or degenerate fit has no meaningful test statistic.
                        continue
                pvalues = {lag: float(res[0]["ssr_ftest"][1]) for lag, res in tests.items()}
                best = min(pvalues, key=pvalues.get)
                raw.append((str(asset), feature, label, target, pvalues[best], best, len(data)))
    tests_run = max(1, len(raw) * max_lag)
    return [
        Granger(a, f, lbl, t, p, lag, min(1.0, p * tests_run), n) for a, f, lbl, t, p, lag, n in raw
    ]


def regime(rows: pd.DataFrame) -> Regime | None:
    """Two-state volatility regime from a Markov-switching model of daily returns (switching
    variance), falling back to a median split of 7-day realised volatility if it fails."""
    from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

    rows = rows.sort_values("day")
    returns = rows["ret_1d"].dropna()
    asset = str(rows["asset"].iloc[0])
    if len(returns) < MIN_OBS:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Scale to percent: the optimiser is more stable away from tiny variances.
            fit = MarkovRegression(
                returns.values * 100, k_regimes=2, trend="c", switching_variance=True
            ).fit(disp=False)
        params = dict(zip(fit.model.param_names, fit.params, strict=True))
        variances = [float(params[f"sigma2[{k}]"]) for k in range(2)]
        high = int(np.argmax(variances))
        probs = fit.smoothed_marginal_probabilities[:, high]
        p_now = float(probs[-1])
        return Regime(
            asset=asset,
            method="markov-switching (2 states)",
            current="high volatility" if p_now >= 0.5 else "low volatility",
            prob_high_vol=p_now,
            high_vol_share=float((probs >= 0.5).mean()),
            high_vol_ann=math.sqrt(max(variances)) / 100 * math.sqrt(365),
            low_vol_ann=math.sqrt(min(variances)) / 100 * math.sqrt(365),
        )
    except (ValueError, np.linalg.LinAlgError, KeyError):
        rv = rows["rv_7d"].dropna()
        if rv.empty:
            return None
        median = float(rv.median())
        high_days = rv[rv > median]
        low_days = rv[rv <= median]
        return Regime(
            asset=asset,
            method="median split of 7-day realised volatility",
            current="high volatility" if float(rv.iloc[-1]) > median else "low volatility",
            prob_high_vol=None,
            high_vol_share=float(len(high_days) / len(rv)),
            high_vol_ann=float(high_days.mean()) if len(high_days) else median,
            low_vol_ann=float(low_days.mean()),
        )


def analyse(df: pd.DataFrame, benchmark: str = "BTC") -> Relationships:
    wide = returns_wide(df)
    notes = []
    days = int(wide.dropna(how="all").shape[0])
    if days < 60:
        notes.append(f"only {days} days of returns: treat every number here as rough")
    regimes = [r for _, rows in df.groupby("asset") if (r := regime(rows)) is not None]
    return Relationships(
        correlations_full=wide.corr(min_periods=MIN_OBS),
        correlations_30d=wide.tail(30).corr(min_periods=20),
        betas=betas(wide, benchmark) if benchmark in wide.columns else [],
        lead_lag=lead_lag(df),
        granger=granger(df),
        regimes=regimes,
        notes=notes,
    )
