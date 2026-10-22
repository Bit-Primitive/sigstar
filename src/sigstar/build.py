"""Build: run the bundled dbt project (staging -> marts) against the warehouse.

sigstar writes a dbt profile into .sigstar/dbt (it holds only file paths, never secrets),
passes sigstar.yml's assets, topics, keyword rules and stablecoins as dbt vars, and keeps
dbt's target/ and logs/ there too, so the installed package directory stays read-only.
A chainstar warehouse, when configured, is attached read-only as `chainstar_wh`.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml

from sigstar import licences
from sigstar.config import Config
from sigstar.errors import SigstarError

PROJECT_DIR = Path(__file__).parent / "dbt"
MARTS_SCHEMA = "sigstar"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_REGEX_SPECIALS = set(r"\.^$|?*+()[]{}")


@dataclass
class BuildResult:
    models: int
    tests_passed: int
    tests_warned: int
    seconds: float


def keyword_pattern(keyword: str) -> str:
    """Whole-word, case-insensitive (input is lowercased) pattern for one keyword."""
    escaped = "".join(f"\\{c}" if c in _REGEX_SPECIALS else c for c in keyword.lower())
    return rf"\b{escaped}\b"


def news_rules(cfg: Config) -> list[dict[str, str]]:
    rules = []
    for asset in cfg.assets.values():
        rules += [
            {"tag": asset.symbol, "kind": "asset", "keyword": k, "pattern": keyword_pattern(k)}
            for k in asset.news_keywords
        ]
    for topic in cfg.topics.values():
        rules += [
            {"tag": topic.key, "kind": "topic", "keyword": k, "pattern": keyword_pattern(k)}
            for k in topic.news_keywords
        ]
    return rules


def dbt_vars(cfg: Config) -> dict[str, Any]:
    for key in cfg.topics:
        if not _IDENTIFIER.match(key):
            raise SigstarError(
                f"topic name {key!r} can only use lowercase letters, digits and _",
                "rename it under topics: in sigstar.yml (it becomes a column name)",
            )
    series = cfg.fred_series()
    for s in series:
        if not re.fullmatch(r"[A-Za-z0-9]+", s):
            raise SigstarError(f"FRED series id {s!r} is not valid", "use ids like DGS10")
    return {
        "assets": [
            {
                "symbol": a.symbol,
                "chains": list(a.chains),
                "onchain_prices": [{"chain": t.chain, "token": t.token} for t in a.onchain_prices],
                "dex_tokens": [{"chain": t.chain, "token": t.token} for t in a.dex_tokens],
            }
            for a in cfg.assets.values()
        ],
        "topics": list(cfg.topics),
        "news_rules": news_rules(cfg),
        "stablecoins": [
            {"chain": s.chain, "address": s.address, "symbol": s.symbol} for s in cfg.stablecoins
        ],
        "fred_series": series,
        "chainstar_enabled": cfg.chainstar_warehouse is not None,
        "licence": licence_vars(cfg),
        "zscore_window": int(cfg.analysis.get("zscore_window_days", 30)),
        "onchain_coverage_tolerance": float(cfg.analysis.get("onchain_coverage_tolerance", 0.1)),
    }


# Raw rows of each source, as the staging models see them: (table kind, venue or provider).
SOURCE_ROWS: dict[str, tuple[str, str]] = {
    "coinbase": ("candles", "coinbase"),
    "kraken": ("candles", "kraken"),
    "binance": ("candles", "binance"),
    "binance_futures": ("derivatives", "binance"),
    "okx": ("derivatives", "okx"),
    "gdelt": ("news", "gdelt"),
    "rss": ("news", "rss"),
}


def licence_vars(cfg: Config) -> dict[str, Any]:
    """What the staging models drop under the licence mode. Rows already collected (in an
    earlier personal-mode run, say) are dropped too, not only new collection."""
    mode = cfg.licence_mode
    excluded: dict[str, list[str]] = {"candles": [], "derivatives": [], "news": []}
    for source, (kind, value) in SOURCE_ROWS.items():
        if not licences.allows(mode, source):
            excluded[kind].append(value)
    return {
        "mode": mode,
        "excluded": excluded,
        "fear_greed": licences.allows(mode, "fear_greed"),
        # None keeps every series (personal mode); a list keeps only those.
        "fred_series": None
        if mode == licences.PERSONAL
        else (cfg.fred_series() if licences.allows(mode, "fred") else []),
    }


CHAINSTAR_PRICE_TABLES = ("fct_token_prices", "fct_swaps")


def chainstar_has_prices(path: Path) -> bool:
    """Whether the chainstar warehouse has swaps and USD prices (chainstar releases from
    October 2026 on; older warehouses have transfers and fees only)."""
    with duckdb.connect(str(path), read_only=True) as con:
        found = {
            row[0]
            for row in con.execute(
                "select table_name from information_schema.tables where table_schema = 'chainstar'"
            ).fetchall()
        }
    return all(t in found for t in CHAINSTAR_PRICE_TABLES)


def check_chainstar(path: Path) -> None:
    if not path.exists():
        raise SigstarError(
            f"chainstar warehouse {path} does not exist",
            "set chainstar: warehouse: in sigstar.yml to a DuckDB file built by chainstar, "
            "or remove it",
        )
    try:
        with duckdb.connect(str(path), read_only=True) as con:
            con.execute("select 1 from chainstar.fct_transfers limit 1")
    except duckdb.Error as exc:
        raise SigstarError(
            f"{path} has no chainstar tables ({exc.__class__.__name__})",
            "point chainstar: warehouse: at the file chainstar's `run` wrote (schema chainstar)",
        ) from exc


def write_profile(cfg: Config) -> Path:
    output: dict[str, Any] = {
        "type": "duckdb",
        "path": str(cfg.warehouse),
        "schema": MARTS_SCHEMA,
        "threads": 1,
        # Every timestamp sigstar stores is UTC; make the session agree.
        "settings": {"TimeZone": "UTC"},
    }
    if cfg.chainstar_warehouse is not None:
        check_chainstar(cfg.chainstar_warehouse)
        output["attach"] = [
            {"path": str(cfg.chainstar_warehouse), "alias": "chainstar_wh", "read_only": True}
        ]
    profile_dir = cfg.state_dir / "dbt"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile = {"sigstar": {"target": "duckdb", "outputs": {"duckdb": output}}}
    (profile_dir / "profiles.yml").write_text(yaml.safe_dump(profile, sort_keys=False))
    return profile_dir


def build(cfg: Config) -> BuildResult:
    if not cfg.warehouse.exists():
        raise SigstarError(
            f"the warehouse {cfg.warehouse} does not exist yet", "run `sigstar collect` first"
        )
    if cfg.licence_mode == licences.COMMERCIAL and cfg.chainstar_warehouse is None:
        raise SigstarError(
            "commercial mode has no price source: exchange prices are off, and prices then "
            "come from a chainstar warehouse",
            "set chainstar: warehouse: in sigstar.yml (or SIGSTAR_CHAINSTAR_WAREHOUSE), or use "
            "licence_mode: personal",
        )
    started = time.monotonic()
    profile_dir = write_profile(cfg)
    variables = dbt_vars(cfg)
    variables["chainstar_prices"] = cfg.chainstar_warehouse is not None and chainstar_has_prices(
        cfg.chainstar_warehouse
    )
    if cfg.licence_mode == licences.COMMERCIAL and not variables["chainstar_prices"]:
        raise SigstarError(
            f"{cfg.chainstar_warehouse} has no swaps or USD prices, so commercial mode has no "
            "price source",
            "rebuild it with a chainstar release that reads swaps: "
            "`chainstar sync --full-refresh` or any `chainstar run`",
        )
    target_dir = profile_dir / "target"
    run_results = target_dir / "run_results.json"
    run_results.unlink(missing_ok=True)
    # dbt runs in its own process: dbt-duckdb keeps its DuckDB connection open for the
    # life of the process, which would lock the warehouse against the analysis that follows.
    command = [
        sys.executable,
        "-m",
        "dbt.cli.main",
        "--no-send-anonymous-usage-stats",
        "build",
        "--project-dir",
        str(PROJECT_DIR),
        "--profiles-dir",
        str(profile_dir),
        "--target-path",
        str(target_dir),
        "--log-path",
        str(profile_dir / "logs"),
        "--vars",
        json.dumps(variables),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    log_hint = f"the full dbt log is in {profile_dir / 'logs' / 'dbt.log'}"
    if not run_results.exists():
        last = (completed.stdout + completed.stderr).strip().splitlines()[-3:]
        raise SigstarError(f"dbt build did not run: {' '.join(last)[:300]}", log_hint)
    results = json.loads(run_results.read_text())["results"]
    failed = [r for r in results if r["status"] in ("error", "fail")]
    if failed:
        detail = "; ".join(
            f"{r['unique_id'].split('.')[-1]}: {' '.join((r.get('message') or '').split())[:200]}"
            for r in failed[:3]
        )
        raise SigstarError(f"dbt build failed: {detail}", log_hint)
    models = [r for r in results if r["unique_id"].startswith("model.")]
    tests = [r for r in results if r["unique_id"].startswith("test.")]
    return BuildResult(
        models=len(models),
        tests_passed=sum(1 for r in tests if r["status"] == "pass"),
        tests_warned=sum(1 for r in tests if r["status"] == "warn"),
        seconds=time.monotonic() - started,
    )
