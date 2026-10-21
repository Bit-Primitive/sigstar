"""The analysis steps the CLI and `sigstar run` share: load features, explain, forecast,
evaluate alerts and assemble the daily brief."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from sigstar import alerts as alerts_mod
from sigstar import report
from sigstar.analysis import anomalies, data, explain, forecast, relationships
from sigstar.config import Config, fred_api_key, webhook_url
from sigstar.errors import SigstarError


def headline_lookup(cfg: Config) -> explain.HeadlineLookup:
    def lookup(tag: str, day: date) -> list[dict]:
        rows = data.headlines(cfg, tag, day)
        return [
            {"title": r.title, "url": r.url, "source": r.source, "provider": r.provider}
            for r in rows.itertuples()
        ]

    return lookup


def resolve_asset(cfg: Config, df: pd.DataFrame, asset: str) -> str:
    symbol = asset.upper()
    if symbol not in set(df["asset"]):
        known = ", ".join(sorted(df["asset"].unique()))
        raise SigstarError(f"no data for {symbol}", f"assets with data: {known}")
    return symbol


def explain_one(cfg: Config, df: pd.DataFrame, asset: str, day: date) -> explain.Explanation:
    return explain.explain_day(
        df,
        asset,
        day,
        headlines=headline_lookup(cfg),
        driver_z=cfg.analysis.get("driver_z", 2.0),
        window_days=int(cfg.analysis.get("zscore_window_days", 30)),
    )


def latest_day(df: pd.DataFrame, asset: str) -> date:
    rows = df[(df["asset"] == asset) & df["ret_1d"].notna()]
    if rows.empty:
        raise SigstarError(f"no closed daily candles for {asset} yet", "run `sigstar collect`")
    return max(rows["day"])


def forecasts(
    cfg: Config, df: pd.DataFrame, asset: str, targets: list[str]
) -> list[forecast.Forecast]:
    return forecast.forecast_asset(
        df,
        asset,
        targets,
        h=int(cfg.analysis.get("forecast_horizon_days", 7)),
        origins=int(cfg.analysis.get("backtest_origins", 20)),
    )


def evaluate_alerts(
    cfg: Config, df: pd.DataFrame, day: date | None = None, send: bool = True
) -> tuple[list[alerts_mod.Alert], list[alerts_mod.Alert], list[str]]:
    """Evaluate rules; log new alerts; send them to the webhook. Returns (all fired, newly
    logged, problems)."""
    rules_path = cfg.resolve(cfg.alerts.get("rules_file", "sigstar-rules.yml"))
    rules, lookback = alerts_mod.load_rules(rules_path)
    fired = alerts_mod.evaluate(rules, df, lookback, day)
    new = alerts_mod.record(fired, cfg.resolve(cfg.alerts.get("log_file", "alerts.jsonl")))
    problems: list[str] = []
    url = webhook_url()
    if send and url and new:
        problems = alerts_mod.deliver_webhook(new, url)
    return fired, new, problems


def build_brief(cfg: Config, window_days: int = 90, top_moves: int = 3) -> report.Brief:
    df = data.features(cfg)
    sections = []
    start, end = report.window(df, window_days)
    for asset in sorted(df["asset"].unique()):
        rows = df[(df["asset"] == asset) & df["ret_1d"].notna()]
        if rows.empty:
            continue
        latest = explain_one(cfg, df, asset, latest_day(df, asset))
        moves = [
            explain_one(cfg, df, asset, d)
            for d in explain.biggest_moves(df, asset, start, end, top_moves)
        ]
        sections.append(
            report.AssetSection(
                asset, latest, moves, forecasts(cfg, df, asset, list(forecast.TARGETS))
            )
        )
    fired, _, _ = evaluate_alerts(cfg, df)
    threshold = cfg.analysis.get("anomaly_z", 3.0)
    found = anomalies.find(df, threshold, end - timedelta(days=6), end)
    rel = relationships.analyse(df, benchmark="BTC")
    notes = []
    onchain = None
    if cfg.chainstar_warehouse is not None:
        onchain = data.read_table(cfg, "fct_onchain_daily")
        onchain["day"] = pd.to_datetime(onchain["day"]).dt.date
        days = ", ".join(sorted(map(str, onchain["day"].unique())))
        notes.append(
            f"On-chain data: chainstar warehouse {cfg.chainstar_warehouse}, "
            f"{onchain['day'].nunique()} day(s) ({days})."
        )
    used = [
        name
        for name in report.ATTRIBUTIONS
        if cfg.source_enabled(name) and (name != "fred" or fred_api_key() is not None)
    ]
    rss = [
        f.get("attribution", f["name"])
        for f in (cfg.sources.get("rss", {}).get("feeds") or [])
        if cfg.source_enabled("rss")
    ]
    return report.Brief(
        day=end,
        sections=sections,
        anomalies=found,
        alerts=fired,
        relationships=rel,
        features=df,
        sources_used=used,
        rss_attributions=rss,
        notes=notes,
        onchain=onchain,
    )
