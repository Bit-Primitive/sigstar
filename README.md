# sigstar

Collect market, on-chain, news and macro data, organise it, and explain, forecast and
alert on crypto price moves from every angle.

[Website](https://bitprimitive.com/projects/sigstar) ·
[Source](https://github.com/Bit-Primitive/sigstar) ·
[Sources and their terms](SOURCES.md)

sigstar gathers prices from several exchanges, perpetual-futures funding and open
interest, a sentiment index, news coverage and tone, macro series and (with
[chainstar](https://bitprimitive.com/projects/chainstar)) on-chain activity. It lines them
up day by day and answers: *what else was unusual when this coin moved?* It also measures
how the angles relate, flags outliers, makes baseline forecasts that show their own track
record, checks rules you write, and writes a daily brief. It runs on your computer, needs
no account, and sends nothing anywhere except requests to the public data sources.

> **Not financial advice.** sigstar shows analytics and the rules you define. It does not
> recommend buying, selling or holding anything, and things that happen together are not
> shown to cause each other.

## Start here: your first brief in 2 minutes

You need a terminal and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
uv tool install git+https://github.com/Bit-Primitive/sigstar
mkdir my-sigstar && cd my-sigstar
sigstar init        # writes sigstar.yml and sigstar-rules.yml (edit later if you like)
sigstar run         # collects, organises, analyses and writes the brief
```

(Once sigstar is on PyPI this becomes `uv tool install sigstar`.)

The first `run` fetches 90 days of daily data and 7 days of hourly data for BTC, ETH and
SOL. Most sources take seconds; news coverage from GDELT is slow on purpose (GDELT asks
for one request every few seconds), so the first run takes several minutes. Later runs only
fetch what is new.

Then open `reports/sigstar-brief-<date>.html` in your browser. It works offline.

Ask about one move:

```sh
sigstar explain BTC                    # the latest closed day
sigstar explain ETH --date 2026-10-14  # a given day
sigstar explain SOL --top 3            # the three biggest moves of the last 90 days
```

**Optional extras.** Macro data needs a free [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html):
`export SIGSTAR_FRED_API_KEY=...`. Alerts can be POSTed to a webhook:
`export SIGSTAR_ALERT_WEBHOOK_URL=...`. On-chain activity comes from a chainstar warehouse:
set `chainstar: warehouse: path/to/warehouse.duckdb` in `sigstar.yml`. Keys and URLs are
only ever read from the environment, never written to files or logs.

If something goes wrong, sigstar says what happened and what to try in two lines; add
`--debug` before the command for the full traceback. `sigstar doctor` checks keys (set or
not, never shown), that each source answers, and summarises its terms.

The rest of this page is the full reference.

## How it works

```
collect (dlt)         organise (dbt)                analyse (Python)          outputs
public APIs ──► sigstar_raw.* ──► sigstar_staging.* ──► sigstar.* marts ──► explain, relationships,
chainstar warehouse (read-only ATTACH) ──────────┘     features_daily      anomalies, forecasts,
                                                                            alerts ──► brief (MD + HTML)
```

- **collect**: [dlt](https://dlthub.com) pipelines load each source into DuckDB (schema
  `sigstar_raw`) with merge loads on each table's key. Every *stream* (one market on one
  venue at one interval, one news query, one series) keeps its own cursor in dlt's state,
  so runs are incremental, and adding an asset backfills only that asset. A stream that
  fails (a busy API, a venue not offered in your country) is reported and skipped; the
  others still load. Only closed candles are stored.
- **organise**: a bundled [dbt](https://www.getdbt.com) project (dbt-duckdb) builds staging
  views and marts, then runs its data tests. A source never collected yields an empty
  table, so the marts always build.
- **analyse**: pandas and [statsmodels](https://www.statsmodels.org) read the marts
  read-only.
- **polite HTTP**: one client for every source: identifying User-Agent, per-host pacing at
  or below each published limit, robots.txt obeyed (RFC 9309, with Crawl-delay), retries
  with backoff and Retry-After, an on-disk cache. Details per source in
  [SOURCES.md](SOURCES.md).

Files sigstar writes next to `sigstar.yml`: `warehouse.duckdb` (all data), `.sigstar/`
(dlt state, dbt profile and logs, HTTP cache), `reports/`, `alerts.jsonl` and
`alerts.csv`.

## Sources

| Angle | Source | Default |
| --- | --- | --- |
| Prices (OHLCV, daily + hourly) | Coinbase Exchange, Kraken, Binance (data-api.binance.vision) | on |
| Derivatives (funding, open interest) | Binance USDⓈ-M futures; OKX | Binance on, OKX off (its terms) |
| Sentiment | Crypto Fear & Greed Index (alternative.me) | on |
| News volume and tone, headlines | GDELT DOC 2.0 | on |
| Headlines with snippets | Bitcoin Magazine RSS | on |
| Macro | FRED: DFF, DGS10, DTWEXBGS | on when `SIGSTAR_FRED_API_KEY` is set |
| On-chain | chainstar warehouse (DuckDB) | when configured |

Several exchanges license their market data for personal use only: keep reports and data
to yourself. Which news feeds were left out and why, rate limits, and the attribution each
source asks for are in [SOURCES.md](SOURCES.md). Reports carry the required credits.

## Configuration

`sigstar init` writes a commented `sigstar.yml`; every key is optional and the defaults are
the ones in that file. The main parts:

- `warehouse`: the DuckDB file (default `warehouse.duckdb`).
- `history`: `daily_days` (90) and `hourly_days` (7) for the first collection.
- `assets`: per asset, each venue's own market name (`coinbase: BTC-USD`,
  `kraken: XBTUSD`, `binance: BTCUSDT`, `binance_futures: BTCUSDT`,
  `okx_swap: BTC-USDT-SWAP`), its GDELT query, its headline keywords and its chainstar
  chains. Leave a venue out to skip it. Listing `assets:` replaces the defaults.
- `topics`: market-wide news topics (defaults `sec` and `stablecoin`), each with a GDELT
  query and keywords; each becomes `news_<topic>_volume_z` and `_tone_z` features.
- `sources`: `enabled: true/false` per source; GDELT headline settings; RSS feeds; FRED
  series.
- `chainstar`: `warehouse` (or `SIGSTAR_CHAINSTAR_WAREHOUSE`), and the stablecoins to count,
  by chain and contract address.
- `analysis`: z-score window (30 days), anomaly threshold (3), driver threshold (2),
  forecast horizon (7 days), backtest origins (20).
- `alerts`: rules file and log file. `report`: output folder.

Environment variables: `SIGSTAR_FRED_API_KEY`, `SIGSTAR_ALERT_WEBHOOK_URL`,
`SIGSTAR_CHAINSTAR_WAREHOUSE`.

## Commands

| Command | What it does |
| --- | --- |
| `sigstar init` | Writes `sigstar.yml` and an example `sigstar-rules.yml` (never overwrites without `--force`). |
| `sigstar collect [--source NAME ...]` | Fetches what is new from every enabled source, or only the named ones (`coinbase, kraken, binance, binance_futures, okx, fear_greed, gdelt, rss, fred`). |
| `sigstar build` | Builds the dbt staging views and marts and runs the data tests. |
| `sigstar explain ASSET [--date D] [--top N --start D --end D] [--json]` | The move and what coincided with it, ranked, with evidence. |
| `sigstar relationships [--benchmark BTC]` | Correlations, betas, lead/lag, Granger tests, volatility regimes. |
| `sigstar anomalies [--days 7] [--threshold 3]` | Features far from their own recent history. |
| `sigstar forecast [ASSET ...] [--target price\|volatility\|volume]` | Forecasts with their backtest error against naive. |
| `sigstar alerts [--date D] [--no-webhook]` | Evaluates your rules, logs and sends new alerts. |
| `sigstar report [--window 90]` | Writes the daily brief (Markdown + HTML). |
| `sigstar run [--skip-collect]` | collect, build, alerts, report: the one to schedule. |
| `sigstar doctor` | Config, keys (set or not), each source's reachability and robots.txt, terms notes, chainstar, dbt. |

Global options go before the command: `--config PATH`, `--debug`.

## Tables

Schema `sigstar` in the warehouse. All times are UTC; a "day" is a UTC calendar day.

| Mart | Grain | What is in it |
| --- | --- | --- |
| `fct_prices` | asset, venue, interval (`1d`/`1h`), ts (candle open) | OHLCV of closed candles; `volume` in the coin, `volume_quote` in USD or USDT (venue-reported, or volume x close flagged by `volume_quote_is_estimate`), `log_return` |
| `fct_derivatives` | asset, venue, day | `funding_rate_sum` (the day's settlements, a fraction), `funding_events`, open interest snapshots at this and the next midnight, `oi_change_pct` in contracts |
| `fct_news` | one headline (URL) | title, link, source, time, provider (`gdelt`/`rss`), feed snippet (max 280 chars), `tags` |
| `bridge_news_tags` | headline, tag | which asset or topic a headline matched, and the keyword that matched |
| `news_rules` | keyword | the whole-word keyword rules behind the tags |
| `agg_news_daily` | tag, day | GDELT article count, share of all monitored articles, article-weighted tone, headlines held; `gdelt_gap` marks GDELT outage days (blanked) |
| `fct_sentiment` | indicator, day | Fear & Greed value, classification, daily change |
| `fct_macro` | series, observation day | value, change since the previous observation, its z-score |
| `fct_onchain_daily` | day, chain, metric, token_address | transactions, fees, fee payers, native and stablecoin transfers (by contract address), coverage |
| `features_daily` | asset, day (gap-free) | everything below, one row per asset and day |

Feature columns in `features_daily` (each `*_z` compares the value with the previous 30
days, the day itself excluded, and is empty until 10 earlier days exist):

| Column | Meaning |
| --- | --- |
| `close`, `ref_venue` | Close on the first venue in `coinbase, kraken, binance` that has the day |
| `ret_1d`, `ret_1d_z`, `ret_7d` | Log return, its z-score ("size of the move"), 7-day log return |
| `rv_7d`, `rv_30d` | Realised volatility from daily returns, annualised (x √365) |
| `range_pct`, `range_z` | (high - low) / close |
| `rv_intraday`, `rv_intraday_z` | Volatility within the day from the 24 hourly returns, annualised (hourly history starts at 7 days and grows) |
| `volume_quote`, `volume_z` | Volume across venues in USD/USDT (z on its log) |
| `spread_bps`, `spread_z` | Gap between the highest and lowest close among USD venues, in basis points |
| `funding_rate`, `funding_z` | Day's funding, averaged across venues |
| `oi_usd`, `oi_change_pct`, `oi_change_z` | Open interest at the end of the day; change in contracts during the day |
| `news_volume`, `news_volume_share`, `news_volume_z` | GDELT articles matching the asset's query (z on log(1 + n)) |
| `news_tone`, `news_tone_z`, `news_headlines` | GDELT tone; headlines held for the asset |
| `news_<topic>_volume(_z)`, `news_<topic>_tone(_z)` | The same for each topic (market-wide) |
| `fear_greed`, `fear_greed_change`, `fear_greed_change_z` | Fear & Greed Index and its daily change |
| `macro_<series>`, `macro_<series>_change(_z)` | Latest FRED value; change on observation days |
| `onchain_tx_count(_z)`, `onchain_active_wallets(_z)`, `onchain_fees_native`, `onchain_coverage_seconds` | chainstar activity on the asset's chains |
| `stablecoin_transfer_amount`, `stablecoin_transfer_z` | Stablecoin transfers on all chains (market-wide) |

Query them directly, for example with the [DuckDB CLI](https://duckdb.org/docs/installation/):

```sh
duckdb warehouse.duckdb "select asset, day, ret_1d, funding_z, news_volume_z
  from sigstar.features_daily where abs(ret_1d_z) > 2 order by day desc"
```

## Analysis methods

**Explain.** For the asset and day: the close-to-close return, its z-score and its rank in
the last 30 days. Then every feature's z-score that day and the day before, ranked by size
(|z| ≥ 2 is marked unusual), with the raw value and the 30-day median. News readings carry
that day's headlines (feed items first, then GDELT's relevance order) with links, and up
to three of the asset's headlines that day are listed as context either way. Other
assets' moves the same day show co-movement. Notes say which angles had no data. Intraday
range and volume move with price by construction, so read them as describing the move
rather than explaining it.

**Relationships.** Pearson correlations of daily returns (full window and last 30 days);
OLS betas against BTC; lead/lag correlations of funding, open interest change, news
volume, news tone and Fear & Greed change against returns and absolute returns for lags
-3 to +3 days; Granger F-tests (lags 1-3) with Bonferroni-adjusted p-values over all tests
run; two-state Markov-switching volatility regimes on daily returns (switching variance),
falling back to a median split of 7-day realised volatility if the model fails.

**Anomalies.** For every feature, the dbt z-score and a robust z (median and MAD of the
previous 60 days, scaled by 1.4826); either at or above 3 counts.

**Forecasts.** Targets: daily close, 7-day realised volatility and daily volume (price and
volume on the log scale). Models: naive (no change, with an empirical interval from the
series' own past k-day changes), ETS (additive errors, no trend or damped trend, chosen by
AIC) and ARIMA (orders (0,1,1), (1,1,0), (1,0,0), (1,0,1), chosen by AIC). Each is scored
by a rolling-origin backtest over the last 20 days, horizons 1-7: MAE, MAPE and MAE
relative to naive. A model is preferred only if it beats naive's MAE by at least 2%;
otherwise the naive forecast is shown and the output says so. Intervals are 80%.

**Alerts.** Rules in `sigstar-rules.yml`:

```yaml
lookback_days: 90
rules:
  - name: eth_funding_hot_news_negative
    description: ETH funding above its 95th percentile while its news tone is unusually negative
    assets: [ETH]                 # or ["*"] for every asset
    all:                          # or any:
      - {feature: funding_rate, above: p95}   # percentile of the asset's own history
      - {feature: news_tone_z, below: -2}     # a number
      - {feature: ret_1d_z, abs_above: 3}     # size either way
```

Rules are checked on each asset's latest closed day (or `--date`). A missing value never
satisfies a condition. Each alert lists every condition with its value and threshold, and
carries the not-financial-advice notice. New alerts (once per rule, asset and day) are
printed, appended to `alerts.jsonl` and `alerts.csv`, and POSTed as JSON
(`{"text": ..., "alert": {...}}`) to `SIGSTAR_ALERT_WEBHOOK_URL` when set; a webhook
failure is a warning, and the URL is never printed.

## Scheduling

`sigstar run` is safe to repeat. Daily at 00:30 UTC, after the day's candles close:

**cron** (`crontab -e`; cron uses the machine's time zone, so adjust the hour):

```
30 0 * * * cd /home/you/my-sigstar && /home/you/.local/bin/sigstar run >> sigstar.log 2>&1
```

Put `SIGSTAR_FRED_API_KEY=...` on its own line above it if you use FRED.

**systemd** (user units): `~/.config/systemd/user/sigstar.service`

```ini
[Unit]
Description=sigstar daily run

[Service]
Type=oneshot
WorkingDirectory=%h/my-sigstar
EnvironmentFile=-%h/.config/sigstar.env
ExecStart=%h/.local/bin/sigstar run
```

and `~/.config/systemd/user/sigstar.timer`

```ini
[Unit]
Description=Run sigstar daily

[Timer]
OnCalendar=*-*-* 00:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

then `systemctl --user enable --now sigstar.timer`. Keep keys in `~/.config/sigstar.env`
(`chmod 600`), one `NAME=value` per line.

## Privacy and limits

- No telemetry: dlt's and dbt's anonymous usage statistics are switched off; sigstar
  itself reports nothing anywhere.
- First-run history is bounded by the sources: Binance open interest goes back 30 days,
  OKX funding about three months, GDELT three months, Kraken 720 candles. History beyond
  that builds up as you keep running sigstar.
- 90 days is a short sample. Correlations, Granger tests, regimes and backtests on it are
  rough; the outputs say so.
- Prices from USDT venues (Binance) are not converted to USD; the cross-venue spread uses
  USD venues only.
- GDELT sometimes refuses requests even at its published pace (on 2026-10-21 it refused
  most of them); a skipped GDELT stream is caught up on a later run, and days where GDELT
  itself had an outage are blanked rather than counted as zero coverage.
- Postgres is not a target in this version: the dbt models use DuckDB SQL (regular
  expressions, `max_by`, `generate_series`, `ATTACH`), and the chainstar link is a DuckDB
  file. A chainstar warehouse in Postgres is not read yet.

## Development

```sh
uv sync
uv run pytest           # unit tests and dbt build on recorded fixtures (no network)
uv run ruff check . && uv run ruff format --check .
```

Fixtures in `tests/fixtures` are small slices of real responses. The project layout:

```
src/sigstar/
  cli.py          commands            config.py    sigstar.yml and defaults
  http.py         polite client       streams.py   sources -> streams with cursors
  collect.py      dlt pipeline        build.py     dbt runner
  sources/        one module per API  dbt/         staging, marts, tests, macros
  analysis/       explain, relationships, anomalies, forecast, catalog, data
  alerts.py       rules engine        report.py    brief (MD + HTML), svg.py charts
  workflow.py     shared steps        doctor.py    checks
```

Licence: Apache-2.0.
