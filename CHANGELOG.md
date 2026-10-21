# Changelog

All notable changes to sigstar are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `sigstar collect`: incremental collection with dlt into DuckDB (schema `sigstar_raw`),
  one cursor per stream, merge loads, one failing stream never stops the others.
  Sources: Coinbase Exchange, Kraken and Binance (data-api.binance.vision) daily and hourly
  candles; Binance USDⓈ-M funding and open interest; OKX funding and open interest (off
  by default, see SOURCES.md); Crypto Fear & Greed Index (alternative.me); GDELT DOC 2.0
  coverage volume, tone and headline lists; Bitcoin Magazine RSS headlines; FRED macro
  series (optional, needs `SIGSTAR_FRED_API_KEY`).
- A shared polite HTTP client: identifying User-Agent with the project URL, per-host
  pacing at or below each published limit, robots.txt with RFC 9309 semantics (including
  Crawl-delay), retries with backoff honouring Retry-After, an on-disk cache, and
  credentials redacted from every message. A host that keeps refusing through every retry
  is left alone for the rest of the run.
- Scarce sources: streams never read go first, and GDELT timelines are not re-read within
  6 hours, so GDELT's few accepted requests go to missing data.
- `sigstar build`: a bundled dbt project (dbt-duckdb) with staging views and marts
  `fct_prices`, `fct_derivatives`, `fct_news`, `bridge_news_tags`, `news_rules`,
  `agg_news_daily`, `fct_sentiment`, `fct_macro`, `fct_onchain_daily` and
  `features_daily`, with tests on grain, nulls and accepted ranges. Features include
  intraday volatility from hourly candles; GDELT outage days are blanked, not counted as
  zero coverage.
- On-chain angle from a chainstar warehouse, attached read-only: daily transactions,
  fees, fee payers, native transfers and stablecoin transfers matched by contract address.
- `sigstar explain`: a move and the features that were unusual the same day and the day
  before, ranked by z-score, with headlines as evidence, the asset's headlines that day as
  context, and same-day moves of other assets.
  `--top N` explains the biggest moves in a window.
- `sigstar relationships`: return correlations, betas, lead/lag correlations, Granger
  tests with Bonferroni-adjusted p-values, and Markov-switching volatility regimes.
- `sigstar anomalies`: z-score and robust (median/MAD) outliers across all features.
- `sigstar forecast`: naive, ETS and ARIMA forecasts for price, volatility and volume,
  each with a rolling-origin backtest (MAE, MAPE, ratio to naive); naive is kept unless a
  model beats it by at least 2%.
- `sigstar alerts`: YAML rules with numeric or percentile thresholds, logged to JSONL and
  CSV once per rule, asset and day, and POSTed to `SIGSTAR_ALERT_WEBHOOK_URL` if set.
- `sigstar report`: a daily brief in Markdown and self-contained HTML with inline SVG
  charts and source attribution.
- `sigstar init`, `sigstar run` (for cron or systemd), `sigstar doctor`.
- No telemetry: dlt's and dbt's anonymous usage statistics are switched off.
