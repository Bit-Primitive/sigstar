-- Daily on-chain activity from an attached chainstar warehouse (schema chainstar), in long
-- form: one row per day, chain, metric and token. Stablecoins are picked by contract
-- address (mint on Solana), never by symbol. Empty when no chainstar warehouse is set.
-- chainstar slices can cover minutes rather than whole days; coverage_seconds says how much
-- of each day the numbers come from.
{% if var('chainstar_enabled') %}
{% set stablecoins = var('stablecoins') %}
with stable as (
    {% if stablecoins %}
    select * from (values
        {% for s in stablecoins %}('{{ s.chain }}', '{{ s.address | lower if s.address.startswith("0x") else s.address }}', '{{ s.symbol }}'){{ "," if not loop.last }}
        {% endfor %}
    ) as t(chain, token_address, symbol)
    {% else %}
    select cast(null as varchar) as chain, cast(null as varchar) as token_address,
        cast(null as varchar) as symbol
    where false
    {% endif %}
),

transfers as (
    select
        f.date_day as day,
        f.chain,
        f.block_timestamp,
        case
            when f.token_address = 'native' then 'native'
            when s.token_address is not null then 'stablecoin'
        end as token_role,
        f.token_address,
        f.from_address,
        f.amount
    from chainstar_wh.chainstar.fct_transfers as f
    left join stable as s
        on s.chain = f.chain
        -- EVM (0x) addresses are hex and case-insensitive; Solana mints are case-sensitive.
        and s.token_address = case when f.token_address like '0x%'
            then lower(f.token_address) else f.token_address end
),

fees as (
    select date_day as day, chain, block_timestamp, from_address, fee_native
    from chainstar_wh.chainstar.fct_fees
),

metrics as (
    select day, chain, 'transfer_count' as metric, token_address, count(*)::double as value
    from transfers where token_role is not null group by all
    union all
    select day, chain, 'transfer_amount', token_address, sum(amount)
    from transfers where token_role is not null group by all
    union all
    select day, chain, 'stablecoin_transfer_count', '', count(*)::double
    from transfers where token_role = 'stablecoin' group by all
    union all
    select day, chain, 'stablecoin_transfer_amount', '', sum(amount)
    from transfers where token_role = 'stablecoin' group by all
    union all
    select day, chain, 'tx_count', '', count(*)::double from fees group by all
    union all
    select day, chain, 'fees_native', '', sum(fee_native) from fees group by all
    union all
    select day, chain, 'active_wallets', '', count(distinct from_address)::double
    from fees group by all
    union all
    select day, chain, 'coverage_seconds', '',
        date_diff('second', min(block_timestamp), max(block_timestamp))::double
    from fees group by all
)

select * from metrics
{% else %}
select
    cast(null as date) as day,
    cast(null as varchar) as chain,
    cast(null as varchar) as metric,
    cast(null as varchar) as token_address,
    cast(null as double) as value
where false
{% endif %}
