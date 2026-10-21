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
        for feature in features_for(list(df.columns), str(asset)):
            value_col = feature.value_column
            if value_col is None or value_col not in rows:
                continue
            rz = robust_z(rows[value_col].astype(float))
            for i in rows.index[in_window]:
                z = rows.at[i, feature.z_column]
                z = None if pd.isna(z) else float(z)
                r = None if pd.isna(rz.iloc[i]) or math.isinf(rz.iloc[i]) else float(rz.iloc[i])
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
