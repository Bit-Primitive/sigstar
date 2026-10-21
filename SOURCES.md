# Sources: terms, limits and attribution

What sigstar calls, what each provider's terms say about it, the limits sigstar keeps
to, and the credit each one asks for. Checked on **2026-10-21** from a South African IP
address; terms change, so read the linked pages yourself before relying on this. This is
a reading of published text, not legal advice.

How sigstar behaves with every source:

- It identifies itself: `User-Agent: sigstar/<version> (+https://bitprimitive.com/projects/sigstar)`.
- It reads each host's `robots.txt` first and obeys it for the agent `sigstar` (RFC 9309:
  a 4xx `robots.txt` means no rules, a 5xx means stop; `Crawl-delay` is honoured).
- It paces each host at or below its published limit (table below), retries busy answers
  with exponential backoff, honours `Retry-After`, and caches answers on disk.
- It uses public, unauthenticated endpoints only, except FRED, which needs a free key that
  you supply in `SIGSTAR_FRED_API_KEY`. No logins, no paywalled content, no scraping of
  web pages.
- For news it stores the headline, link, source, time and at most the short description
  the feed itself provides (cut to 280 characters). It never fetches or stores article
  text, and drops full text a feed embeds (`content:encoded`).
- Everything stays on your computer. Reports are for you: several venues license their
  market data for personal use only, so do not publish or redistribute reports or data.

## Summary

| Source | Used for | Status in v1 | Key | sigstar's pace | Attribution shown |
| --- | --- | --- | --- | --- | --- |
| Coinbase Exchange | daily and hourly candles | on | no | 1 request / 0.35 s | "Market data: Coinbase Exchange public API." |
| Kraken | daily and hourly candles | on | no | 1 request / 1.5 s | "Market data: Kraken public API." |
| Binance spot (data-api.binance.vision) | daily and hourly candles | on | no | 1 request / 0.25 s | "Market data: Binance public market data API" |
| Binance USDⓈ-M futures | funding, open interest | on | no | 1 request / 0.25 s | "Derivatives data: Binance USDⓈ-M futures public API." |
| OKX | funding, open interest | **off** (opt-in) | no | 1 request / 0.25 s | "Derivatives data: OKX public API." |
| alternative.me Fear & Greed | sentiment index | on | no | 1 request / 1 s | "Crypto Fear & Greed Index: alternative.me" with link |
| GDELT DOC 2.0 | news volume, tone, headlines | on | no | 1 request / 10 s | "The GDELT Project" with link |
| Bitcoin Magazine RSS | headlines | on | no | at most every 15 min (robots Crawl-delay 5 s) | publisher name, each headline links to it |
| FRED | macro series | on if key set | **yes** (free) | 1 request / 0.6 s | FRED notice + per-series source |
| CoinDesk, Cointelegraph, Decrypt, The Block, CryptoSlate RSS | | **left out** | | | see below |
| chainstar warehouse | on-chain activity | if configured | no | local file | |

## Market data

### Coinbase Exchange

- Endpoint: `GET https://api.exchange.coinbase.com/products/{BTC-USD}/candles?granularity=86400|3600&start&end`.
  Verified: rows `[time, low, high, open, close, volume]`, newest first, at most 300 per
  request (a larger range answers HTTP 400 "Count of aggregations requested exceeds 300");
  the current candle is returned while it is still forming (sigstar drops it).
- Limits: "Requests per second per IP: 10", bursts up to 15
  (https://docs.cdp.coinbase.com/exchange/rest-api/rate-limits). The candles page adds
  "Historical rates should not be polled frequently". sigstar asks about 3 per second and
  only for candles it does not have.
- Terms: Market Data Terms of Use (https://www.coinbase.com/legal/market_data, last
  updated 6 Feb 2023; read from a Dec 2025 archive copy because the live page returned
  403): use "is exclusively for you or your entity's personal or research purposes"; no
  redistribution or display of the data "or any data, charts, analytics ... derived from
  the Market Data" to third parties without consent; no indexes or benchmarks.
  **Fits personal use on your own machine. Do not publish reports built from it.**
- robots.txt: answers 401, which RFC 9309 treats as "no rules". Key: none.

### Kraken

- Endpoint: `GET https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440|60&since`.
  Verified: rows `[time, open, high, low, close, vwap, volume, count]`, prices as
  strings, keyed by Kraken's own pair name (XBTUSD comes back as XXBTZUSD); only the latest
  720 candles exist ("older data cannot be retrieved, regardless of the value of since",
  https://docs.kraken.com/api/docs/rest-api/get-ohlc-data); the newest row is still forming
  and is sometimes repeated.
- Limits: no published per-IP number for public endpoints was found; sigstar keeps to one
  request per 1.5 s.
- Terms: the API introduction says "Use of the Kraken APIs is subject to the Kraken Terms &
  Conditions" and that prior permission is needed for "any non-personal commercial use of
  data from publicly accessible endpoints, such as market data"
  (https://docs-legacy.kraken.com/api/docs/guides/global-intro/; contact
  marketdata@kraken.com). Global Terms §8 permit use "only for your own benefit"
  (https://www.kraken.com/legal/global-terms). **Personal use only.**
- robots.txt: 404 (no rules). Key: none.

### Binance spot

- Endpoint: `GET https://data-api.binance.vision/api/v3/klines?symbol&interval=1d|1h&startTime&limit=1000`.
  Binance documents this host as serving "only public market data" without authentication
  (https://developers.binance.com/docs/binance-spot-api-docs/faqs/market_data_only).
  **sigstar does not call api.binance.com: its robots.txt is `User-agent: * / Disallow: /`.**
  Verified rows: `[openTime ms, open, high, low, close, volume, closeTime, quoteVolume,
  trades, ...]`.
- Limits: request weight 6000 per minute per IP; klines weight 2, max 1000 rows; HTTP 429
  must be respected or the IP is banned (418) for 2 minutes to 3 days.
- Terms: the Terms of Use licence is "for non-commercial personal or internal business
  use"; Binance is not available in some countries (its List of Prohibited Countries could
  not be read, it loads with JavaScript) and the terms forbid getting round a restriction
  with a VPN. If a Binance host answers HTTP 451, sigstar says so and skips it.
  **Personal use; disable it if Binance is not offered where you are.**
- robots.txt on data-api.binance.vision: 404 (no rules). Key: none.

## Derivatives

### Binance USDⓈ-M futures

- Endpoints: `GET https://fapi.binance.com/fapi/v1/fundingRate?symbol&startTime&limit=1000`
  and `GET https://fapi.binance.com/futures/data/openInterestHist?symbol&period=1d&limit`.
  Verified: `fundingRate` is the fraction paid per settlement (8-hourly for BTC, ETH, SOL;
  `fundingTime` can carry a few stray milliseconds); open interest gives
  `sumOpenInterest` (contracts in the base coin) and `sumOpenInterestValue` (in USDT), daily
  snapshots at 00:00 UTC; **only the latest 30 days exist** (an older `startTime` answers
  "parameter 'startTime' is invalid"). sigstar keeps every snapshot it collects, so history
  grows from the first run.
- Limits: overall weight 2400 per minute; fundingRate shares 500 requests per 5 minutes per
  IP; openInterestHist 1000 requests per 5 minutes per IP, max 500 rows.
- Terms, geography: as Binance spot. robots.txt on fapi.binance.com disallows only named
  search engines. Key: none.

### OKX (off by default)

- Endpoints: `GET https://www.okx.com/api/v5/public/funding-rate-history?instId&after&limit=400`
  (about three months of history; sigstar stores `realizedRate`, the "Actual funding rate",
  not `fundingRate`, the predicted one) and
  `GET https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history?instId&period=1Dutc&end&limit=100`
  (rows `[ts, oi (contracts), oiCcy (coin), oiUsd (USD)]`; `1Dutc` aligns days to UTC,
  plain `1D` to UTC+8). Both verified. Limits: 10 requests per 2 seconds per IP and
  instrument (https://www.okx.com/docs-v5/en/).
- Why it is off: the OKX API Agreement (https://www.okx.com/help/okx-api-agreement, 28
  July 2026) §3.2(a) applies to public endpoints without a key, and §9.4 limits market data
  to "your own personal, non-commercial trading and account management purposes" and
  forbids using it "to build, operate, or contribute to any ... analytics platform". The
  docs also send US, Australian and EU users to other domains. Turn it on
  (`sources: okx: {enabled: true}`) only if you have read those terms and your use fits.
- robots.txt on www.okx.com allows `/api/`. Key: none.

## Sentiment

### Crypto Fear & Greed Index (alternative.me)

- Endpoint: `GET https://api.alternative.me/fng/?limit=N&format=json`. Verified: daily values
  0-100 with a classification and a Unix timestamp at 00:00 UTC, newest first.
- Terms (https://alternative.me/crypto/fear-and-greed-index/): free; "Commercial use is
  allowed as long as the attribution is given right next to the display of the data"; you
  "must properly acknowledge the source of the data and prominently reference it". sigstar's
  reports credit "Crypto Fear & Greed Index: alternative.me" with a link.
- Limit: "60 requests per minute enforced over a 10 minute window" (general API page,
  https://alternative.me/crypto/api/); sigstar makes one request per run.
- Timing note: in the real 90-day run the index's change correlates best with the
  **previous** day's return (lag -1), so treat it as a reaction to price, not a lead.
- robots.txt: alternative.me allows everything; api.alternative.me has none. Key: none.

## News

### GDELT DOC 2.0

- Endpoint: `GET https://api.gdeltproject.org/api/v2/doc/doc?query&mode&format=json&startdatetime&enddatetime`.
  Verified modes: `timelinevolraw` (matching article count per step, and in `norm` all
  articles GDELT monitored in that step), `timelinetone` (average tone), `artlist`
  (url, title, seendate, domain, language, sourcecountry; at most 250). Windows longer than a
  week come back in daily steps, shorter ones hourly or every 15 minutes, so sigstar always
  asks for more than a week. Only the last three months can be searched. Queries add
  `sourcelang:english` so the headline keyword rules apply.
- Terms (https://www.gdeltproject.org/about.html): "available for unlimited and
  unrestricted use for any academic, commercial, or governmental use of any kind without
  fee", and "any use or redistribution of the data must include a citation to the GDELT
  Project and a link to this website (https://www.gdeltproject.org/)". GDELT provides no
  article text; sigstar stores headline, URL, domain and time.
- Limit: the server's own refusal reads "Please limit requests to one every 5 seconds".
  On 2026-10-21 it also refused requests spaced 16 s to several minutes apart, so sigstar
  waits 10 s between requests and backs off from 30 s when refused, and asks for several
  days of headlines per request. After a request has been refused through every retry,
  sigstar leaves GDELT alone for the rest of that run instead of piling on. In the real
  run on 2026-10-21, six collects over about two hours got through for BTC and ETH only
  (90-day timelines and 7 days of headlines); SOL and the two topics were refused every
  time, typically after the first two to four requests of a run (see the run transcript). Why it refused at
  well under its published pace is not known; a shared IP address (mobile networks often
  share one address between many users) is a likely cause but was not verified.
- Data gaps: some days in early October 2026 came back with GDELT monitoring under 1% of
  its usual article volume, and one 7-day headline request answered `{}` (no results).
  Days where GDELT monitored under 10% of its usual volume are blanked in
  `agg_news_daily` (`gdelt_gap = true`) rather than read as a collapse in coverage.
- robots.txt: api.gdeltproject.org has none. Key: none.

### RSS feeds

Only feeds whose robots.txt **and** terms allow automated reading are on by default.

| Feed | Decision | Why |
| --- | --- | --- |
| Bitcoin Magazine, https://bitcoinmagazine.com/feed | **on** | robots.txt allows `/feed` with `Crawl-delay: 5` (honoured); terms (https://bitcoinmagazine.com/terms-of-use) have no automation clause and forbid reproduction and distribution, which sigstar does not do. The feed embeds full article text in `content:encoded`; sigstar discards it and keeps the title, link, time and a 280-character cut of the description. |
| The Block, https://www.theblock.co/rss.xml | left out | Terms (effective 8 Apr 2026) forbid "systematic or automated data collection" and processing the content with AI or ML, and allow an RSS feed only in "an RSS feed reader". |
| CoinDesk, https://www.coindesk.com/arc/outboundfeeds/rss/ | left out | Terms forbid "any robot, spider, scraper, or other automated means to access the Services for any purpose without our express written permission". |
| Cointelegraph, https://cointelegraph.com/rss | left out | Terms forbid "automated scraping, or creation of independent content pipelines" without written authorisation. |
| Decrypt, https://decrypt.co/feed | left out | Terms name RSS feeds as part of the Services and forbid collecting or caching content by automated means without consent. |
| CryptoSlate, https://cryptoslate.com/feed/ | left out | The feed and robots.txt answer 403 (a bot challenge), and the terms forbid bots. |

You can add feeds under `sources: rss: feeds:` in `sigstar.yml`; check that publisher's
robots.txt and terms first. sigstar will refuse any feed its robots.txt disallows.

## Macro

### FRED (Federal Reserve Bank of St. Louis), optional

- Endpoint: `GET https://api.stlouisfed.org/fred/series/observations?series_id&api_key&file_type=json&observation_start`.
  Needs a free key per user (https://fred.stlouisfed.org/docs/api/api_key.html), read
  only from `SIGSTAR_FRED_API_KEY` and redacted from every message. Without it, FRED is
  skipped with a notice.
- **Not verified live**: there was no key on the build machine, and fred.stlouisfed.org
  and api.stlouisfed.org showed a maintenance page on 2026-10-21. The parser follows the
  JSON example in FRED's documentation (read from an October 2026 archive copy) and skips
  values that are not numbers (days without an observation).
- Limit: "Up to 120 requests per minute are allowed before being served a 429 error code";
  sigstar keeps to about 100.
- Terms (https://fred.stlouisfed.org/docs/api/terms_of_use.html): applications must show
  "This product uses the FRED® API but is not endorsed or certified by the Federal Reserve
  Bank of St. Louis." (sigstar's reports do, when FRED data is used); third-party series
  need the owner's permission for anything beyond personal use.
- Default series (from each series page): DFF, Federal Funds Effective Rate, daily,
  percent; DGS10, 10-year Treasury constant maturity, daily, percent; DTWEXBGS, Nominal
  Broad U.S. Dollar Index, daily, index Jan 2006 = 100. All three are "Public Domain:
  Citation Requested" (Board of Governors of the Federal Reserve System, H.15 and H.10).
- Deliberately not included: **SP500** ("Copyrighted: Pre-Approval Required"; reproduction
  "prohibited except with the prior written permission of S&P Dow Jones Indices LLC") and
  **VIXCLS** ("Copyrighted: Citation Required", Cboe). Add them yourself only for personal
  use, with their citations.

## On-chain

### chainstar warehouse

- Read-only `ATTACH` of a DuckDB file built by
  [chainstar](https://bitprimitive.com/projects/chainstar) (tables in schema `chainstar`).
  sigstar reads `fct_transfers` and `fct_fees` and never writes to the file. The data comes
  from whatever RPC endpoint you used with chainstar, under that provider's terms.
- Stablecoins are matched by contract address (Solana: mint), never by symbol, because
  copies with the same symbol exist (the real demo warehouse holds tokens named USDC and
  USDT at other addresses). Default addresses were checked against Circle's and Tether's
  published lists and Etherscan on 2026-10-21.
