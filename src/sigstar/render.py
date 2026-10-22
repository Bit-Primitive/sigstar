"""Plain-text renderings for the terminal (the report has its own Markdown/HTML)."""

from __future__ import annotations

import math

from sigstar.alerts import Alert
from sigstar.analysis.anomalies import Anomaly, NotCompared
from sigstar.analysis.explain import Explanation
from sigstar.analysis.forecast import Forecast
from sigstar.analysis.relationships import Relationships
from sigstar.disclaimer import CAUSATION, DISCLAIMER

TARGET_UNITS = {"price": "USD", "volatility": "annualised", "volume": "USD/day"}


def money(v: float) -> str:
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.2f}bn"
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.2f}m"
    if abs(v) >= 100:
        return f"{v:,.2f}"
    return f"{v:,.4f}"


def target_value(target: str, v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v * 100:.1f}%" if target == "volatility" else money(v)


def z_text(z: float | None) -> str:
    return "n/a" if z is None else f"{z:+.1f}σ"


def explanation(e: Explanation) -> str:
    m = e.move
    lines = [
        f"{m.asset} on {m.day}: {m.return_pct * 100:+.2f}% "
        f"(close {money(m.close)} on {m.venue}, previous {money(m.prev_close or math.nan)})",
        *([f"  price: {m.price_source}"] if m.price_source else []),
        f"  size: {z_text(m.z)} against the previous 30 days; "
        f"#{m.rank_in_window} largest of the last {m.window_days} days",
        "",
        f"What coincided (top {len(e.drivers)} of {e.checked} checked, ranked by |z|):",
    ]
    if not e.drivers:
        lines.append("  nothing to compare yet (z-scores need 10 earlier days)")
    for i, d in enumerate(e.drivers, 1):
        flag = "unusual" if d.unusual else "normal range"
        lines.append(
            f"  {i}. {d.label} [{d.timing}]: {z_text(d.z)} ({flag}); value {d.value}, {d.typical}"
        )
        for h in d.evidence:
            lines.append(f"       - {h['title']} ({h['source']}) {h['url']}")
    if e.headlines:
        lines.append("")
        lines.append(f"{m.asset} headlines that day (context, not ranked):")
        for h in e.headlines:
            lines.append(f"  - {h['title']} ({h['source']}) {h['url']}")
    if e.co_movements:
        lines.append("")
        lines.append("Other assets the same day:")
        for c in e.co_movements:
            lines.append(
                f"  {c['asset']}: {c['return_pct'] * 100:+.2f}% ({z_text(c['z'])}), "
                f"{'same' if c['same_direction'] else 'opposite'} direction"
            )
    for note in e.notes:
        lines.append(f"note: {note}")
    lines += ["", CAUSATION, DISCLAIMER]
    return "\n".join(lines)


def forecast(f: Forecast) -> str:
    unit = TARGET_UNITS[f.target]
    lines = [
        f"{f.asset} {f.target} ({unit}), last {f.last_day}: {target_value(f.target, f.last_value)}",
        f"  backtest: {f.scores[0].origins} rolling origins, horizons 1-{f.horizon} days",
        f"  {'model':<6} {'MAE':>12} {'MAPE':>8} {'vs naive':>9}",
    ]
    for s in f.scores:
        lines.append(
            f"  {s.model:<6} {target_value(f.target, s.mae):>12} {s.mape * 100:>7.2f}% "
            f"{s.vs_naive:>9.2f}"
        )
    best = next(s for s in f.scores if s.model == f.best_model)
    verdict = (
        f"{f.best_model} beats naive (MAE {best.vs_naive:.2f}x naive's)"
        if f.beats_naive
        else "no model beats naive by 2% or more: the naive forecast (no change) is shown"
    )
    lines.append(f"  {verdict}")
    for i, day in enumerate(f.days):
        band = ""
        if f.lower and f.upper:
            band = (
                f"  (80% interval {target_value(f.target, f.lower[i])} to "
                f"{target_value(f.target, f.upper[i])})"
            )
        lines.append(f"  {day}: {target_value(f.target, f.point[i])}{band}")
    return "\n".join(lines)


def alert(a: Alert) -> str:
    lines = [f"ALERT {a.rule} on {a.asset} for {a.day}: {a.description}"]
    for e in a.evidence:
        value = "missing" if e["value"] is None else f"{e['value']:.6g}"
        lines.append(
            f"  {e['feature']} = {value}, {e['condition']}: {'yes' if e['held'] else 'no'}"
        )
    lines.append(f"  {a.disclaimer}")
    return "\n".join(lines)


def anomalies(items: list[Anomaly], skipped: list[NotCompared] | None = None) -> str:
    lines = [] if items else ["no anomalies in this window"]
    if items:
        lines.append(f"{'day':<10} {'asset':<5} {'z':>7} {'robust':>7}  feature (value)")
    for a in items:
        lines.append(
            f"{a.day!s:<10} {a.asset:<5} {z_text(a.z):>7} {z_text(a.robust_z):>7}  "
            f"{a.label} ({a.value})"
        )
    if skipped:
        lines.append("")
        lines.append("Not checked, because on-chain coverage was not comparable:")
        for n in skipped:
            lines.append(f"  {n.day} {', '.join(n.assets)}: {n.label}, {n.note}")
    return "\n".join(lines)


def relationships(r: Relationships) -> str:
    lines = ["Return correlations, full window:", r.correlations_full.round(2).to_string(), ""]
    lines += ["Return correlations, last 30 days:", r.correlations_30d.round(2).to_string(), ""]
    for b in r.betas:
        lines.append(
            f"beta of {b['asset']} on {b['benchmark']}: full {_num(b.get('full'))}, "
            f"30d {_num(b.get('30d'))}"
        )
    lines.append("")
    lines.append("Volatility regimes:")
    for g in r.regimes:
        prob = "" if g.prob_high_vol is None else f", P(high) {g.prob_high_vol:.2f}"
        lines.append(
            f"  {g.asset}: now {g.current}{prob}; high {g.high_vol_ann * 100:.0f}% vs low "
            f"{g.low_vol_ann * 100:.0f}% annualised; {g.high_vol_share * 100:.0f}% of days high "
            f"({g.method})"
        )
    lines.append("")
    lines.append("Lead/lag (strongest correlation per pair, lags -3..+3 days):")
    for ll in sorted(r.lead_lag, key=lambda x: -abs(x.best_corr))[:10]:
        lines.append(
            f"  {ll.asset} {ll.label} vs {ll.target.replace('_', ' ')}: corr {ll.best_corr:+.2f}, "
            f"{lag_text(ll.best_lag)} (n={ll.n})"
        )
    lines.append("")
    lines.append("Granger tests (does the feature's past add to the return's own past?):")
    for g in sorted(r.granger, key=lambda x: x.min_p)[:10]:
        lines.append(
            f"  {g.asset} {g.label} -> {g.target}: p={g.min_p:.3f} at lag {g.best_lag}, "
            f"Bonferroni-adjusted p={g.p_bonferroni:.3f} (n={g.n})"
        )
    lines.append(
        "  A low p-value says past values helped predict in this sample; it is not evidence "
        "of cause, and the adjusted p accounts for the many tests run."
    )
    lines += [f"note: {n}" for n in r.notes]
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def lag_text(lag: int) -> str:
    if lag == 0:
        return "same day"
    days = f"{abs(lag)} day{'s' if abs(lag) > 1 else ''}"
    return f"feature leads by {days}" if lag > 0 else f"feature follows by {days}"


def _num(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.2f}"
