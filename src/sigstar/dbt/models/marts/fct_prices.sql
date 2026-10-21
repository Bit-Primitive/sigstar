-- Grain: one closed candle per asset, venue, interval and open time (ts, UTC).
-- volume is in the asset's own units. volume_quote is in the quote currency (USD or USDT):
-- reported by the venue where it can be (Binance quote volume, Kraken vwap x volume) and
-- estimated as volume x close where not (Coinbase), flagged by volume_quote_is_estimate.
select
    asset,
    venue,
    market,
    quote,
    "interval",
    ts,
    cast(ts as date) as day,
    open,
    high,
    low,
    close,
    volume,
    coalesce(quote_volume, volume * close) as volume_quote,
    quote_volume is null as volume_quote_is_estimate,
    trades,
    ln(close / lag(close) over (partition by venue, market, "interval" order by ts)) as log_return
from {{ ref('stg_candles') }}
