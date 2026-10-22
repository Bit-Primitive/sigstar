"""Command-line interface. Every expected failure prints one `error:` and one `hint:` line;
`--debug` shows the traceback instead."""

from __future__ import annotations

import json
import sys
import traceback
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer

from sigstar import __version__, licences, render
from sigstar.alerts import RULES_TEMPLATE
from sigstar.config import CONFIG_FILENAME, Config, fred_api_key, load_config, render_template
from sigstar.disclaimer import DISCLAIMER
from sigstar.errors import SigstarError, bug_report_hint, hint_for

app = typer.Typer(
    help="Collect market, on-chain, news and macro data, organise it, and explain, forecast "
    "and alert on crypto price moves from every angle.\n\nNew here? `sigstar init`, then "
    "`sigstar run`.",
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

_state = {"debug": False, "config": None}

SOURCE_NAMES = "coinbase, kraken, binance, binance_futures, okx, fear_greed, gdelt, rss, fred"


@app.callback()
def _global(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help=f"Config file (default ./{CONFIG_FILENAME})."),
    ] = None,
    debug: Annotated[bool, typer.Option("--debug", help="Show full tracebacks.")] = False,
) -> None:
    _state["debug"] = debug
    _state["config"] = config


def _cfg() -> Config:
    return load_config(_state["config"])


def _parse_day(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SigstarError(f"{value!r} is not a date", "write dates as YYYY-MM-DD") from exc


@app.command()
def init(
    force: Annotated[bool, typer.Option(help="Overwrite existing files.")] = False,
) -> None:
    """Write a commented sigstar.yml and an example sigstar-rules.yml."""
    base = (_state["config"] or Path(CONFIG_FILENAME)).parent
    for path, text in (
        (_state["config"] or Path(CONFIG_FILENAME), render_template()),
        (base / "sigstar-rules.yml", RULES_TEMPLATE),
    ):
        if path.exists() and not force:
            typer.echo(f"kept     {path} (exists; --force to overwrite)")
            continue
        path.write_text(text)
        typer.echo(f"wrote    {path}")
    typer.echo("")
    typer.echo("API keys come from environment variables only, never from these files:")
    typer.echo(
        "  SIGSTAR_FRED_API_KEY       optional, macro data from FRED "
        f"({'set' if fred_api_key() else 'not set'})"
    )
    typer.echo("  SIGSTAR_ALERT_WEBHOOK_URL  optional, where alerts are POSTed")
    typer.echo("next: `sigstar doctor`, then `sigstar run`")


def _collect(cfg: Config, sources: list[str] | None) -> bool:
    from sigstar.collect import collect

    only = set(sources) if sources else None
    results, notices, seconds = collect(cfg, only)
    for notice in notices:
        typer.echo(f"notice   {notice}")
    ok = 0
    for r in results:
        if r.error:
            typer.echo(f"skipped  {r.stream:<38} {r.error}")
            if r.hint:
                typer.echo(f"         hint: {r.hint}")
        else:
            ok += 1
            note = f"  {r.note}" if r.note else ""
            typer.echo(f"collect  {r.stream:<38} {r.rows:>7,} rows{note}")
    typer.echo(f"collected {ok} of {len(results)} streams into {cfg.warehouse} in {seconds:.0f}s")
    return ok > 0


@app.command()
def collect(
    source: Annotated[
        list[str] | None,
        typer.Option("--source", "-s", help=f"Only these sources (repeatable): {SOURCE_NAMES}."),
    ] = None,
) -> None:
    """Fetch new data from every enabled source (incremental: only what is new)."""
    if not _collect(_cfg(), source):
        raise SigstarError(
            "no stream could be collected", "see the lines above; try `sigstar doctor`"
        )


def _build(cfg: Config) -> None:
    from sigstar.build import build as run_build

    result = run_build(cfg)
    typer.echo(
        f"build    dbt: {result.models} models, {result.tests_passed} tests passed"
        f"{f', {result.tests_warned} warnings' if result.tests_warned else ''} "
        f"in {result.seconds:.1f}s"
    )


@app.command()
def build() -> None:
    """Build the staging tables and marts with dbt, and run the data tests."""
    _build(_cfg())


@app.command()
def explain(
    asset: Annotated[str, typer.Argument(help="Asset symbol, e.g. BTC.")],
    day: Annotated[
        str | None,
        typer.Option("--date", "-d", help="UTC day YYYY-MM-DD (default: latest closed)."),
    ] = None,
    start: Annotated[str | None, typer.Option(help="With --top: window start.")] = None,
    end: Annotated[str | None, typer.Option(help="With --top: window end.")] = None,
    top: Annotated[
        int | None, typer.Option(help="Explain the N biggest moves in a window instead.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    """Explain a move: what else was unusual that day, ranked, with evidence."""
    from sigstar import workflow
    from sigstar.analysis import data
    from sigstar.analysis import explain as ex

    cfg = _cfg()
    df = data.features(cfg)
    symbol = workflow.resolve_asset(cfg, df, asset)
    if top:
        last = _parse_day(end) or workflow.latest_day(df, symbol)
        first = _parse_day(start) or last - timedelta(days=89)
        days = ex.biggest_moves(df, symbol, first, last, top)
    else:
        days = [_parse_day(day) or workflow.latest_day(df, symbol)]
    explanations = [workflow.explain_one(cfg, df, symbol, d) for d in days]
    licence = workflow.licence_line(cfg)
    if as_json:
        payload = [{**e.to_dict(), "licence": licence} for e in explanations]
        typer.echo(json.dumps(payload, indent=2, default=str))
        return
    typer.echo("\n\n".join(render.explanation(e) for e in explanations))
    typer.echo(licence)


@app.command()
def forecast(
    assets: Annotated[list[str] | None, typer.Argument(help="Assets (default: all).")] = None,
    target: Annotated[
        list[str] | None,
        typer.Option("--target", "-t", help="price, volatility or volume (repeatable)."),
    ] = None,
) -> None:
    """Forecast price, volatility and volume, each with its backtest error against naive."""
    from sigstar import workflow
    from sigstar.analysis import data
    from sigstar.analysis.forecast import TARGETS

    cfg = _cfg()
    df = data.features(cfg)
    targets = target or list(TARGETS)
    for t in targets:
        if t not in TARGETS:
            raise SigstarError(f"unknown target {t!r}", f"use one of {', '.join(TARGETS)}")
    symbols = (
        [workflow.resolve_asset(cfg, df, a) for a in assets]
        if assets
        else sorted(df["asset"].unique())
    )
    blocks = []
    for symbol in symbols:
        results = workflow.forecasts(cfg, df, symbol, targets)
        if not results:
            blocks.append(f"{symbol}: not enough history to forecast and backtest yet")
        blocks += [render.forecast(f) for f in results]
    typer.echo("\n\n".join(blocks))
    typer.echo(f"\n{DISCLAIMER}")
    typer.echo(workflow.licence_line(cfg))


@app.command()
def alerts(
    day: Annotated[str | None, typer.Option("--date", "-d", help="Evaluate this UTC day.")] = None,
    no_webhook: Annotated[bool, typer.Option("--no-webhook", help="Do not POST.")] = False,
) -> None:
    """Evaluate the rules in sigstar-rules.yml on the latest day (log + optional webhook)."""
    from sigstar import workflow
    from sigstar.analysis import data

    cfg = _cfg()
    fired, new, problems, _ = workflow.evaluate_alerts(
        cfg, data.features(cfg), _parse_day(day), send=not no_webhook
    )
    if not fired:
        typer.echo("no rule fired")
    for a in fired:
        typer.echo(render.alert(a))
    typer.echo(
        f"{len(fired)} alert(s), {len(new)} new, logged to "
        f"{cfg.resolve(cfg.alerts.get('log_file', 'alerts.jsonl'))}"
    )
    for p in problems:
        typer.echo(f"warning  {p}")
    typer.echo(workflow.licence_line(cfg))


@app.command()
def anomalies(
    days: Annotated[int, typer.Option(help="How many recent days to scan.")] = 7,
    threshold: Annotated[float | None, typer.Option(help="|z| threshold (default 3).")] = None,
) -> None:
    """List features far from their own recent history (z and robust z)."""
    from sigstar import workflow
    from sigstar.analysis import anomalies as an
    from sigstar.analysis import data

    cfg = _cfg()
    df = data.features(cfg)
    end = max(df.loc[df["ret_1d"].notna(), "day"])
    items = an.find(
        df, threshold or cfg.analysis.get("anomaly_z", 3.0), end - timedelta(days=days - 1), end
    )
    skipped = an.not_comparable(df, end - timedelta(days=days - 1), end)
    typer.echo(render.anomalies(items, skipped))
    typer.echo(workflow.licence_line(cfg))


@app.command()
def relationships(
    benchmark: Annotated[str, typer.Option(help="Asset for betas.")] = "BTC",
) -> None:
    """Correlations, betas, lead/lag, Granger tests and volatility regimes."""
    from sigstar import workflow
    from sigstar.analysis import data
    from sigstar.analysis import relationships as rel

    cfg = _cfg()
    typer.echo(render.relationships(rel.analyse(data.features(cfg), benchmark.upper())))
    typer.echo(workflow.licence_line(cfg))


def _report(cfg: Config, window_days: int) -> None:
    from sigstar import report as rp
    from sigstar import workflow

    brief = workflow.build_brief(cfg, window_days)
    md, html = rp.write(brief, cfg.report_dir)
    typer.echo(f"report   {licences.statement(brief.licence_mode, brief.sources_used)}")
    typer.echo(f"report   {md}")
    typer.echo(f"report   {html}")


@app.command()
def report(
    window: Annotated[int, typer.Option(help="Days covered by 'largest moves'.")] = 90,
) -> None:
    """Write the daily brief as Markdown and self-contained HTML."""
    _report(_cfg(), window)


@app.command()
def run(
    skip_collect: Annotated[bool, typer.Option(help="Use what is already collected.")] = False,
) -> None:
    """collect + build + alerts + report: the command for cron or a systemd timer."""
    from sigstar import workflow
    from sigstar.analysis import data

    cfg = _cfg()
    if not skip_collect and not _collect(cfg, None):
        raise SigstarError(
            "no stream could be collected", "see the lines above; try `sigstar doctor`"
        )
    _build(cfg)
    fired, new, problems, _ = workflow.evaluate_alerts(cfg, data.features(cfg))
    for a in new:
        typer.echo(render.alert(a))
    typer.echo(f"alerts   {len(fired)} fired, {len(new)} new")
    for p in problems:
        typer.echo(f"warning  {p}")
    _report(cfg, 90)


@app.command()
def doctor() -> None:
    """Check config, keys (set or not, never shown), connectivity and terms notes."""
    from sigstar.collect import make_client
    from sigstar.doctor import run_checks

    cfg = _cfg()
    checks = run_checks(cfg, make_client(cfg))
    for c in checks:
        typer.echo(f"{'ok  ' if c.ok else 'FAIL'}  {c.name:<20} {c.detail}")
    typer.echo("terms: see SOURCES.md for each source's terms, limits and attribution")
    if not all(c.ok for c in checks):
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the sigstar version."""
    typer.echo(f"sigstar {__version__}")


def main() -> None:
    try:
        app(standalone_mode=False)
    except SigstarError as exc:
        if _state["debug"]:
            traceback.print_exc()
        typer.echo(f"error: {exc}", err=True)
        if exc.hint:
            typer.echo(f"hint: {exc.hint}", err=True)
        sys.exit(1)
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
    except (typer.Abort, KeyboardInterrupt):
        typer.echo("error: interrupted", err=True)
        sys.exit(130)
    except Exception as exc:
        import click

        if isinstance(exc, click.ClickException):
            exc.show()
            sys.exit(exc.exit_code)
        if _state["debug"]:
            traceback.print_exc()
        typer.echo(f"error: {exc}", err=True)
        typer.echo(f"hint: {hint_for(exc) or bug_report_hint()}", err=True)
        sys.exit(1)
