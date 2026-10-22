-- Grain: one row per asset, venue, interval and open time (ts, UTC).
-- Exchange venues (coinbase, kraken, binance): closed OHLCV candles. volume is in the
-- asset's own units. volume_quote is in the quote currency (USD or USDT): reported by the
-- venue where it can be (Binance quote volume, Kraken vwap x volume) and estimated as
-- volume x close where not (Coinbase), flagged by volume_quote_is_estimate.
-- Venue onchain: chainstar's USD price from DEX swaps (fct_onchain_prices), one chain per
-- asset and bucket (the highest confidence, then the most observations): close is the
-- bucket's median swap price, not a closing price; high and low are the highest and lowest
-- observations kept; open is NULL; volume_quote is the USD volume of the swaps behind the
-- price. log_return only between consecutive buckets. price_chain, price_method,
-- price_confidence and price_observations say where each on-chain price came from (NULL on
-- exchange rows).
with candles as (
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
        ln(close / lag(close) over (partition by venue, market, "interval" order by ts))
            as log_return,
        cast(null as varchar) as price_chain,
        cast(null as varchar) as price_method,
        cast(null as varchar) as price_confidence,
        cast(null as bigint) as price_observations
    from {{ ref('stg_candles') }}
),

onchain_ranked as (
    select
        *,
        row_number() over (
            partition by asset, "interval", ts
            order by case confidence when 'high' then 1 when 'medium' then 2 else 3 end,
                observations desc, volume_usd desc, chain
        ) as pick
    from {{ ref('fct_onchain_prices') }}
),

onchain as (
    select
        asset,
        'onchain' as venue,
        chain || ':' || token_address as market,
        'USD' as quote,
        "interval",
        ts,
        day,
        cast(null as double) as open,
        price_high as high,
        price_low as low,
        usd_price as close,
        volume_usd / usd_price as volume,
        volume_usd as volume_quote,
        false as volume_quote_is_estimate,
        cast(null as bigint) as trades,
        case
            when lag(ts) over w = ts - case "interval" when '1h' then interval 1 hour
                else interval 1 day end
            then ln(usd_price / lag(usd_price) over w)
        end as log_return,
        chain as price_chain,
        price_method,
        confidence as price_confidence,
        observations as price_observations
    from onchain_ranked
    where pick = 1
    window w as (partition by asset, "interval" order by ts)
)

select * from candles
union all by name
select * from onchain
