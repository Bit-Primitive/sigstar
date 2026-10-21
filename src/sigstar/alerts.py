"""Alerts: user-defined rules from a YAML file, evaluated on the latest features.

A rule names assets and a list of conditions joined by `all` (every one must hold) or `any`
(at least one). A condition compares one features_daily column with a threshold:

    {feature: funding_rate, above: p95}     # above its own 95th percentile
    {feature: news_tone_z, below: -2}       # a plain number
    {feature: ret_1d_z, abs_above: 3}       # size either way

`pNN` thresholds are percentiles of that asset's own history of the feature over
`lookback_days` before the evaluated day (the day itself excluded). A condition on a
missing value does not hold, and the evidence says the value was missing.

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


def _threshold(condition: Condition, history: pd.Series) -> tuple[float | None, str]:
    if isinstance(condition.threshold, (int, float)):
        return float(condition.threshold), str(condition.threshold)
    q = float(_PERCENTILE.match(condition.threshold).group(1))  # type: ignore[union-attr]
    values = history.dropna()
    if len(values) < 20:
        return None, f"{condition.threshold} (needs 20 days of history, has {len(values)})"
    value = float(values.quantile(q / 100))
    return value, f"{condition.threshold} = {value:.6g}"


def _holds(op: str, value: float, threshold: float) -> bool:
    if op == "above":
        return value > threshold
    if op == "below":
        return value < threshold
    return abs(value) > threshold


def evaluate(
    rules: list[Rule], df: pd.DataFrame, lookback_days: int, day: date | None = None
) -> list[Alert]:
    """Evaluate every rule for each of its assets on `day` (default: each asset's latest
    day with a closed daily candle)."""
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
                threshold, described = _threshold(c, past[c.feature])
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
