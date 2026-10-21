"""CLI behaviour (init, friendly errors) and the report's self-contained output."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from sigstar import report
from sigstar.analysis import explain, forecast, relationships
from sigstar.config import load_config
from sigstar.disclaimer import CAUSATION, DISCLAIMER


def sigstar(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "sigstar", *args], cwd=cwd, capture_output=True, text=True
    )


def test_init_writes_config_and_rules_with_no_secrets(tmp_path: Path) -> None:
    result = sigstar("init", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    config = (tmp_path / "sigstar.yml").read_text()
    data = yaml.safe_load(config)
    assert set(data["assets"]) == {"BTC", "ETH", "SOL"}
    assert data["sources"]["okx"]["enabled"] is False  # terms: off by default
    assert "api_key" not in config.lower().replace("sigstar_fred_api_key", "")
    rules = yaml.safe_load((tmp_path / "sigstar-rules.yml").read_text())
    assert rules["rules"]
    assert "kept" in sigstar("init", cwd=tmp_path).stdout  # does not overwrite


def test_errors_are_one_error_and_one_hint_line(tmp_path: Path) -> None:
    result = sigstar("explain", "BTC", cwd=tmp_path)
    assert result.returncode == 1
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("error: ") and lines[1].startswith("hint: ")


def test_debug_shows_a_traceback(tmp_path: Path) -> None:
    result = sigstar("--debug", "explain", "BTC", cwd=tmp_path)
    assert "Traceback" in result.stderr


def test_config_defaults_and_overrides(tmp_path: Path) -> None:
    (tmp_path / "sigstar.yml").write_text("assets:\n  DOGE: {coinbase: DOGE-USD}\n")
    cfg = load_config(tmp_path / "sigstar.yml")
    assert list(cfg.assets) == ["DOGE"]  # assets replace the defaults whole
    assert cfg.source_enabled("coinbase") and not cfg.source_enabled("okx")
    assert cfg.warehouse == tmp_path / "warehouse.duckdb"


def test_report_is_self_contained_and_carries_notices(
    tmp_path: Path, features: pd.DataFrame
) -> None:
    sections = []
    for asset in ("BTC", "ETH"):
        latest = explain.explain_day(features, asset, features["day"].max())
        f = forecast.forecast_asset(features, asset, ["price"], h=3, origins=5)
        sections.append(report.AssetSection(asset, latest, [latest], f))
    brief = report.Brief(
        day=date(2026, 9, 28),
        sections=sections,
        anomalies=[],
        alerts=[],
        relationships=relationships.analyse(features),
        features=features,
        sources_used=["coinbase", "fear_greed", "gdelt"],
        rss_attributions=["Bitcoin Magazine"],
        notes=[],
    )
    md_path, html_path = report.write(brief, tmp_path)
    html = html_path.read_text()
    md = md_path.read_text()
    for text in (md, html):
        assert DISCLAIMER in text or DISCLAIMER.replace("'", "&#x27;") in text
        assert "alternative.me" in text and "GDELT" in text
    assert CAUSATION in md
    assert "<script" not in html and "<link" not in html  # nothing loaded from elsewhere
    assert "<svg" in html
    charts = list((tmp_path / f"{md_path.stem}-charts").glob("*.svg"))
    assert charts and all(c.read_text().startswith("<svg") for c in charts)
