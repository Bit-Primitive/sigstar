-- Grain: one row per asset, on-chain feature and UTC day: the feature's z-score against
-- comparable earlier days only, and a note when there were too few of them.
--
-- The rule. A chainstar warehouse can hold whole days or short samples, and chains can be
-- loaded on some days and not others, so a raw on-chain total moves with how much was
-- loaded, not only with what happened. An earlier day (in the previous zscore_window days,
-- the day itself excluded) is comparable to the day being scored only if every chain that
-- contributes to the scored day was loaded on it too, with a block count within
-- onchain_coverage_tolerance of the scored day's (blocks are counted, not seconds: equal
-- samples of 40 Solana slots spanned 8 to 11 seconds). The earlier day's value is summed
-- over those same chains only, so a sum over {Solana, Ethereum} is never compared with a
-- sum over {Solana}. Otherwise the z-score is as in macros/zscore.sql: at least
-- zscore_min_obs comparable days and a baseline that varies, else NULL. When the window
-- holds enough days with data but too few comparable ones, `note` says which chain was
-- missing or loaded differently, so explanations can say why a feature was not ranked.
{% set features = [
    ('onchain_tx_count', 'onchain_tx_count_z', 'v', 'sum'),
    ('onchain_active_wallets', 'onchain_active_wallets_z', 'v', 'sum'),
    ('stablecoin_transfer_amount', 'stablecoin_transfer_z', 'v', 'sum'),
    ('onchain_dex_volume_usd', 'onchain_dex_volume_z', 'ln(nullif(v, 0))', 'sum'),
    ('onchain_swap_count', 'onchain_swap_count_z', 'ln(1 + v)', 'sum'),
    ('onchain_price_gap_bps', 'onchain_price_gap_z', 'v', 'max'),
    ('range_pct', 'range_z', 'v', 'max'),
] %}
{# A day's value over its chains: the sum for totals, the one shared value otherwise. #}
{% set shared = [] %}
{% for f in features if f[3] == 'max' %}{% do shared.append("'" ~ f[0] ~ "'") %}{% endfor %}
{% set combine = "case when max({f}) in (" ~ shared | join(', ') ~ ") then max({v}) else sum({v}) end" %}
{% set window = var('zscore_window') %}
{% set min_obs = var('zscore_min_obs') %}
{% set tolerance = var('onchain_coverage_tolerance') %}

with parts as (
    select * from {{ ref('int_onchain_feature_parts') }}
    where value is not null
        and feature in ({% for f in features %}'{{ f[0] }}'{{ ", " if not loop.last }}{% endfor %})
),

days as (
    select
        asset, feature, day,
        count(*) as chains,
        {{ combine.replace('{f}', 'feature').replace('{v}', 'value') }} as v
    from parts
    group by asset, feature, day
),

matches as (
    -- Each earlier day in the window, chain by chain, where that chain was loaded with a
    -- comparable number of blocks.
    select
        d.asset, d.feature, d.day, d.chain, e.day as earlier_day, e.value
    from parts as d
    join parts as e
        on e.asset = d.asset
        and e.feature = d.feature
        and e.chain = d.chain
        and e.day >= d.day - {{ window }}
        and e.day < d.day
        and greatest(d.blocks, e.blocks) <= least(d.blocks, e.blocks) * (1 + {{ tolerance }})
),

comparable as (
    -- Earlier days comparable on every chain of the scored day, valued over those chains.
    select
        m.asset, m.feature, m.day, m.earlier_day,
        {{ combine.replace('{f}', 'm.feature').replace('{v}', 'm.value') }} as v
    from matches as m
    join days as d using (asset, feature, day)
    group by m.asset, m.feature, m.day, m.earlier_day, d.chains
    having count(*) = d.chains
),

stats as (
    select
        c.asset, c.feature, c.day,
        case c.feature
            {% for f in features %}when '{{ f[0] }}' then {{ f[2] | replace('v', 'c.v') }} {% endfor %}
        end as x
    from comparable as c
),

baseline as (
    select asset, feature, day, count(x) as n, avg(x) as mean, stddev_samp(x) as sd
    from stats
    group by all
),

window_days as (
    -- Earlier days in the window with any value for the feature, comparable or not.
    select d.asset, d.feature, d.day, count(*) as n
    from days as d
    join days as e
        on e.asset = d.asset and e.feature = d.feature
        and e.day >= d.day - {{ window }} and e.day < d.day
    group by all
),

per_chain as (
    select p.asset, p.feature, p.day, p.chain, count(distinct m.earlier_day) as n
    from parts as p
    left join matches as m
        on m.asset = p.asset and m.feature = p.feature and m.day = p.day and m.chain = p.chain
    group by all
),

notes as (
    select
        pc.asset, pc.feature, pc.day,
        string_agg(
            pc.chain || ' loaded with similar coverage (blocks within '
                || {{ (tolerance * 100) | round | int }} || '%) on ' || pc.n || ' of '
                || w.n || ' earlier days',
            '; ' order by pc.chain
        ) filter (where pc.n < {{ min_obs }}) as chains_short
    from per_chain as pc
    join window_days as w using (asset, feature, day)
    group by all
)

select
    d.asset,
    d.feature,
    {% for f in features %}{% if loop.first %}case d.feature {% endif %}when '{{ f[0] }}' then '{{ f[1] }}' {% if loop.last %}end{% endif %}{% endfor %} as z_column,
    d.day,
    d.v as value,
    coalesce(b.n, 0) as comparable_days,
    coalesce(w.n, 0) as window_days,
    case
        when b.n >= {{ min_obs }} and b.sd > 0 and b.sd > 1e-9 * abs(b.mean)
        then (
            case d.feature
                {% for f in features %}when '{{ f[0] }}' then {{ f[2] | replace('v', 'd.v') }} {% endfor %}
            end - b.mean
        ) / b.sd
    end as z,
    case
        when coalesce(w.n, 0) >= {{ min_obs }} and coalesce(b.n, 0) < {{ min_obs }}
        then 'not comparable: ' || coalesce(
            n.chains_short,
            'the same chains were loaded with similar coverage on only '
                || coalesce(b.n, 0) || ' of ' || w.n || ' earlier days'
        ) || ' (' || {{ min_obs }} || ' needed)'
    end as note
from days as d
left join baseline as b using (asset, feature, day)
left join window_days as w using (asset, feature, day)
left join notes as n using (asset, feature, day)
