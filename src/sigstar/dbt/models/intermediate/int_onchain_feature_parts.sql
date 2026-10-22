-- Grain: one row per asset, on-chain feature, UTC day and contributing chain, with that
-- chain's part of the feature's value and how many of its blocks were loaded that day.
-- A chain contributes on a day only if chainstar has blocks of it that day; a loaded chain
-- with no matching activity contributes 0 (a sum feature) rather than being left out.
--   onchain_tx_count, onchain_active_wallets, onchain_fees_native: the asset's `chains`
--   stablecoin_transfer_amount: every chain (market-wide)
--   onchain_dex_volume_usd, onchain_swap_count: the chains of the asset's `dex_tokens`
--   onchain_price_gap_bps, range_pct (on days whose reference price is on-chain): the
--     chains the day's on-chain price came from (for cross_chain_wrapped_btc, Ethereum and
--     Base, whose wrapped BTC swaps chainstar prices Bitcoin from)
-- int_onchain_feature_z compares each day only with days of comparable coverage, and alert
-- rules' percentile thresholds use the same rule (sigstar/alerts.py).
{% set assets = var('assets') %}
{% set chain_pairs = [] %}
{% set dex_pairs = [] %}
{% for a in assets %}
    {% for c in a.chains %}{% do chain_pairs.append((a.symbol, c)) %}{% endfor %}
    {% for t in a.get('dex_tokens', []) %}{% if (a.symbol, t.chain) not in dex_pairs %}{% do dex_pairs.append((a.symbol, t.chain)) %}{% endif %}{% endfor %}
{% endfor %}

with coverage as (
    select day, chain, value as blocks
    from {{ ref('fct_onchain_daily') }}
    where metric = 'blocks'
),

metric as (
    select day, chain, metric, sum(value) as value
    from {{ ref('fct_onchain_daily') }}
    where token_address = ''
    group by all
),

asset_list as (
    {% if assets %}
    select * from (values {% for a in assets %}('{{ a.symbol }}'){{ "," if not loop.last }}{% endfor %}) as t(asset)
    {% else %}
    select distinct asset from {{ ref('fct_prices') }}
    {% endif %}
),

asset_chains as (
    {% if chain_pairs %}
    select * from (values {% for p in chain_pairs %}('{{ p[0] }}', '{{ p[1] }}'){{ "," if not loop.last }}{% endfor %}) as t(asset, chain)
    {% else %}
    select cast(null as varchar) as asset, cast(null as varchar) as chain where false
    {% endif %}
),

dex_chains as (
    {% if dex_pairs %}
    select * from (values {% for p in dex_pairs %}('{{ p[0] }}', '{{ p[1] }}'){{ "," if not loop.last }}{% endfor %}) as t(asset, chain)
    {% else %}
    select cast(null as varchar) as asset, cast(null as varchar) as chain where false
    {% endif %}
),

activity as (
    select ac.asset, c.day, f.feature, c.chain, coalesce(m.value, 0) as value, c.blocks
    from asset_chains as ac
    join coverage as c on c.chain = ac.chain
    cross join (
        values
            ('onchain_tx_count', 'tx_count'),
            ('onchain_active_wallets', 'active_wallets'),
            ('onchain_fees_native', 'fees_native')
    ) as f(feature, metric)
    left join metric as m on m.day = c.day and m.chain = c.chain and m.metric = f.metric
),

stablecoins as (
    select a.asset, c.day, 'stablecoin_transfer_amount' as feature, c.chain,
        coalesce(m.value, 0) as value, c.blocks
    from asset_list as a
    cross join coverage as c
    left join metric as m
        on m.day = c.day and m.chain = c.chain and m.metric = 'stablecoin_transfer_amount'
),

dex as (
    select dc.asset, c.day, f.feature, c.chain,
        coalesce(case f.feature when 'onchain_dex_volume_usd' then d.volume_usd
            else d.swap_count end, 0) as value,
        c.blocks
    from dex_chains as dc
    join coverage as c on c.chain = dc.chain
    cross join (values ('onchain_dex_volume_usd'), ('onchain_swap_count')) as f(feature)
    left join {{ ref('fct_onchain_dex_daily') }} as d
        on d.asset = dc.asset and d.day = c.day and d.chain = c.chain
),

onchain_daily as (
    -- The day's on-chain price, and whether it is the reference (no exchange has the day).
    select
        p.asset,
        p.day,
        p.price_chain,
        p.price_method,
        (p.high - p.low) / p.close as range_pct,
        not exists (
            select 1 from {{ ref('fct_prices') }} as x
            where x.asset = p.asset and x.day = p.day and x."interval" = '1d'
                and x.venue <> 'onchain'
        ) as is_reference
    from {{ ref('fct_prices') }} as p
    where p.venue = 'onchain' and p."interval" = '1d'
),

price_chains as (
    select o.*, c.chain, c.blocks
    from onchain_daily as o
    join coverage as c
        on c.day = o.day
        and (
            c.chain = o.price_chain
            or (o.price_method = 'cross_chain_wrapped_btc' and c.chain in ('ethereum', 'base'))
        )
    where o.price_method <> 'cross_chain_wrapped_btc' or c.chain <> o.price_chain
),

prices as (
    select pc.asset, pc.day, 'onchain_price_gap_bps' as feature, pc.chain,
        g.onchain_price_gap_bps as value, pc.blocks
    from price_chains as pc
    join {{ ref('int_onchain_price_gap') }} as g on g.asset = pc.asset and g.day = pc.day
    union all
    select asset, day, 'range_pct', chain, range_pct, blocks
    from price_chains
    where is_reference
)

select * from activity
union all select * from stablecoins
union all select * from dex
union all select * from prices
