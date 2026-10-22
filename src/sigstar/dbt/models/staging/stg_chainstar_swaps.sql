-- DEX swaps from chainstar.fct_swaps that involve one of an asset's `onchain.dex_tokens`
-- (by chain and token address, never by symbol), one row per asset and swap: a swap of two
-- of the asset's tokens (WBTC for cbBTC, say) counts once. Only swaps chainstar trusts:
-- verified pools on Ethereum and Base (swaps in pools it could not verify have no USD
-- value and are never priced), and Solana's swaps (which have no pool). usd_value is
-- chainstar's, at its hourly on-chain price; NULL when that hour had no reliable price.
{% set rows = onchain_token_rows('dex_tokens') %}
{% if var('chainstar_enabled') and var('chainstar_prices') and rows %}
with wanted as (
    select * from (values
        {% for r in rows %}('{{ r[0] }}', '{{ r[1] }}', '{{ r[2] }}'){{ "," if not loop.last }}
        {% endfor %}
    ) as t(asset, chain, token_address)
),

swaps as (
    select
        swap_id,
        chain,
        date_day as day,
        block_timestamp,
        venue,
        {{ normalised_address('token_in_address') }} as token_in,
        {{ normalised_address('token_out_address') }} as token_out,
        usd_value
    from chainstar_wh.chainstar.fct_swaps
    where chain = 'solana' or pool_verified
)

select distinct
    w.asset,
    s.swap_id,
    s.chain,
    s.day,
    s.block_timestamp,
    s.venue,
    s.usd_value
from swaps as s
join wanted as w
    on w.chain = s.chain
    and w.token_address in (s.token_in, s.token_out)
{% else %}
select
    cast(null as varchar) as asset,
    cast(null as varchar) as swap_id,
    cast(null as varchar) as chain,
    cast(null as date) as day,
    cast(null as timestamp) as block_timestamp,
    cast(null as varchar) as venue,
    cast(null as double) as usd_value
where false
{% endif %}
