"""Explain a move: what else was unusual on the day an asset moved, ranked by z-score.

For one asset and day: the move itself (return and how large it was against the previous
30 days), then every feature's z-score that day and the day before, ranked by size, with
the raw value, and headlines as evidence for news features. Other assets' moves the same
day are listed as co-movement. Nothing here claims a cause; see disclaimer.CAUSATION.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import pandas as pd

from sigstar.analysis.catalog import Feature, features_for, fmt_value
from sigstar.errors import SigstarError

# Returns evidence rows (title, url, source, ...) for a news tag on a day.
HeadlineLookup = Callable[[str, date], list[dict]]


@dataclass
class Driver:
    feature: str
    label: str
    group: str
    timing: str  # "same day" or "day before"
    z: float
    value: str
    typical: str
    unusual: bool
    evidence: list[dict] = field(default_factory=list)


@dataclass
class Move:
    asset: str
    day: date
    close: float
    prev_close: float | None
    return_pct: float
    log_return: float
    z: float | None
    venue: str
    rank_in_window: int | None
    window_days: int


@dataclass
class Explanation:
    move: Move
    drivers: list[Driver]
    co_movements: list[dict]
    checked: int
    notes: list[str]
    # The asset's own headlines that day, as context whether or not news was unusual.
    headlines: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["move"]["day"] = self.move.day.isoformat()
        return data


def _finite(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _typical(history: pd.Series, feature: Feature) -> str:
    values = history.dropna()
    if values.empty:
        return "n/a"
    return f"median {fmt_value(feature, float(values.median()))}"


def explain_day(
    df: pd.DataFrame,
    asset: str,
    day: date,
    headlines: HeadlineLookup | None = None,
    driver_z: float = 2.0,
    top: int = 8,
    window_days: int = 30,
) -> Explanation:
    """Rank what coincided with `asset`'s move on `day`. `headlines(tag, day)` returns
    evidence rows for news features (left out when None)."""
    rows = df[df["asset"] == asset].sort_values("day").reset_index(drop=True)
    if rows.empty:
        raise SigstarError(f"no data for {asset}", "check the asset symbol, e.g. BTC")
    match = rows.index[rows["day"] == day]
    if len(match) == 0 or _finite(rows.loc[match[0], "ret_1d"]) is None:
        first, last = rows["day"].min(), rows["day"].max()
        raise SigstarError(
            f"no closed daily candle for {asset} on {day}",
            f"choose a day from {first} to {last} (today's candle is not closed yet)",
        )
    i = int(match[0])
    today = rows.loc[i]
    log_ret = float(today["ret_1d"])
    window = rows.loc[max(0, i - window_days + 1) : i]
    abs_rank = int((window["ret_1d"].abs() > abs(log_ret)).sum()) + 1
    move = Move(
        asset=asset,
        day=day,
        close=float(today["close"]),
        prev_close=_finite(rows.loc[i - 1, "close"]) if i > 0 else None,
        return_pct=math.exp(log_ret) - 1,
        log_return=log_ret,
        z=_finite(today.get("ret_1d_z")),
        venue=str(today["ref_venue"]),
        rank_in_window=abs_rank,
        window_days=len(window),
    )

    catalog = [f for f in features_for(list(df.columns), asset) if f.z_column != "ret_1d_z"]
    drivers: list[Driver] = []
    checked = 0
    for offset, timing in ((0, "same day"), (1, "day before")):
        if i - offset < 0:
            continue
        row = rows.loc[i - offset]
        history = rows.loc[max(0, i - offset - window_days) : i - offset - 1]
        for feature in catalog:
            z = _finite(row.get(feature.z_column))
            if z is None:
                continue
            checked += 1
            value_col = feature.value_column
            drivers.append(
                Driver(
                    feature=feature.z_column,
                    label=feature.label,
                    group=feature.group,
                    timing=timing,
                    z=round(z, 2),
                    value=fmt_value(feature, _finite(row.get(value_col))) if value_col else "",
                    typical=_typical(history[value_col], feature) if value_col else "",
                    unusual=abs(z) >= driver_z,
                )
            )
    # Same-day evidence outranks the day before at equal size: it is closer to the move.
    drivers.sort(key=lambda d: (abs(d.z), d.timing == "same day"), reverse=True)
    drivers = drivers[:top]
    if headlines is not None:
        tags = {f.z_column: f.tag for f in catalog}
        for d in drivers:
            tag = tags.get(d.feature)
            if d.group == "news" and tag and d.unusual:
                news_day = day if d.timing == "same day" else day - timedelta(days=1)
                d.evidence = headlines(tag, news_day)

    co = []
    for other, group in df[df["day"] == day].groupby("asset"):
        if other == asset:
            continue
        r = _finite(group["ret_1d"].iloc[0])
        if r is not None:
            co.append(
                {
                    "asset": other,
                    "return_pct": round(math.exp(r) - 1, 4),
                    "z": _finite(group["ret_1d_z"].iloc[0]),
                    "same_direction": (r > 0) == (log_ret > 0),
                }
            )
    context = headlines(asset, day)[:3] if headlines is not None else []
    return Explanation(move, drivers, co, checked, coverage_notes(today), context)


def coverage_notes(row: pd.Series) -> list[str]:
    """Say which angles had no data that day, so a short driver list is not misread."""
    notes = []
    missing = {
        "derivatives": ["funding_z", "oi_change_z"],
        "news": ["news_volume_z", "news_tone_z"],
        "on-chain": ["onchain_tx_count_z"],
    }
    for angle, cols in missing.items():
        if all(_finite(row.get(c)) is None for c in cols):
            notes.append(f"no {angle} z-scores for this day (not collected, or under 10 days)")
    coverage = _finite(row.get("onchain_coverage_seconds"))
    if coverage is not None and coverage < 86000:
        notes.append(
            f"on-chain numbers span only {coverage:.0f} seconds of this day "
            "(chainstar slice), so they are a sample, not the day's total"
        )
    return notes


def biggest_moves(df: pd.DataFrame, asset: str, start: date, end: date, top: int) -> list[date]:
    """Days with the largest moves for `asset` in [start, end], by |z| (|return| if no z)."""
    rows = df[(df["asset"] == asset) & (df["day"] >= start) & (df["day"] <= end)].copy()
    rows = rows[rows["ret_1d"].notna()]
    if rows.empty:
        raise SigstarError(
            f"no closed daily candles for {asset} between {start} and {end}",
            "widen the window or run `sigstar collect`",
        )
    rows["size"] = rows["ret_1d_z"].abs().fillna(rows["ret_1d"].abs() / rows["ret_1d"].std())
    return list(rows.sort_values("size", ascending=False)["day"].head(top))
