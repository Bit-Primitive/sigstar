"""What each feature means, so explanations, anomalies and alerts can name it plainly.

Every `<name>_z` column in features_daily is a candidate driver. Topic and macro columns
are generated from sigstar.yml, so they are recognised by pattern rather than listed.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from sigstar.sources.fred import SERIES_LABELS


def _pct(v: float) -> str:
    return f"{v * 100:+.2f}%"


def _pct3(v: float) -> str:
    return f"{v * 100:+.4f}%"


def _usd(v: float) -> str:
    for unit, size in (("bn", 1e9), ("m", 1e6), ("k", 1e3)):
        if abs(v) >= size:
            return f"${v / size:,.2f}{unit}"
    return f"${v:,.0f}"


def _num(v: float) -> str:
    return f"{v:,.2f}"


def _int(v: float) -> str:
    return f"{v:,.0f}"


def _bps(v: float) -> str:
    return f"{v:,.1f} bp"


@dataclass(frozen=True)
class Feature:
    z_column: str
    value_column: str | None
    label: str
    group: str
    fmt: Callable[[float], str]
    tag: str | None = None  # news tag whose headlines are evidence for this feature


STATIC: tuple[Feature, ...] = (
    Feature("ret_1d_z", "ret_1d", "Daily log return", "market", _pct),
    Feature("range_z", "range_pct", "Intraday high-low range", "market", _pct),
    Feature("rv_intraday_z", "rv_intraday", "Intraday volatility (hourly returns)", "market", _pct),
    Feature("volume_z", "volume_quote", "Trading volume (all venues)", "market", _usd),
    Feature("spread_z", "spread_bps", "Price gap between USD venues", "market", _bps),
    Feature("funding_z", "funding_rate", "Perpetual funding (day's total)", "derivatives", _pct3),
    Feature(
        "oi_change_z", "oi_change_pct", "Open interest change (contracts)", "derivatives", _pct
    ),
    Feature("news_volume_z", "news_volume", "News volume (GDELT articles)", "news", _int),
    Feature("news_tone_z", "news_tone", "News tone (GDELT, -10..+10)", "news", _num),
    Feature(
        "fear_greed_change_z", "fear_greed_change", "Fear & Greed Index change", "sentiment", _num
    ),
    Feature("onchain_tx_count_z", "onchain_tx_count", "On-chain transactions", "onchain", _int),
    Feature(
        "onchain_active_wallets_z",
        "onchain_active_wallets",
        "On-chain active wallets",
        "onchain",
        _int,
    ),
    Feature(
        "stablecoin_transfer_z",
        "stablecoin_transfer_amount",
        "Stablecoin transfer volume",
        "onchain",
        _usd,
    ),
)

_TOPIC = re.compile(r"^news_(?P<topic>[a-z0-9_]+)_(?P<kind>volume|tone)_z$")
_MACRO = re.compile(r"^macro_(?P<series>[a-z0-9]+)_change_z$")


def features_for(columns: list[str], asset: str) -> list[Feature]:
    """The driver features present in features_daily, static ones first."""
    present = set(columns)
    found = [f for f in STATIC if f.z_column in present]
    for column in columns:
        if column in {f.z_column for f in STATIC}:
            continue
        if m := _TOPIC.match(column):
            topic, kind = m["topic"], m["kind"]
            label = f"News {'volume' if kind == 'volume' else 'tone'}: topic '{topic}'"
            value = f"news_{topic}_{kind}"
            found.append(
                Feature(column, value, label, "news", _int if kind == "volume" else _num, topic)
            )
        elif m := _MACRO.match(column):
            series = m["series"].upper()
            label = f"Macro: {SERIES_LABELS.get(series, series)} change"
            found.append(Feature(column, f"macro_{m['series']}_change", label, "macro", _num))
    # Asset news features carry the asset's own headlines as evidence.
    return [
        Feature(f.z_column, f.value_column, f.label, f.group, f.fmt, asset)
        if f.z_column in ("news_volume_z", "news_tone_z")
        else f
        for f in found
    ]


def fmt_value(feature: Feature, value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return feature.fmt(float(value))
