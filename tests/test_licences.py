"""Licence modes: commercial mode switches off every source whose terms restrict commercial
use, in collection, in the dbt staging models (rows collected earlier too), in doctor and in
what reports credit."""

from __future__ import annotations

from pathlib import Path

import pytest

from sigstar import licences
from sigstar.build import build, dbt_vars, licence_vars
from sigstar.config import load_config
from sigstar.doctor import run_checks
from sigstar.errors import SigstarError
from sigstar.streams import plan_streams

EXCHANGES = ("coinbase", "kraken", "binance", "binance_futures", "okx")


def _cfg(tmp_path: Path, extra: str = ""):  # type: ignore[no-untyped-def]
    (tmp_path / "sigstar.yml").write_text(
        "sources:\n  licence_mode: commercial\n  okx: {enabled: true}\n"
        "  fred: {series: [DFF, SP500, DGS10]}\n" + extra
    )
    return load_config(tmp_path / "sigstar.yml")


def test_every_restricted_source_is_recorded_with_its_terms() -> None:
    assert licences.restricted() == [*EXCHANGES, "rss"]
    for name in ("fear_greed", "gdelt", "fred", "chainstar"):
        assert licences.allows(licences.COMMERCIAL, name)
    assert not licences.allows(licences.COMMERCIAL, "some_new_source")  # unknown: restricted
    assert all(licences.allows(licences.PERSONAL, n) for n in [*licences.TERMS, "x"])


def test_commercial_mode_gates_sources_whatever_enabled_says(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert cfg.licence_mode == "commercial"
    for name in (*EXCHANGES, "rss"):
        assert cfg.source_configured(name)
        assert not cfg.source_enabled(name) and cfg.licence_blocked(name)
    assert cfg.source_enabled("fear_greed") and cfg.source_enabled("gdelt")
    # Only the public-domain FRED series survive.
    assert cfg.fred_series() == ["DFF", "DGS10"]


def test_default_is_personal_and_unchanged(tmp_path: Path) -> None:
    (tmp_path / "sigstar.yml").write_text("history: {daily_days: 90}\n")
    cfg = load_config(tmp_path / "sigstar.yml")
    assert cfg.licence_mode == "personal"
    assert cfg.source_enabled("coinbase") and not cfg.source_enabled("okx")
    assert not any(cfg.licence_blocked(n) for n in licences.TERMS)
    assert licence_vars(cfg) == {
        "mode": "personal",
        "excluded": {"candles": [], "derivatives": [], "news": []},
        "fear_greed": True,
        "fred_series": None,
    }


def test_unknown_mode_is_refused(tmp_path: Path) -> None:
    (tmp_path / "sigstar.yml").write_text("sources: {licence_mode: business}\n")
    with pytest.raises(SigstarError, match="licence_mode") as info:
        load_config(tmp_path / "sigstar.yml")
    assert "personal, commercial" in (info.value.hint or "")


def test_plan_has_no_restricted_streams_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIGSTAR_FRED_API_KEY", "test-key")
    cfg = _cfg(tmp_path)
    streams, notices = plan_streams(cfg)
    sources = {s.source for s in streams}
    assert sources == {"fear_greed", "gdelt", "fred"}
    assert {s.key for s in streams if s.source == "fred"} == {"fred:DFF", "fred:DGS10"}
    for name in (*EXCHANGES, "rss"):
        assert any(n.startswith(f"{name}: off in commercial mode") for n in notices), name
    assert any("SP500 left out in commercial mode" in n for n in notices)


def test_dbt_vars_drop_restricted_rows(tmp_path: Path) -> None:
    variables = dbt_vars(_cfg(tmp_path))
    assert variables["licence"] == {
        "mode": "commercial",
        "excluded": {
            "candles": ["coinbase", "kraken", "binance"],
            "derivatives": ["binance", "okx"],
            "news": ["rss"],
        },
        "fear_greed": True,
        "fred_series": ["DFF", "DGS10"],
    }
    assert variables["fred_series"] == ["DFF", "DGS10"]


def test_commercial_build_needs_a_chainstar_warehouse(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.warehouse.touch()
    with pytest.raises(SigstarError, match="no price source") as info:
        build(cfg)
    assert "chainstar" in (info.value.hint or "")


class _NoNetwork:
    """Doctor's client: records what it was asked to reach, answers nothing."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_text(self, url: str, params: dict | None = None, **_: object) -> str:
        self.urls.append(url)
        return "{}"


def test_doctor_states_the_mode_and_does_not_probe_restricted_sources(tmp_path: Path) -> None:
    client = _NoNetwork()
    checks = {c.name: c for c in run_checks(_cfg(tmp_path), client)}  # type: ignore[arg-type]
    assert checks["licence mode"].detail.startswith("commercial")
    for name in EXCHANGES:
        assert checks[name].ok and checks[name].detail.startswith("off in commercial mode")
    assert checks["rss"].detail.startswith("off in commercial mode")
    assert not any("coinbase" in u or "kraken" in u or "binance" in u for u in client.urls)
    assert not any("okx" in u or "bitcoinmagazine" in u for u in client.urls)
    # No chainstar warehouse: commercial mode has no prices, which doctor flags.
    assert not checks["chainstar"].ok
    assert "commercial" in checks["fred"].detail


def test_statement_names_mode_and_sources() -> None:
    line = licences.statement("commercial", ["fear_greed", "chainstar"])
    assert "commercial" in line and "fear_greed, chainstar" in line
    assert all(name in line for name in licences.restricted())
    assert "not legal advice" in line
    assert licences.statement("personal", ["coinbase"]).startswith("Licence mode: personal")
