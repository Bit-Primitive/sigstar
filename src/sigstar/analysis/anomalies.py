"""Anomalies: features that are far from their own recent history, every day.

Two measures per feature: the z-score built in dbt (mean and standard deviation of the
previous 30 days) and a robust z computed here (median and MAD of the previous 60 days,
scaled by 1.4826 so it matches a z-score for normal data). The robust one is not dragged
around by the very outliers it is looking for. Either at or above the threshold counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from sigstar.analysis.catalog import features_for, fmt_value

ROBUST_WINDOW = 60
ROBUST_MIN_OBS = 15
MAD_SCALE = 1.4826


@dataclass
class Anomaly:
    asset: str
    day: date
    feature: str
    label: str
    value: str
    z: float | None
    robust_z: float | None

    @property
    def size(self) -> float:
        return max(abs(self.z or 0.0), abs(self.robust_z or 0.0))


def robust_z(series: pd.Series, window: int = ROBUST_WINDOW) -> pd.Series:
    """(x - median) / (1.4826 * MAD) over the previous `window` values, excluding x."""
    past = series.shift(1).rolling(window, min_periods=ROBUST_MIN_OBS)
    median = past.median()
    mad = past.apply(lambda a: np.nanmedian(np.abs(a - np.nanmedian(a))), raw=True)
    scale = (mad * MAD_SCALE).where(mad > 0)
    return (series - median) / scale


def find(df: pd.DataFrame, threshold: float, start: date, end: date) -> list[Anomaly]:
    out: list[Anomaly] = []
    for asset, rows in df.groupby("asset"):
        rows = rows.sort_values("day").reset_index(drop=True)
        in_window = (rows["day"] >= start) & (rows["day"] <= end)
        onchain_ref = rows["ref_venue"] == "onchain" if "ref_venue" in rows else False
        for feature in features_for(list(df.columns), str(asset)):
            value_col = feature.value_column
            if value_col is None or value_col not in rows:
                continue
            values = rows[value_col].astype(float)
            if feature.z_column == "range_z" and isinstance(onchain_ref, pd.Series):
                # Exchange ranges against exchange ranges; on-chain ranges use the dbt z only.
                values = values.where(~onchain_ref)
            rz = robust_z(values)
            for i in rows.index[in_window]:
                z = rows.at[i, feature.z_column]
                z = None if pd.isna(z) else float(z)
                r = None if pd.isna(rz.iloc[i]) or math.isinf(rz.iloc[i]) else float(rz.iloc[i])
                if feature.coverage_gated:
                    # The dbt z-score compares comparable chain coverage only; a robust z
                    # over every past day would not, so it is not used for these.
                    r = None
                if max(abs(z or 0.0), abs(r or 0.0)) >= threshold:
                    out.append(
                        Anomaly(
                            asset=str(asset),
                            day=rows.at[i, "day"],
                            feature=feature.z_column,
                            label=feature.label,
                            value=fmt_value(feature, rows.at[i, value_col]),
                            z=None if z is None else round(z, 2),
                            robust_z=None if r is None else round(r, 2),
                        )
                    )
    return sorted(out, key=lambda a: (a.day, a.size), reverse=True)


@dataclass
class NotCompared:
    day: date
    assets: list[str]
    label: str
    note: str


def not_comparable(df: pd.DataFrame, start: date, end: date) -> list[NotCompared]:
    """On-chain features with no z-score in the window because coverage was not
    comparable, one entry per day, feature and reason (market-wide ones list every asset)."""
    found: dict[tuple, NotCompared] = {}
    rows = df[(df["day"] >= start) & (df["day"] <= end)]
    for feature in features_for(list(df.columns), ""):
        column = f"{feature.z_column}_note"
        if column not in rows:
            continue
        for _, r in rows[rows[column].notna()].iterrows():
            key = (r["day"], feature.label, r[column])
            entry = found.setdefault(key, NotCompared(r["day"], [], feature.label, r[column]))
            entry.assets.append(str(r["asset"]))
    return sorted(found.values(), key=lambda n: (n.day, n.label), reverse=True)
