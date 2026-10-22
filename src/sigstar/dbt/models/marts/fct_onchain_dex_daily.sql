-- Grain: one row per asset, UTC day and chain.
-- DEX activity in the asset's tokens from chainstar (see stg_chainstar_swaps): swap_count
-- (all trusted swaps), valued_swaps and volume_usd (those chainstar could value, at its
-- hourly on-chain price), and coverage_seconds, the time between the chain's first and last
-- loaded block that day: a chainstar slice of minutes is a sample of the day, not its total.
with swaps as (
    select
        asset,
        day,
        chain,
        count(*) as swap_count,
        count(usd_value) as valued_swaps,
        coalesce(sum(usd_value), 0) as volume_usd
    from {{ ref('stg_chainstar_swaps') }}
    group by all
),

coverage as (
    select day, chain, value as coverage_seconds
    from {{ ref('fct_onchain_daily') }}
    where metric = 'coverage_seconds'
)

select s.asset, s.day, s.chain, s.swap_count, s.valued_swaps, s.volume_usd, c.coverage_seconds
from swaps as s
left join coverage as c on c.day = s.day and c.chain = s.chain
