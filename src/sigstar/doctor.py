"""sigstar doctor: keys, connectivity, robots.txt and terms notes, one line per check.

Each enabled source gets one small request to a cheap endpoint through the same polite
client the collectors use, so robots.txt and pacing apply here too. Keys are reported as
set or not set, never shown.
"""

from __future__ import annotations

from dataclasses import dataclass

from sigstar import licences
from sigstar.build import chainstar_has_prices, check_chainstar
from sigstar.config import Config, fred_api_key, webhook_url
from sigstar.errors import SigstarError
from sigstar.http import PoliteClient

# source: (cheap URL, params, short terms note from SOURCES.md)
PROBES: dict[str, tuple[str, dict, str]] = {
    "coinbase": (
        "https://api.exchange.coinbase.com/time",
        {},
        "personal/research use; do not redistribute data or charts made from it",
    ),
    "kraken": (
        "https://api.kraken.com/0/public/Time",
        {},
        "personal use; commercial use needs Kraken's permission",
    ),
    "binance": (
        "https://data-api.binance.vision/api/v3/ping",
        {},
        "non-commercial personal use; not available in some countries",
    ),
    "binance_futures": (
        "https://fapi.binance.com/fapi/v1/ping",
        {},
        "same terms as Binance spot; open interest history is 30 days",
    ),
    "okx": (
        "https://www.okx.com/api/v5/public/time",
        {},
        "API Agreement 9.4: personal non-commercial trading use only",
    ),
    "fear_greed": (
        "https://api.alternative.me/fng/",
        {"limit": 1},
        "free; credit alternative.me next to the data",
    ),
    "gdelt": (
        "https://api.gdeltproject.org/api/v2/doc/doc",
        {"query": "bitcoin", "mode": "artlist", "maxrecords": 1, "format": "json"},
        "open data; cite The GDELT Project; at most one request per 5 s",
    ),
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_checks(cfg: Config, client: PoliteClient) -> list[Check]:
    checks = [
        Check(
            "config",
            True,
            str(cfg.path) if cfg.path else "no sigstar.yml; using built-in defaults",
        ),
        Check(
            "warehouse",
            True,
            f"{cfg.warehouse} ({'exists' if cfg.warehouse.exists() else 'not yet created'})",
        ),
    ]
    commercial = cfg.licence_mode == licences.COMMERCIAL
    checks.append(
        Check(
            "licence mode",
            True,
            "commercial: only sources whose terms allow commercial use, plus chainstar"
            if commercial
            else "personal: every enabled source, for your own use",
        )
    )
    for name, (url, params, note) in PROBES.items():
        if not licences.allows(cfg.licence_mode, name):
            checks.append(
                Check(name, True, f"off in commercial mode ({licences.TERMS[name].note})")
            )
            continue
        if not cfg.source_enabled(name):
            checks.append(Check(name, True, f"disabled in sigstar.yml ({note})"))
            continue
        try:
            client.get_text(url, params)
            checks.append(Check(name, True, f"reachable; {note}"))
        except SigstarError as exc:
            checks.append(Check(name, False, f"{exc} (hint: {exc.hint})"))
    if not licences.allows(cfg.licence_mode, "rss"):
        checks.append(Check("rss", True, f"off in commercial mode ({licences.TERMS['rss'].note})"))
    if cfg.source_enabled("rss"):
        for feed in cfg.sources.get("rss", {}).get("feeds") or []:
            try:
                client.get_text(feed["url"], cache_ttl=900)
                checks.append(Check(f"rss:{feed['name']}", True, "reachable; robots.txt allows it"))
            except SigstarError as exc:
                checks.append(Check(f"rss:{feed['name']}", False, str(exc)))
    if cfg.source_enabled("fred"):
        key = fred_api_key()
        detail = (
            "SIGSTAR_FRED_API_KEY is set"
            if key
            else "SIGSTAR_FRED_API_KEY not set: macro data will be skipped (optional)"
        )
        if commercial:
            kept = ", ".join(cfg.fred_series()) or "none"
            detail += f"; commercial mode keeps the public-domain series: {kept}"
        checks.append(Check("fred", True, detail))
    checks.append(
        Check(
            "webhook",
            True,
            "SIGSTAR_ALERT_WEBHOOK_URL is set" if webhook_url() else "no webhook (optional)",
        )
    )
    if cfg.chainstar_warehouse is None:
        checks.append(
            Check(
                "chainstar",
                not commercial,
                "not configured: commercial mode has no price source without it"
                if commercial
                else "not configured (optional on-chain angle)",
            )
        )
    else:
        try:
            check_chainstar(cfg.chainstar_warehouse)
            prices = chainstar_has_prices(cfg.chainstar_warehouse)
            detail = (
                "with swaps and on-chain USD prices"
                if prices
                else "without swaps or USD prices (built before chainstar read swaps; "
                "rebuild with `chainstar sync --full-refresh` for on-chain prices)"
            )
            checks.append(
                Check(
                    "chainstar",
                    prices or not commercial,
                    f"{cfg.chainstar_warehouse} has chainstar tables, {detail}; "
                    f"{licences.TERMS['chainstar'].note}",
                )
            )
        except SigstarError as exc:
            checks.append(Check("chainstar", False, str(exc)))
    try:
        import dbt.version

        checks.append(Check("dbt", True, f"dbt-core {dbt.version.__version__}"))
    except ImportError:
        checks.append(Check("dbt", False, "dbt-core is not installed"))
    return checks
