-- Grain: one row per asset and UTC day with both an on-chain and an exchange hourly price.
-- Hour by hour: the on-chain median swap price against the exchanges' typical price that
-- hour ((high + low + close) / 3, averaged over USD venues), in basis points; the day's
-- value is the median over the hours that have both. Needs exchange candles, so it is
-- empty in commercial mode.
select
    o.asset,
    o.day,
    median((o.close - x.typical) / x.typical * 10000) as onchain_price_gap_bps,
    count(*) as onchain_price_gap_hours
from {{ ref('fct_prices') }} as o
join (
    select asset, ts, avg((high + low + close) / 3) as typical
    from {{ ref('fct_prices') }}
    where "interval" = '1h' and venue <> 'onchain' and quote = '{{ var("spread_quote") }}'
    group by all
) as x on x.asset = o.asset and x.ts = o.ts
where o."interval" = '1h' and o.venue = 'onchain'
group by all
