"""Alerts: user-defined rules from a YAML file, evaluated on the latest features.

A rule names assets and a list of conditions joined by `all` (every one must hold) or `any`
(at least one). A condition compares one features_daily column with a threshold:

    {feature: funding_rate, above: p95}     # above its own 95th percentile
    {feature: news_tone_z, below: -2}       # a plain number
    {feature: ret_1d_z, abs_above: 3}       # size either way

`pNN` thresholds are percentiles of that asset's own history of the feature over
`lookback_days` before the evaluated day (the day itself excluded). A condition on a
missing value does not hold, and the evidence says the value was missing.

On-chain totals move with how much of each chain the chainstar warehouse holds, so for an
on-chain feature the history is only the earlier days that are comparable by the rule the
on-chain z-scores use (dbt: int_onchain_feature_z): every chain behind the evaluated day's
value was loaded on that day too, with a block count within `onchain_coverage_tolerance`,
and the value is summed over those same chains. With fewer than 20 such days the condition
is not evaluated (it never holds); the skip is recorded with its reason.

Fired alerts go to the console, are appended to a JSONL log with a CSV twin (once per rule,
asset and day), and are POSTed to SIGSTAR_ALERT_WEBHOOK_URL when that is set. Each carries
its evidence and the not-financial-advice notice.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml

from sigstar.disclaimer import DISCLAIMER
from sigstar.errors import SigstarError
from sigstar.http import USER_AGENT

OPERATORS = ("above", "below", "abs_above")
_PERCENTILE = re.compile(r"^p(\d{1,2}(\.\d+)?)$")

RULES_TEMPLATE = """\
# sigstar alert rules. A rule fires when its conditions hold on the latest closed day.
# Rules describe conditions you chose to watch; they are not advice to buy or sell.
#
# Condition operators: above, below, abs_above. Thresholds are numbers or pNN, a percentile
# of the asset's own history over lookback_days (the evaluated day excluded).
# Features are features_daily columns: see the README's feature table.

lookback_days: 90

rules:
  - name: eth_funding_hot_news_negative
    description: ETH funding above its 95th percentile while its news tone is unusually negative
    assets: [ETH]
    all:
      - {feature: funding_rate, above: p95}
      - {feature: news_tone_z, below: -2}

  - name: large_daily_move
    description: Daily return at least 3 standard deviations from its recent average
    assets: ["*"]
    all:
      - {feature: ret_1d_z, abs_above: 3}

  - name: volume_spike_with_news
    description: Trading volume and news coverage both unusually high on the same day
    assets: ["*"]
    all:
      - {feature: volume_z, above: 2}
      - {feature: news_volume_z, above: 2}

  - name: crowded_longs
    description: Funding in its top 10% while open interest grew unusually fast
    assets: ["*"]
    all:
      - {feature: funding_rate, above: p90}
      - {feature: oi_change_z, above: 2}
"""


@dataclass
class Condition:
    feature: str
    op: str
    threshold: float | str


@dataclass
class Rule:
    name: str
    description: str
    assets: list[str]
    mode: str  # "all" or "any"
    conditions: list[Condition]


@dataclass
class Alert:
    rule: str
    description: str
    asset: str
    day: str
    evidence: list[dict[str, Any]]
    fired_at: str
    disclaimer: str = DISCLAIMER
    delivered: list[str] = field(default_factory=list)

    def key(self) -> tuple[str, str, str]:
        return (self.rule, self.asset, self.day)


def load_rules(path: Path) -> tuple[list[Rule], int]:
    if not path.exists():
        raise SigstarError(
            f"rules file {path} does not exist", "run `sigstar init` to write an example"
        )
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise SigstarError(f"{path} is not valid YAML: {exc}", "fix the line it names") from exc
    rules = [_parse_rule(r, path) for r in data.get("rules") or []]
    return rules, int(data.get("lookback_days", 90))


def _parse_rule(raw: dict[str, Any], path: Path) -> Rule:
    name = raw.get("name") if isinstance(raw, dict) else None
    if not name:
        raise SigstarError(f"a rule in {path} has no name", "give every rule a name:")
    modes = [m for m in ("all", "any") if m in raw]
    if len(modes) != 1:
        raise SigstarError(
            f"rule {name} needs exactly one of all: or any:", "see the example in `sigstar init`"
        )
    conditions = []
    for c in raw[modes[0]] or []:
        ops = [op for op in OPERATORS if op in c]
        if "feature" not in c or len(ops) != 1:
            raise SigstarError(
                f"rule {name}: each condition needs feature: and one of {', '.join(OPERATORS)}",
                "for example {feature: funding_z, above: 2}",
            )
        threshold = c[ops[0]]
        if isinstance(threshold, str) and not _PERCENTILE.match(threshold):
            raise SigstarError(
                f"rule {name}: threshold {threshold!r} is neither a number nor pNN",
                "use a number such as 2 or a percentile such as p95",
            )
        conditions.append(Condition(str(c["feature"]), ops[0], threshold))
    if not conditions:
        raise SigstarError(f"rule {name} has no conditions", "add at least one condition")
    return Rule(
        name=str(name),
        description=str(raw.get("description", "")),
        assets=[str(a).upper() if a != "*" else "*" for a in raw.get("assets") or ["*"]],
        mode=modes[0],
        conditions=conditions,
    )


# features_daily value columns that are on-chain quantities, and how a day's value combines
# its chains' parts in int_onchain_feature_parts (sum, or one shared value).
ONCHAIN_PARTS = {
    "onchain_tx_count": "sum",
    "onchain_active_wallets": "sum",
    "onchain_fees_native": "sum",
    "stablecoin_transfer_amount": "sum",
    "onchain_dex_volume_usd": "sum",
    "onchain_swap_count": "sum",
    "onchain_price_gap_bps": "max",
    "range_pct": "max",  # only on days whose reference price is on-chain
}
# On-chain columns that measure the sample itself: no percentile of them is meaningful
# across days of different coverage, so pNN thresholds on them are never evaluated.
ONCHAIN_SAMPLE_SIZE = (
    "onchain_price_observations",
    "onchain_price_gap_hours",
    "onchain_coverage_seconds",
    "onchain_dex_coverage_seconds",
)
MIN_HISTORY = 20


@dataclass
class Skip:
    """A condition that was not evaluated because on-chain coverage was not comparable."""

    rule: str
    asset: str
    day: str
    feature: str
    reason: str


@dataclass
class Coverage:
    """Each on-chain feature's parts per chain and day (dbt: int_onchain_feature_parts:
    asset, day, feature, chain, value, blocks) and the tolerance on block counts."""

    parts: pd.DataFrame
    tolerance: float = 0.1

    def history(
        self, asset: str, feature: str, day: date, past_days: list[date]
    ) -> tuple[pd.Series, str | None] | None:
        """The feature's values on the comparable days among `past_days`, and why they
        are too few (None when enough). None when the feature has no on-chain parts that
        day (an exchange-priced day's range, say), so the plain history applies."""
        parts = self.parts[(self.parts["asset"] == asset) & (self.parts["feature"] == feature)]
        today = parts[parts["day"] == day]
        if today.empty:
            return None
        blocks = dict(zip(today["chain"], today["blocks"], strict=True))
        earlier = parts[parts["day"].isin(past_days) & parts["chain"].isin(blocks)]
        ratio = earlier["blocks"] / earlier["chain"].map(blocks)
        ok = earlier[(ratio <= 1 + self.tolerance) & (1 / ratio <= 1 + self.tolerance)]
        per_day = ok.groupby("day")
        combine = ONCHAIN_PARTS[feature]
        values = per_day["value"].sum() if combine == "sum" else per_day["value"].max()
        values = values[per_day["chain"].nunique() == len(blocks)]
        with_data = parts[parts["day"].isin(past_days)]["day"].nunique()
        if len(values) >= MIN_HISTORY or with_data < MIN_HISTORY:
            return values, None
        short = [
            f"{chain} loaded with similar coverage (blocks within "
            f"{self.tolerance * 100:.0f}%) on {ok[ok['chain'] == chain]['day'].nunique()} "
            f"of {with_data} earlier days"
            for chain in sorted(blocks)
            if ok[ok["chain"] == chain]["day"].nunique() < MIN_HISTORY
        ]
        detail = "; ".join(short) or (
            f"the same chains were loaded with similar coverage on only {len(values)} of "
            f"{with_data} earlier days"
        )
        return values, f"not comparable: {detail} ({MIN_HISTORY} needed)"


def _threshold(condition: Condition, history: pd.Series) -> tuple[float | None, str]:
    if isinstance(condition.threshold, (int, float)):
        return float(condition.threshold), str(condition.threshold)
    q = float(_PERCENTILE.match(condition.threshold).group(1))  # type: ignore[union-attr]
    values = history.dropna()
    if len(values) < MIN_HISTORY:
        return None, (
            f"{condition.threshold} (needs {MIN_HISTORY} days of history, has {len(values)})"
        )
    value = float(values.quantile(q / 100))
    return value, f"{condition.threshold} = {value:.6g}"


def _holds(op: str, value: float, threshold: float) -> bool:
    if op == "above":
        return value > threshold
    if op == "below":
        return value < threshold
    return abs(value) > threshold


def evaluate(
    rules: list[Rule],
    df: pd.DataFrame,
    lookback_days: int,
    day: date | None = None,
    coverage: Coverage | None = None,
    skipped: list[Skip] | None = None,
) -> list[Alert]:
    """Evaluate every rule for each of its assets on `day` (default: each asset's latest
    day with a closed daily candle). Percentile thresholds on on-chain features use only
    days of comparable coverage (`coverage`); conditions that could not be evaluated for
    that reason are appended to `skipped`."""
    alerts = []
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for rule in rules:
        for c in rule.conditions:
            if c.feature not in df.columns:
                raise SigstarError(
                    f"rule {rule.name}: unknown feature {c.feature!r}",
                    "use a column of sigstar.features_daily (see the README's feature table)",
                )
        assets = sorted(df["asset"].unique()) if "*" in rule.assets else rule.assets
        for asset in assets:
            rows = df[(df["asset"] == asset) & df["ret_1d"].notna()].sort_values("day")
            if rows.empty:
                continue
            eval_day = day or rows["day"].iloc[-1]
            current = rows[rows["day"] == eval_day]
            if current.empty:
                continue
            past = rows[
                (rows["day"] < eval_day) & (rows["day"] >= eval_day - timedelta(lookback_days))
            ]
            evidence, results = [], []
            for c in rule.conditions:
                raw_value = current[c.feature].iloc[0]
                value = None if pd.isna(raw_value) else float(raw_value)
                onchain_day = (
                    current["ref_venue"].iloc[0] == "onchain" if "ref_venue" in current else False
                )
                history, reason = _history(c, asset, eval_day, past, value, coverage, onchain_day)
                if reason is not None:
                    threshold, described = None, f"{c.threshold} (not evaluated: {reason})"
                    if skipped is not None:
                        skipped.append(
                            Skip(rule.name, asset, eval_day.isoformat(), c.feature, reason)
                        )
                else:
                    threshold, described = _threshold(c, history)
                held = (
                    value is not None
                    and threshold is not None
                    and not math.isnan(value)
                    and _holds(c.op, value, threshold)
                )
                results.append(held)
                evidence.append(
                    {
                        "feature": c.feature,
                        "value": value,
                        "condition": f"{c.op.replace('_', ' ')} {described}",
                        "held": held,
                    }
                )
            fired = all(results) if rule.mode == "all" else any(results)
            if fired:
                alerts.append(
                    Alert(rule.name, rule.description, asset, eval_day.isoformat(), evidence, now)
                )
    return alerts


def _history(
    c: Condition,
    asset: str,
    day: date,
    past: pd.DataFrame,
    value: float | None,
    coverage: Coverage | None,
    onchain_day: bool = False,
) -> tuple[pd.Series, str | None]:
    """The history a condition's percentile is taken from, or why there is none."""
    plain = past[c.feature]
    if isinstance(c.threshold, (int, float)) or value is None:
        return plain, None
    if c.feature in ONCHAIN_SAMPLE_SIZE:
        return plain, "it measures how much on-chain data was loaded, so days do not compare"
    if c.feature not in ONCHAIN_PARTS:
        return plain, None
    if c.feature == "range_pct" and "ref_venue" in past:
        # Exchange ranges against exchange ranges; on-chain days are handled below.
        plain = past.loc[past["ref_venue"] != "onchain", c.feature]
    if coverage is None:
        if c.feature == "range_pct" and not onchain_day:
            return plain, None
        return plain, "on-chain coverage is not available to compare days"
    found = coverage.history(asset, c.feature, day, list(past["day"]))
    if found is None:
        return plain, None
    return found


def record_skips(skips: list[Skip], log_file: Path) -> list[Skip]:
    """Append skipped conditions not logged before (by rule, asset, day, feature) to
    `<log>-skipped.jsonl`, next to the alert log. Returns the new ones."""
    path = log_file.with_name(f"{log_file.stem}-skipped.jsonl")
    seen: set[tuple[str, str, str, str]] = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                seen.add((item["rule"], item["asset"], item["day"], item["feature"]))
    new = [s for s in skips if (s.rule, s.asset, s.day, s.feature) not in seen]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            for skip in new:
                fh.write(json.dumps(asdict(skip)) + "\n")
    return new


def record(alerts: list[Alert], log_file: Path) -> list[Alert]:
    """Append alerts not logged before (by rule, asset, day). Returns the new ones."""
    seen: set[tuple[str, str, str]] = set()
    if log_file.exists():
        for line in log_file.read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                seen.add((item["rule"], item["asset"], item["day"]))
    new = [a for a in alerts if a.key() not in seen]
    if not new:
        return []
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a") as fh:
        for alert in new:
            fh.write(json.dumps(asdict(alert)) + "\n")
    csv_file = log_file.with_suffix(".csv")
    write_header = not csv_file.exists()
    with csv_file.open("a", newline="") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow(["fired_at", "rule", "asset", "day", "evidence", "disclaimer"])
        for a in new:
            summary = "; ".join(
                f"{e['feature']}={e['value']} ({e['condition']})" for e in a.evidence
            )
            writer.writerow([a.fired_at, a.rule, a.asset, a.day, summary, a.disclaimer])
    return new


def deliver_webhook(alerts: list[Alert], url: str) -> list[str]:
    """POST each alert as JSON. Returns problems (never the URL, which may hold a secret)."""
    problems = []
    for alert in alerts:
        text = f"sigstar alert {alert.rule} on {alert.asset} ({alert.day}). {alert.disclaimer}"
        try:
            response = requests.post(
                url,
                json={"text": text, "alert": asdict(alert)},
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            if response.status_code >= 400:
                problems.append(f"webhook answered HTTP {response.status_code} for {alert.rule}")
            else:
                alert.delivered.append("webhook")
        except requests.RequestException as exc:
            problems.append(f"webhook could not be reached ({type(exc).__name__})")
    return problems
