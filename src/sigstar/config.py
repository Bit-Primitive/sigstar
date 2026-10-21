"""sigstar.yml: which assets, which sources, where the warehouse is.

The template below is both what `sigstar init` writes and the defaults used when there is
no config file, so the documented defaults and the real ones cannot drift apart. API keys
and webhook URLs are never read from this file: only from environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from sigstar.errors import SigstarError

CONFIG_FILENAME = "sigstar.yml"
FRED_KEY_ENV = "SIGSTAR_FRED_API_KEY"
WEBHOOK_ENV = "SIGSTAR_ALERT_WEBHOOK_URL"
CHAINSTAR_ENV = "SIGSTAR_CHAINSTAR_WAREHOUSE"

CONFIG_TEMPLATE = """\
# sigstar configuration. Edit freely; every key is optional.
# API keys and webhook URLs never go in this file. sigstar reads them from environment
# variables only: SIGSTAR_FRED_API_KEY (FRED macro data), SIGSTAR_ALERT_WEBHOOK_URL (alerts).

# DuckDB file that holds everything sigstar collects and builds.
warehouse: warehouse.duckdb

# How much history the first collection fetches. Later runs only fetch what is new.
history:
  daily_days: 90
  hourly_days: 7

# Assets to follow. Each venue key is that venue's own market name; leave one out to skip
# that venue for the asset. news_keywords are whole-word, case-insensitive matches against
# headlines and feed snippets. chains are chainstar chains whose activity counts for it.
assets:
  BTC:
    name: Bitcoin
    coinbase: BTC-USD
    kraken: XBTUSD
    binance: BTCUSDT
    binance_futures: BTCUSDT
    okx_swap: BTC-USDT-SWAP
    gdelt_query: bitcoin
    news_keywords: [bitcoin, btc]
    chains: [bitcoin]
  ETH:
    name: Ethereum
    coinbase: ETH-USD
    kraken: ETHUSD
    binance: ETHUSDT
    binance_futures: ETHUSDT
    okx_swap: ETH-USDT-SWAP
    gdelt_query: ethereum
    news_keywords: [ethereum, ether, eth]
    chains: [ethereum, base]
  SOL:
    name: Solana
    coinbase: SOL-USD
    kraken: SOLUSD
    binance: SOLUSDT
    binance_futures: SOLUSDT
    okx_swap: SOL-USDT-SWAP
    gdelt_query: solana
    news_keywords: [solana]
    chains: [solana]

# News topics that are not a single asset. Same keyword rules as above.
topics:
  sec:
    gdelt_query: SEC crypto
    news_keywords: [sec, securities and exchange commission]
  stablecoin:
    gdelt_query: stablecoin
    news_keywords: [stablecoin, stablecoins, usdt, usdc, tether]

# Sources. Set enabled: false to skip one. See SOURCES.md for each one's terms.
sources:
  coinbase: {enabled: true}
  kraken: {enabled: true}
  binance: {enabled: true}          # spot klines from data-api.binance.vision
  binance_futures: {enabled: true}  # funding and open interest (30 days of OI history)
  # OKX is off by default: its API Agreement (section 9.4) limits public market data to
  # personal, non-commercial trading use and forbids using it in an "analytics platform".
  # Turn it on only if you have read those terms and your use fits them (SOURCES.md).
  okx: {enabled: false}
  fear_greed: {enabled: true}
  gdelt:
    enabled: true
    # Headline lists: this many recent days on the first run, then each finished day
    # once. GDELT allows few requests (one per 5 s at most), so several days share one
    # request of up to 250 headlines, in GDELT's relevance order.
    article_days: 14
    article_chunk_days: 7
    articles_per_request: 250
  rss:
    enabled: true
    feeds:
{rss_feeds}
  fred:
    enabled: true                   # skipped with a notice when SIGSTAR_FRED_API_KEY is unset
    series: [DFF, DGS10, DTWEXBGS]

# Optional chainstar warehouse for on-chain activity (a DuckDB file built by chainstar).
# The SIGSTAR_CHAINSTAR_WAREHOUSE environment variable overrides it.
chainstar:
  warehouse: null
  # Stablecoins are matched by contract address (mint on Solana), never by symbol, because
  # anyone can deploy a token called USDC. Addresses checked against Circle's and Tether's
  # published lists (DAI: Etherscan) in October 2026.
  stablecoins:
    - {chain: ethereum, address: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", symbol: USDC}
    - {chain: ethereum, address: "0xdac17f958d2ee523a2206206994597c13d831ec7", symbol: USDT}
    - {chain: ethereum, address: "0x6b175474e89094c44da98b954eedeac495271d0f", symbol: DAI}
    - {chain: base, address: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", symbol: USDC}
    - {chain: solana, address: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v, symbol: USDC}
    - {chain: solana, address: Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB, symbol: USDT}

analysis:
  zscore_window_days: 30     # trailing window for z-scores (today is never in its own window)
  anomaly_z: 3.0             # |z| at or above this is an anomaly
  driver_z: 2.0              # |z| at or above this counts as unusual in explanations
  forecast_horizon_days: 7
  backtest_origins: 20       # rolling origins for forecast error

alerts:
  rules_file: sigstar-rules.yml
  log_file: alerts.jsonl     # one JSON object per alert; a .csv twin is written alongside
  # Webhook URL: set SIGSTAR_ALERT_WEBHOOK_URL. It is never stored in this file.

report:
  output_dir: reports
"""


def render_template() -> str:
    from sigstar.sources.rss import DEFAULT_FEEDS

    feeds = DEFAULT_FEEDS
    lines = [
        f'      - {{name: {f["name"]}, url: "{f["url"]}", attribution: "{f["attribution"]}"}}'
        for f in feeds
    ]
    return CONFIG_TEMPLATE.replace("{rss_feeds}", "\n".join(lines) if lines else "      []")


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    venues: dict[str, str]
    gdelt_query: str | None
    news_keywords: tuple[str, ...]
    chains: tuple[str, ...]


@dataclass(frozen=True)
class Topic:
    key: str
    gdelt_query: str | None
    news_keywords: tuple[str, ...]


@dataclass(frozen=True)
class Stablecoin:
    chain: str
    address: str
    symbol: str


VENUE_KEYS = ("coinbase", "kraken", "binance", "binance_futures", "okx_swap")


@dataclass
class Config:
    path: Path | None
    base_dir: Path
    warehouse: Path
    daily_days: int
    hourly_days: int
    assets: dict[str, Asset]
    topics: dict[str, Topic]
    sources: dict[str, dict[str, Any]]
    chainstar_warehouse: Path | None
    stablecoins: tuple[Stablecoin, ...]
    analysis: dict[str, float]
    alerts: dict[str, Any]
    report_dir: Path
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def state_dir(self) -> Path:
        """dlt state, dbt artefacts and the HTTP cache live here, next to the config."""
        return self.base_dir / ".sigstar"

    def source_enabled(self, name: str) -> bool:
        return bool(self.sources.get(name, {}).get("enabled", False))

    def resolve(self, value: str | os.PathLike[str]) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.base_dir / path


def _defaults() -> dict[str, Any]:
    return yaml.safe_load(render_template())


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge dicts, except `assets` and `topics`, which replace the defaults whole:
    listing your own assets should mean exactly those assets."""
    merged = dict(base)
    for key, value in override.items():
        if key in ("assets", "topics") or not isinstance(value, dict):
            merged[key] = value
        elif isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _keywords(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise SigstarError(
            f"{where}.news_keywords must be a list of words", "for example: [bitcoin, btc]"
        )
    return tuple(v.strip().lower() for v in value)


def _parse_assets(raw: Any) -> dict[str, Asset]:
    if not isinstance(raw, dict) or not raw:
        raise SigstarError(
            "assets: in sigstar.yml must list at least one asset",
            "run `sigstar init` for an example",
        )
    assets = {}
    for symbol, spec in raw.items():
        spec = spec or {}
        sym = str(symbol).upper()
        assets[sym] = Asset(
            symbol=sym,
            name=str(spec.get("name", sym)),
            venues={k: str(spec[k]) for k in VENUE_KEYS if spec.get(k)},
            gdelt_query=spec.get("gdelt_query"),
            news_keywords=_keywords(spec.get("news_keywords"), f"assets.{sym}"),
            chains=tuple(spec.get("chains") or ()),
        )
    return assets


def load_config(path: Path | None = None) -> Config:
    """Read sigstar.yml (or the given file) over the built-in defaults."""
    explicit = path is not None
    path = path or Path(CONFIG_FILENAME)
    user: dict[str, Any] = {}
    if path.exists():
        try:
            user = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise SigstarError(
                f"{path} is not valid YAML: {exc}", "fix the line it names, or rerun `sigstar init`"
            ) from exc
        if not isinstance(user, dict):
            raise SigstarError(f"{path} should be a mapping of settings", "run `sigstar init`")
    elif explicit:
        raise SigstarError(f"config file {path} does not exist", "run `sigstar init` to write one")

    raw = _merge(_defaults(), user)
    base_dir = path.resolve().parent
    chainstar_raw = os.environ.get(CHAINSTAR_ENV) or (raw.get("chainstar") or {}).get("warehouse")
    topics = {
        str(key): Topic(
            key=str(key),
            gdelt_query=(spec or {}).get("gdelt_query"),
            news_keywords=_keywords((spec or {}).get("news_keywords"), f"topics.{key}"),
        )
        for key, spec in (raw.get("topics") or {}).items()
    }
    cfg = Config(
        path=path if path.exists() else None,
        base_dir=base_dir,
        warehouse=Path(),
        daily_days=int(raw["history"]["daily_days"]),
        hourly_days=int(raw["history"]["hourly_days"]),
        assets=_parse_assets(raw.get("assets")),
        topics=topics,
        sources=raw.get("sources") or {},
        chainstar_warehouse=None,
        stablecoins=tuple(
            Stablecoin(chain=s["chain"], address=str(s["address"]), symbol=s.get("symbol", ""))
            for s in (raw.get("chainstar") or {}).get("stablecoins") or ()
        ),
        analysis={k: float(v) for k, v in (raw.get("analysis") or {}).items()},
        alerts=raw.get("alerts") or {},
        report_dir=Path(),
        raw=raw,
    )
    cfg.warehouse = cfg.resolve(raw.get("warehouse") or "warehouse.duckdb")
    cfg.report_dir = cfg.resolve((raw.get("report") or {}).get("output_dir") or "reports")
    if chainstar_raw:
        cfg.chainstar_warehouse = cfg.resolve(str(chainstar_raw))
    return cfg


def fred_api_key() -> str | None:
    return os.environ.get(FRED_KEY_ENV) or None


def webhook_url() -> str | None:
    return os.environ.get(WEBHOOK_ENV) or None
