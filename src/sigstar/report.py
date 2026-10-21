"""The daily brief: one Markdown and one self-contained HTML file per day.

Both are rendered from the same list of blocks, so they always say the same thing. Charts
are inline SVG in the HTML and separate .svg files next to the Markdown; nothing is loaded
from the network when either is opened.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape
from pathlib import Path

import pandas as pd

from sigstar import __version__, render
from sigstar.alerts import Alert
from sigstar.analysis.anomalies import Anomaly
from sigstar.analysis.explain import Explanation
from sigstar.analysis.forecast import Forecast
from sigstar.analysis.relationships import Relationships
from sigstar.disclaimer import CAUSATION, DISCLAIMER
from sigstar.http import PROJECT_URL
from sigstar.svg import Band, Chart, Marker, Series
from sigstar.svg import render as render_svg

ATTRIBUTIONS: dict[str, str] = {
    "coinbase": "Market data: Coinbase Exchange public API.",
    "kraken": "Market data: Kraken public API.",
    "binance": "Market data: Binance public market data API (data-api.binance.vision).",
    "binance_futures": "Derivatives data: Binance USDⓈ-M futures public API.",
    "okx": "Derivatives data: OKX public API.",
    "fear_greed": "Crypto Fear & Greed Index: alternative.me (https://alternative.me/crypto/).",
    "gdelt": "News coverage data: The GDELT Project (https://www.gdeltproject.org/).",
    "fred": (
        "This product uses the FRED® API but is not endorsed or certified by the Federal "
        "Reserve Bank of St. Louis. Series DFF, DGS10: Board of Governors of the Federal "
        "Reserve System (H.15) via FRED; DTWEXBGS: Board of Governors (H.10) via FRED."
    ),
}
PERSONAL_USE = (
    "Exchange market data is licensed by the venues for your personal use: keep this report "
    "to yourself rather than publishing or redistributing it (see SOURCES.md)."
)


@dataclass
class Block:
    kind: str  # h1, h2, h3, p, note, table, links, chart
    text: str = ""
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    links: list[tuple[str, str | None]] = field(default_factory=list)
    svg: str = ""
    name: str = ""


@dataclass
class AssetSection:
    asset: str
    latest: Explanation | None
    top_moves: list[Explanation]
    forecasts: list[Forecast]


@dataclass
class Brief:
    day: date
    sections: list[AssetSection]
    anomalies: list[Anomaly]
    alerts: list[Alert]
    relationships: Relationships | None
    features: pd.DataFrame
    sources_used: list[str]
    rss_attributions: list[str]
    notes: list[str]
    onchain: pd.DataFrame | None = None


def _pct(v: float | None) -> str:
    return "n/a" if v is None or math.isnan(v) else f"{v * 100:+.2f}%"


def _driver_rows(e: Explanation) -> list[list[str]]:
    return [[d.label, d.timing, render.z_text(d.z), d.value, d.typical] for d in e.drivers]


def price_chart(asset: str, df: pd.DataFrame, moves: list[Explanation]) -> str:
    rows = df[(df["asset"] == asset) & df["close"].notna()].sort_values("day")
    points = list(zip(rows["day"], rows["close"].astype(float), strict=True))
    markers = [
        Marker(m.move.day, m.move.close, f"{m.move.day:%b %d} {m.move.return_pct * 100:+.1f}%")
        for m in moves
    ]
    return render_svg(
        Chart(
            f"{asset} daily close (reference venue) with the largest moves marked",
            [Series(points, "--series-1", "#2a78d6", f"{asset} close")],
            _axis_money,
            markers=markers,
        )
    )


def _axis_money(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}"


def forecast_chart(f: Forecast) -> str:
    history = [(d, v) for d, v in f.history[-30:]]
    forecast = [(f.last_day, f.last_value), *zip(f.days, f.point, strict=True)]
    bands = []
    if f.lower and f.upper:
        bands.append(
            Band(
                [(f.last_day, f.last_value, f.last_value)]
                + [(d, lo, hi) for d, lo, hi in zip(f.days, f.lower, f.upper, strict=True)],
                "--series-1",
                "#2a78d6",
                "80% prediction interval",
            )
        )
    return render_svg(
        Chart(
            f"{f.asset} {f.target}: last 30 days and {f.horizon}-day {f.best_model} forecast",
            [
                Series(history, "--series-1", "#2a78d6", "actual"),
                Series(forecast, "--series-1", "#2a78d6", f"{f.best_model} forecast", dashed=True),
            ],
            _axis_money if f.target == "price" else lambda v: render.target_value(f.target, v),
            bands=bands,
        )
    )


def build_blocks(brief: Brief) -> list[Block]:
    b: list[Block] = [
        Block("h1", f"sigstar daily brief: {brief.day.isoformat()}"),
        Block("note", DISCLAIMER),
        Block(
            "p",
            "Each day below is a closed UTC day. Moves are close-to-close log returns on the "
            "reference venue; z-scores compare a value with the previous 30 days.",
        ),
    ]
    summary = []
    regimes = {r.asset: r for r in (brief.relationships.regimes if brief.relationships else [])}
    for s in brief.sections:
        if s.latest is None:
            continue
        m = s.latest.move
        row = brief.features[
            (brief.features["asset"] == s.asset) & (brief.features["day"] == m.day)
        ]
        ret7 = float(row["ret_7d"].iloc[0]) if not row.empty else math.nan
        rv30 = float(row["rv_30d"].iloc[0]) if not row.empty else math.nan
        regime = regimes.get(s.asset)
        summary.append(
            [
                s.asset,
                m.day.isoformat(),
                render.money(m.close),
                _pct(m.return_pct),
                render.z_text(m.z),
                _pct(math.exp(ret7) - 1 if not math.isnan(ret7) else math.nan),
                "n/a" if math.isnan(rv30) else f"{rv30 * 100:.0f}%",
                regime.current if regime else "n/a",
            ]
        )
    b.append(Block("h2", "Overview"))
    b.append(
        Block(
            "table",
            headers=["Asset", "Day", "Close", "1-day", "Size", "7-day", "30d vol", "Regime"],
            rows=summary,
        )
    )
    for note in brief.notes:
        b.append(Block("note", note))

    for s in brief.sections:
        b.append(Block("h2", s.asset))
        b.append(
            Block(
                "chart",
                svg=price_chart(s.asset, brief.features, s.top_moves),
                name=f"{s.asset.lower()}-price",
            )
        )
        if s.latest is not None:
            m = s.latest.move
            b.append(
                Block("h3", f"Latest day, {m.day}: {_pct(m.return_pct)} ({render.z_text(m.z)})")
            )
            b += _explanation_blocks(s.latest)
        if s.top_moves:
            b.append(Block("h3", "Largest moves in the window"))
            for e in s.top_moves:
                top = "; ".join(
                    f"{d.label} {render.z_text(d.z)} ({d.timing})" for d in e.drivers[:3]
                )
                b.append(
                    Block(
                        "p",
                        f"{e.move.day}: {_pct(e.move.return_pct)} ({render.z_text(e.move.z)}). "
                        f"Coincided with: {top or 'no z-scores yet'}.",
                    )
                )
        if s.forecasts:
            b.append(Block("h3", "Forecasts and how accurate they have been"))
            rows = []
            for f in s.forecasts:
                naive = next(x for x in f.scores if x.model == "naive")
                best = next(x for x in f.scores if x.model == f.best_model)
                interval = (
                    f"{render.target_value(f.target, f.lower[-1])} to "
                    f"{render.target_value(f.target, f.upper[-1])}"
                    if f.lower and f.upper
                    else "n/a"
                )
                rows.append(
                    [
                        f.target,
                        render.target_value(f.target, f.last_value),
                        f.best_model,
                        render.target_value(f.target, f.point[-1]),
                        interval,
                        f"{render.target_value(f.target, best.mae)} / "
                        f"{render.target_value(f.target, naive.mae)}",
                        "yes" if f.beats_naive else "no",
                    ]
                )
            b.append(
                Block(
                    "table",
                    headers=[
                        "Target",
                        "Last",
                        "Model",
                        f"In {s.forecasts[0].horizon} days",
                        "80% interval",
                        "Backtest MAE model / naive",
                        "Beats naive",
                    ],
                    rows=rows,
                )
            )
            b.append(
                Block(
                    "note",
                    "MAE is the average miss over rolling backtests of the last "
                    f"{s.forecasts[0].scores[0].origins} days. When no model beats naive "
                    "(no change), the naive forecast is the one shown: treat it as a baseline, "
                    "not a prediction.",
                )
            )
            price = next((f for f in s.forecasts if f.target == "price"), None)
            if price is not None:
                b.append(
                    Block("chart", svg=forecast_chart(price), name=f"{s.asset.lower()}-forecast")
                )

    b.append(Block("h2", "Anomalies"))
    if brief.anomalies:
        b.append(
            Block(
                "table",
                headers=["Day", "Asset", "Feature", "Value", "z", "Robust z"],
                rows=[
                    [
                        str(a.day),
                        a.asset,
                        a.label,
                        a.value,
                        render.z_text(a.z),
                        render.z_text(a.robust_z),
                    ]
                    for a in brief.anomalies[:30]
                ],
            )
        )
    else:
        b.append(
            Block("p", "No feature was 3 or more standard deviations from its recent history.")
        )

    b.append(Block("h2", "Alerts"))
    if brief.alerts:
        for a in brief.alerts:
            b.append(Block("p", f"{a.rule} on {a.asset} ({a.day}): {a.description}"))
            b.append(
                Block(
                    "table",
                    headers=["Feature", "Value", "Condition", "Held"],
                    rows=[
                        [
                            e["feature"],
                            "missing" if e["value"] is None else f"{e['value']:.6g}",
                            e["condition"],
                            "yes" if e["held"] else "no",
                        ]
                        for e in a.evidence
                    ],
                )
            )
        b.append(Block("note", DISCLAIMER))
    else:
        b.append(Block("p", "No rule fired on the latest day."))

    if brief.relationships is not None:
        b += _relationship_blocks(brief.relationships)
    if brief.onchain is not None and not brief.onchain.empty:
        b += _onchain_blocks(brief.onchain)

    b.append(Block("h2", "Sources and attribution"))
    lines = [ATTRIBUTIONS[s] for s in brief.sources_used if s in ATTRIBUTIONS]
    lines += [
        f"Headlines: {name} RSS feed; each headline links to the publisher."
        for name in brief.rss_attributions
    ]
    b.append(Block("links", links=[(line, None) for line in lines]))
    b.append(Block("note", PERSONAL_USE))
    b.append(Block("p", f"Made with sigstar {__version__} ({PROJECT_URL}). {DISCLAIMER}"))
    return b


def _explanation_blocks(e: Explanation) -> list[Block]:
    blocks = [
        Block(
            "table",
            headers=["What coincided", "When", "Size", "Value", "Typical (30d)"],
            rows=_driver_rows(e),
        )
    ]
    evidence = [(f"{h['title']} ({h['source']})", h["url"]) for d in e.drivers for h in d.evidence]
    if evidence:
        blocks.append(Block("p", "Headlines behind the news readings:"))
        blocks.append(Block("links", links=evidence))
    if e.headlines:
        blocks.append(Block("p", f"{e.move.asset} headlines that day (context, not ranked):"))
        blocks.append(
            Block("links", links=[(f"{h['title']} ({h['source']})", h["url"]) for h in e.headlines])
        )
    if e.co_movements:
        blocks.append(
            Block(
                "p",
                "Same day elsewhere: "
                + "; ".join(
                    f"{c['asset']} {_pct(c['return_pct'])} ({render.z_text(c['z'])})"
                    for c in e.co_movements
                )
                + ".",
            )
        )
    blocks += [Block("note", n) for n in e.notes]
    blocks.append(Block("note", CAUSATION))
    return blocks


def _relationship_blocks(r: Relationships) -> list[Block]:
    corr = r.correlations_full.round(2)
    blocks = [
        Block("h2", "Relationships"),
        Block(
            "table",
            headers=["Daily return correlation", *[str(c) for c in corr.columns]],
            rows=[
                [str(i), *[("n/a" if pd.isna(v) else f"{v:.2f}") for v in corr.loc[i]]]
                for i in corr.index
            ],
        ),
    ]
    if r.betas:
        blocks.append(
            Block(
                "table",
                headers=["Asset", "Beta vs", "Full window", "Last 30 days"],
                rows=[
                    [
                        x["asset"],
                        x["benchmark"],
                        render._num(x.get("full")),
                        render._num(x.get("30d")),
                    ]
                    for x in r.betas
                ],
            )
        )
    if r.regimes:
        blocks.append(
            Block(
                "table",
                headers=[
                    "Asset",
                    "Volatility regime now",
                    "High-vol level",
                    "Low-vol level",
                    "Share of days high",
                    "Method",
                ],
                rows=[
                    [
                        g.asset,
                        g.current,
                        f"{g.high_vol_ann * 100:.0f}%",
                        f"{g.low_vol_ann * 100:.0f}%",
                        f"{g.high_vol_share * 100:.0f}%",
                        g.method,
                    ]
                    for g in r.regimes
                ],
            )
        )
    if r.granger:
        top = sorted(r.granger, key=lambda g: g.min_p)[:8]
        blocks.append(
            Block(
                "table",
                headers=["Asset", "Does this lead...", "...this?", "Lag (days)", "p", "Adjusted p"],
                rows=[
                    [
                        g.asset,
                        g.label,
                        g.target.replace("_", " "),
                        str(g.best_lag),
                        f"{g.min_p:.3f}",
                        f"{g.p_bonferroni:.3f}",
                    ]
                    for g in top
                ],
            )
        )
        blocks.append(
            Block(
                "note",
                "Granger tests ask whether a feature's past values helped predict returns in "
                "this sample beyond the returns' own past. Many pairs are tested, so the "
                "adjusted p (Bonferroni) is the one to read; even a low one is not evidence "
                "of cause, and short windows make all of these fragile.",
            )
        )
    blocks += [Block("note", n) for n in r.notes]
    return blocks


ONCHAIN_METRICS = {
    "tx_count": "Transactions",
    "active_wallets": "Fee payers",
    "fees_native": "Fees (native coin)",
    "stablecoin_transfer_amount": "Stablecoin transferred",
    "coverage_seconds": "First to last block (s)",
}


def _onchain_blocks(onchain: pd.DataFrame) -> list[Block]:
    """The chainstar slice as it is: per chain and day, with how much of the day it covers."""
    chain_rows = onchain[onchain["token_address"] == ""]
    wide = chain_rows.pivot_table(
        index=["day", "chain"], columns="metric", values="value", aggfunc="sum"
    ).reset_index()
    rows = []
    for _, r in wide.sort_values(["day", "chain"]).iterrows():
        cells = [pd.Timestamp(r["day"]).date().isoformat(), str(r["chain"])]
        for metric in ONCHAIN_METRICS:
            v = r.get(metric)
            if v is None or pd.isna(v):
                cells.append("n/a")
            elif metric == "fees_native":
                cells.append(f"{v:,.4f}")
            else:
                cells.append(f"{v:,.0f}")
        rows.append(cells)
    return [
        Block("h2", "On-chain activity (chainstar)"),
        Block("table", headers=["Day", "Chain", *ONCHAIN_METRICS.values()], rows=rows),
        Block(
            "note",
            "From the attached chainstar warehouse. 'First to last block' is the time between "
            "the first and last block in the slice: a slice of seconds or minutes is a sample, "
            "not the day's total, and on-chain z-scores need at least 10 earlier days, so "
            "these numbers do not enter the explanations until more days are collected. "
            "Stablecoins are counted by contract address, never by symbol.",
        ),
    ]


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def to_markdown(blocks: list[Block], chart_dir: str) -> tuple[str, dict[str, str]]:
    out: list[str] = []
    charts: dict[str, str] = {}
    for blk in blocks:
        if blk.kind in ("h1", "h2", "h3"):
            out.append(f"{'#' * int(blk.kind[1])} {blk.text}\n")
        elif blk.kind == "p":
            out.append(f"{blk.text}\n")
        elif blk.kind == "note":
            out.append(f"> {blk.text}\n")
        elif blk.kind == "table":
            out.append("| " + " | ".join(_md_cell(h) for h in blk.headers) + " |")
            out.append("|" + "---|" * len(blk.headers))
            out += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in blk.rows]
            out.append("")
        elif blk.kind == "links":
            for text, url in blk.links:
                out.append(f"- [{text.replace(']', ')')}]({url})" if url else f"- {text}")
            out.append("")
        elif blk.kind == "chart" and blk.svg:
            charts[blk.name] = blk.svg
            out.append(f"![{blk.name}]({chart_dir}/{blk.name}.svg)\n")
    return "\n".join(out), charts


CSS = """
:root { color-scheme: light; --surface-1: #fcfcfb; --page: #f6f5f2; --text-primary: #0b0b0b;
  --text-secondary: #52514e; --grid: #e4e3df; --series-1: #2a78d6; --series-2: #eb6834;
  --note: #efeee9; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark;
  --surface-1: #1a1a19; --page: #121211; --text-primary: #f0efec; --text-secondary: #c3c2b7;
  --grid: #2e2e2c; --series-1: #3987e5; --series-2: #d95926; --note: #24241f; } }
:root[data-theme="dark"] { color-scheme: dark; --surface-1: #1a1a19; --page: #121211;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --grid: #2e2e2c; --series-1: #3987e5;
  --series-2: #d95926; --note: #24241f; }
body { margin: 0; background: var(--page); color: var(--text-primary);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 1.6rem; } h2 { margin-top: 2rem; border-bottom: 1px solid var(--grid); }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0 1rem; font-size: 0.9rem;
  display: block; overflow-x: auto; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--grid); }
th { color: var(--text-secondary); font-weight: 600; }
.note { background: var(--note); padding: 8px 12px; border-radius: 6px;
  color: var(--text-secondary); }
.chart { width: 100%; height: auto; background: var(--surface-1); border-radius: 6px; }
a { color: var(--series-1); }
"""


def to_html(blocks: list[Block], title: str) -> str:
    body: list[str] = []
    for blk in blocks:
        if blk.kind in ("h1", "h2", "h3"):
            body.append(f"<{blk.kind}>{escape(blk.text)}</{blk.kind}>")
        elif blk.kind == "p":
            body.append(f"<p>{escape(blk.text)}</p>")
        elif blk.kind == "note":
            body.append(f'<p class="note">{escape(blk.text)}</p>')
        elif blk.kind == "table":
            head = "".join(f"<th>{escape(h)}</th>" for h in blk.headers)
            rows = "".join(
                "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>" for row in blk.rows
            )
            body.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
        elif blk.kind == "links":
            items = "".join(
                f'<li><a href="{escape(url)}" rel="noopener noreferrer">{escape(text)}</a></li>'
                if url
                else f"<li>{escape(text)}</li>"
                for text, url in blk.links
            )
            body.append(f"<ul>{items}</ul>")
        elif blk.kind == "chart" and blk.svg:
            body.append(blk.svg)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{''.join(body)}</main></body></html>\n"
    )


def write(brief: Brief, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"sigstar-brief-{brief.day.isoformat()}"
    blocks = build_blocks(brief)
    chart_dir = f"{stem}-charts"
    markdown, charts = to_markdown(blocks, chart_dir)
    (out_dir / chart_dir).mkdir(exist_ok=True)
    for name, svg in charts.items():
        (out_dir / chart_dir / f"{name}.svg").write_text(svg)
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    md_path.write_text(markdown)
    html_path.write_text(to_html(blocks, f"sigstar brief {brief.day.isoformat()}"))
    return md_path, html_path


def window(df: pd.DataFrame, days: int) -> tuple[date, date]:
    end = max(df.loc[df["ret_1d"].notna(), "day"])
    return end - timedelta(days=days - 1), end
