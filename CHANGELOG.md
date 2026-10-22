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
- On-chain prices and DEX activity from chainstar: chainstar's USD prices from DEX swaps
  (`fct_token_prices`, hourly and daily, high and medium confidence only, closed buckets
  only) for each asset's `onchain.prices` tokens, matched by chain and address (ETH from
  WETH on Ethereum and Base, SOL from SOL and wrapped SOL, BTC from wrapped BTC via
  `cross_chain_wrapped_btc`), in a new mart `fct_onchain_prices` and in `fct_prices` as the
  venue `onchain`, with chain, method, confidence and observation count carried through.
  The on-chain price is the reference close when no exchange has the day. New mart
  `fct_onchain_dex_daily` from `fct_swaps` (verified pools only on Ethereum and Base).
- Features `onchain_dex_volume_usd`/`_z`, `onchain_swap_count`/`_z`,
  `onchain_price_gap_bps`/`_z` (on-chain vs exchange price, hour by hour), and the day's
  on-chain price with its chain, method, confidence and observations. The z-scored ones are
  drivers in `explain`, scanned by `anomalies` and usable in alert rules; `explain` names the
  source of an on-chain reference price and how much of the day a chainstar sample covers.
- Licence modes: `sources: licence_mode: personal | commercial`. Commercial mode switches
  off every source whose terms restrict commercial use or do not clearly allow it (Coinbase,
  Kraken, Binance spot and futures, OKX, RSS feeds; FRED series other than the public-domain
  DFF, DGS10 and DTWEXBGS), in collection, in `doctor` and in the dbt staging models (rows
  collected earlier are dropped too), and runs on Fear & Greed, GDELT, FRED and chainstar.
  `doctor`, the brief and every command that prints analysis state the mode and the sources
  the data came from, read from the warehouse. SOURCES.md records each source's terms as
  re-checked on 2026-10-22.
- On-chain z-scores compare like with like: a day is compared only with earlier days on
  which every chain behind the number was loaded, with a block count within 10%
  (`analysis: onchain_coverage_tolerance`), summed over the same chains. Otherwise the
  z-score is empty, the feature is not ranked, and `<column>_note` (repeated by `explain`,
  `anomalies` and the brief) says which chain was missing or loaded differently. Applies to
  every on-chain feature, the existing ones included, and to `range_z` on days priced
  on-chain; `anomalies` uses no robust z for them. New metric `blocks` in
  `fct_onchain_daily`.
- Alert rules: a `pNN` percentile threshold on an on-chain feature is taken only over
  earlier days of comparable chain coverage (the same rule and tolerance as the on-chain
  z-scores). With fewer than 20 such days, or on a column that measures the sample itself
  (coverage seconds, observation and hour counts), the condition is not evaluated and never
  holds; the skip and its reason are printed, logged once to `alerts-skipped.jsonl` and
  listed in the brief.
- A chainstar warehouse built before chainstar read swaps still works for on-chain
  activity; `doctor` says it has no prices, and commercial mode refuses to build without
  them.

### Changed

- The Fear & Greed feature is labelled "Fear & Greed Index change (alternative.me)", since
  its terms ask for attribution right next to the data.
- Reports credit the sources whose data is in the warehouse, rather than those switched on
  in `sigstar.yml`.
- On-chain z-scores (transactions, active wallets, stablecoin transfers) no longer compare
  days of different chain coverage: a warehouse that held a chain on some days only could
  make a total look like a many-sigma outlier.
