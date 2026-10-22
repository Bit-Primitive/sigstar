-- USD prices that chainstar derived from on-chain DEX swaps (chainstar.fct_token_prices),
-- for each asset's `onchain.prices` tokens in sigstar.yml, matched by chain and token
-- address (`native` for ETH, SOL and BTC), never by symbol. Only rows chainstar gives a
-- usd_price (its high or medium confidence; low and none are NULL there) and only closed
-- buckets: a bucket counts once the chain's loaded blocks run past its end, so the hour or
-- day still being filled is never read as a final price. Empty without a chainstar
-- warehouse that has prices.
{% set rows = onchain_token_rows('onchain_prices') %}
{% if var('chainstar_enabled') and var('chainstar_prices') and rows %}
with wanted as (
    select * from (values
        {% for r in rows %}('{{ r[0] }}', '{{ r[1] }}', '{{ r[2] }}'){{ "," if not loop.last }}
        {% endfor %}
    ) as t(asset, chain, token_address)
),

chain_end as (
    select chain, max(block_timestamp) as last_block_at
    from chainstar_wh.chainstar.fct_fees
    group by chain
),

prices as (
    select
        p.*,
        case p.granularity when 'hour' then '1h' when 'day' then '1d' end as "interval",
        p.bucket_start
            + case p.granularity when 'hour' then interval 1 hour else interval 1 day end
            as bucket_end
    from chainstar_wh.chainstar.fct_token_prices as p
    where p.usd_price is not null
        and p.granularity in ('hour', 'day')
)

select
    w.asset,
    p.chain,
    w.token_address,
    p."interval",
    p.bucket_start as ts,
    p.date_day as day,
    p.usd_price,
    p.observed_price,
    p.price_low,
    p.price_high,
    p.price_method,
    p.confidence,
    p.observations,
    p.rejected_observations,
    p.volume_usd,
    p.sources,
    p.price_id
from prices as p
join wanted as w
    on w.chain = p.chain
    and w.token_address = {{ normalised_address('p.token_address') }}
join chain_end as e
    on e.chain = p.chain
where p.bucket_end <= e.last_block_at
{% else %}
select
    cast(null as varchar) as asset,
    cast(null as varchar) as chain,
    cast(null as varchar) as token_address,
    cast(null as varchar) as "interval",
    cast(null as timestamp) as ts,
    cast(null as date) as day,
    cast(null as double) as usd_price,
    cast(null as double) as observed_price,
    cast(null as double) as price_low,
    cast(null as double) as price_high,
    cast(null as varchar) as price_method,
    cast(null as varchar) as confidence,
    cast(null as bigint) as observations,
    cast(null as bigint) as rejected_observations,
    cast(null as double) as volume_usd,
    cast(null as bigint) as sources,
    cast(null as varchar) as price_id
where false
{% endif %}
