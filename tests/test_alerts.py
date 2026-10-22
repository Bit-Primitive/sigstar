"""The rules engine: parsing, percentile thresholds, missing data, logging, webhook."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from sigstar import alerts
from sigstar.disclaimer import DISCLAIMER
from sigstar.errors import SigstarError


def write_rules(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.yml"
    path.write_text(text)
    return path


def test_example_rules_template_parses(tmp_path: Path) -> None:
    rules, lookback = alerts.load_rules(write_rules(tmp_path, alerts.RULES_TEMPLATE))
    assert lookback == 90
    eth = next(r for r in rules if r.name == "eth_funding_hot_news_negative")
    assert eth.assets == ["ETH"] and eth.mode == "all"
    assert [(c.feature, c.op, c.threshold) for c in eth.conditions] == [
        ("funding_rate", "above", "p95"),
        ("news_tone_z", "below", -2),
    ]


def test_percentile_threshold_uses_history_before_the_day(features: pd.DataFrame) -> None:
    eth = features["asset"] == "ETH"
    last = features.loc[eth, "day"].max()
    features.loc[eth & (features["day"] == last), "funding_rate"] = 0.01
    features.loc[eth & (features["day"] == last), "news_tone_z"] = -2.5
    rule = alerts.Rule(
        "hot",
        "test",
        ["ETH"],
        "all",
        [
            alerts.Condition("funding_rate", "above", "p95"),
            alerts.Condition("news_tone_z", "below", -2),
        ],
    )
    (fired,) = alerts.evaluate([rule], features, lookback_days=90)
    assert fired.asset == "ETH" and fired.day == last.isoformat()
    assert fired.disclaimer == DISCLAIMER
    history = features.loc[eth & (features["day"] < last), "funding_rate"]
    assert fired.evidence[0]["condition"].startswith("above p95 = ")
    threshold = float(fired.evidence[0]["condition"].split("= ")[1])
    assert threshold == pytest.approx(history.quantile(0.95), rel=1e-5)


def test_missing_value_does_not_fire(features: pd.DataFrame) -> None:
    features["news_tone_z"] = float("nan")
    rule = alerts.Rule("r", "", ["*"], "any", [alerts.Condition("news_tone_z", "below", 0)])
    assert alerts.evaluate([rule], features, 90) == []


def test_unknown_feature_is_explained(features: pd.DataFrame) -> None:
    rule = alerts.Rule("r", "", ["*"], "all", [alerts.Condition("nope", "above", 1)])
    with pytest.raises(SigstarError, match="unknown feature"):
        alerts.evaluate([rule], features, 90)


@pytest.mark.parametrize(
    "text, message",
    [
        ("rules:\n  - name: a\n    all: [{feature: x}]\n", "one of above"),
        ("rules:\n  - name: a\n    all: [{feature: x, above: high}]\n", "neither a number"),
        ("rules:\n  - name: a\n", "exactly one of all"),
    ],
)
def test_bad_rules_say_what_is_wrong(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(SigstarError, match=message):
        alerts.load_rules(write_rules(tmp_path, text))


def _alert(day: str = "2026-10-20") -> alerts.Alert:
    return alerts.Alert(
        "r",
        "desc",
        "BTC",
        day,
        [{"feature": "f", "value": 1.0, "condition": "above 0", "held": True}],
        "2026-10-21T00:00:00+00:00",
    )


def test_record_logs_once_per_rule_asset_day(tmp_path: Path) -> None:
    log = tmp_path / "alerts.jsonl"
    assert len(alerts.record([_alert()], log)) == 1
    assert alerts.record([_alert()], log) == []
    assert len(alerts.record([_alert("2026-10-21")], log)) == 1
    lines = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["day"] for x in lines] == ["2026-10-20", "2026-10-21"]
    assert all(x["disclaimer"] == DISCLAIMER for x in lines)
    csv_lines = log.with_suffix(".csv").read_text().splitlines()
    assert csv_lines[0].startswith("fired_at,rule,asset,day") and len(csv_lines) == 3


def test_webhook_failure_never_shows_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise requests.ConnectionError("https://hooks.example/SECRET")

    monkeypatch.setattr(requests, "post", boom)
    problems = alerts.deliver_webhook([_alert()], "https://hooks.example/SECRET")
    assert problems and all("SECRET" not in p for p in problems)


def test_evaluate_on_a_given_day(features: pd.DataFrame) -> None:
    rule = alerts.Rule(
        "any_move", "", ["BTC"], "all", [alerts.Condition("ret_1d_z", "abs_above", 0)]
    )
    (fired,) = alerts.evaluate([rule], features, 90, day=date(2026, 8, 1))
    assert fired.day == "2026-08-01"


def test_percentiles_of_onchain_sample_size_are_never_evaluated(features: pd.DataFrame) -> None:
    features["onchain_coverage_seconds"] = range(len(features))
    rule = alerts.Rule(
        "big_sample",
        "",
        ["BTC"],
        "all",
        [alerts.Condition("onchain_coverage_seconds", "above", "p5")],
    )
    skipped: list[alerts.Skip] = []
    assert alerts.evaluate([rule], features, 90, skipped=skipped) == []
    (skip,) = skipped
    assert skip.feature == "onchain_coverage_seconds"
    assert "how much on-chain data was loaded" in skip.reason
    # A plain number is the user's own threshold and is still checked.
    numeric = alerts.Rule(
        "n", "", ["BTC"], "all", [alerts.Condition("onchain_coverage_seconds", "above", 0)]
    )
    assert alerts.evaluate([numeric], features, 90)
