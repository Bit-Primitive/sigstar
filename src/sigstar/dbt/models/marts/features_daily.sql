-- Grain: one row per asset and UTC day, on a gap-free date spine, so every trailing window
-- counts calendar days. Every *_z column is a z-score against the previous
-- var('zscore_window') days (the day itself excluded); see macros/zscore.sql.
-- Market-wide columns (topics, fear & greed, macro, stablecoins) repeat on every asset row.
{% set assets = var('assets') %}
{% set topics = var('topics') %}
{% set fred_series = var('fred_series') %}
{% set priority = var('venue_priority') %}

with asset_list as (
    {% if assets %}
    select * from (values
        {% for a in assets %}('{{ a.symbol }}'){{ "," if not loop.last }}{% endfor %}
    ) as t(asset)
    {% else %}
    select distinct asset from {{ ref('fct_prices') }}
    {% endif %}
),

asset_chains as (
    {% set pairs = [] %}
    {% for a in assets %}{% for c in a.chains %}{% do pairs.append((a.symbol, c)) %}{% endfor %}{% endfor %}
    {% if pairs %}
    select * from (values
        {% for p in pairs %}('{{ p[0] }}', '{{ p[1] }}'){{ "," if not loop.last }}{% endfor %}
    ) as t(asset, chain)
    {% else %}
    select cast(null as varchar) as asset, cast(null as varchar) as chain where false
    {% endif %}
),

daily_prices as (
    select * from {{ ref('fct_prices') }} where "interval" = '1d'
),

spine as (
    select a.asset, cast(d.day as date) as day
    from asset_list as a
    cross join (
        select unnest(generate_series(min(day), max(day), interval 1 day)) as day
        from daily_prices
    ) as d
),

ranked as (
    select
        *,
        row_number() over (
            partition by asset, day
            order by case venue
                {% for v in priority %}when '{{ v }}' then {{ loop.index }} {% endfor %}
                else 999 end
        ) as venue_rank
    from daily_prices
),

reference as (
    select
        asset,
        day,
        venue as ref_venue,
        close,
        log_return as ret_1d,
        (high - low) / close as range_pct
    from ranked
    where venue_rank = 1
),

intraday as (
    -- Realised volatility within the day from hourly returns on the reference venue,
    -- annualised; only days with all 24 hourly candles count.
    select
        h.asset,
        h.day,
        sqrt(sum(h.log_return * h.log_return)) * sqrt(365) as rv_intraday
    from {{ ref('fct_prices') }} as h
    join reference as r on r.asset = h.asset and r.day = h.day and r.ref_venue = h.venue
    where h."interval" = '1h' and h.log_return is not null
    group by all
    having count(*) = 24
),

venues as (
    select
        asset,
        day,
        sum(volume_quote) as volume_quote,
        count(*) as venues_reporting,
        case when count(*) filter (where quote = '{{ var("spread_quote") }}') >= 2 then
            (max(close) filter (where quote = '{{ var("spread_quote") }}')
             - min(close) filter (where quote = '{{ var("spread_quote") }}'))
            / avg(close) filter (where quote = '{{ var("spread_quote") }}') * 10000
        end as spread_bps
    from daily_prices
    group by all
),

derivatives as (
    select
        asset,
        day,
        avg(funding_rate_sum) as funding_rate,
        sum(oi_usd_close) as oi_usd,
        -- Change in contracts held (base units), so a price move alone does not count as
        -- new positioning. Venues missing either snapshot are left out of both sides.
        sum(oi_base_close) filter (where oi_base_open > 0 and oi_base_close is not null)
        / nullif(sum(oi_base_open) filter (where oi_base_open > 0 and oi_base_close is not null), 0)
        - 1 as oi_change_pct
    from {{ ref('fct_derivatives') }}
    group by all
),

news as (
    select tag as asset, day, gdelt_articles, gdelt_volume_share, gdelt_tone, headlines
    from {{ ref('agg_news_daily') }}
),

{% for t in topics %}
topic_{{ t }} as (
    select day, gdelt_articles, gdelt_tone
    from {{ ref('agg_news_daily') }}
    where tag = '{{ t }}'
),
{% endfor %}

sentiment as (
    select day, value as fear_greed, change_1d as fear_greed_change
    from {{ ref('fct_sentiment') }}
    where indicator = 'fear_greed'
),

onchain_asset as (
    select
        c.asset,
        o.day,
        sum(o.value) filter (where o.metric = 'tx_count') as onchain_tx_count,
        sum(o.value) filter (where o.metric = 'active_wallets') as onchain_active_wallets,
        sum(o.value) filter (where o.metric = 'fees_native') as onchain_fees_native,
        min(o.value) filter (where o.metric = 'coverage_seconds') as onchain_coverage_seconds
    from {{ ref('fct_onchain_daily') }} as o
    join asset_chains as c on c.chain = o.chain
    group by all
),

onchain_market as (
    select day, sum(value) as stablecoin_transfer_amount
    from {{ ref('fct_onchain_daily') }}
    where metric = 'stablecoin_transfer_amount'
    group by all
),

joined as (
    select
        s.asset,
        s.day,
        r.ref_venue,
        r.close,
        r.ret_1d,
        sum(r.ret_1d) over (
            partition by s.asset order by s.day rows between 6 preceding and current row
        ) as ret_7d,
        r.range_pct,
        i.rv_intraday,
        v.volume_quote,
        v.venues_reporting,
        v.spread_bps,
        d.funding_rate,
        d.oi_usd,
        d.oi_change_pct,
        n.gdelt_articles as news_volume,
        n.gdelt_volume_share as news_volume_share,
        n.gdelt_tone as news_tone,
        n.headlines as news_headlines,
        {% for t in topics %}
        tp_{{ t }}.gdelt_articles as news_{{ t }}_volume,
        tp_{{ t }}.gdelt_tone as news_{{ t }}_tone,
        {% endfor %}
        se.fear_greed,
        se.fear_greed_change,
        {% for f in fred_series %}
        last_value(m_{{ f | lower }}.value ignore nulls) over (
            partition by s.asset order by s.day rows between unbounded preceding and current row
        ) as macro_{{ f | lower }},
        m_{{ f | lower }}.change as macro_{{ f | lower }}_change,
        m_{{ f | lower }}.change_z as macro_{{ f | lower }}_change_z,
        {% endfor %}
        oa.onchain_tx_count,
        oa.onchain_active_wallets,
        oa.onchain_fees_native,
        oa.onchain_coverage_seconds,
        om.stablecoin_transfer_amount
    from spine as s
    left join reference as r on r.asset = s.asset and r.day = s.day
    left join intraday as i on i.asset = s.asset and i.day = s.day
    left join venues as v on v.asset = s.asset and v.day = s.day
    left join derivatives as d on d.asset = s.asset and d.day = s.day
    left join news as n on n.asset = s.asset and n.day = s.day
    {% for t in topics %}
    left join topic_{{ t }} as tp_{{ t }} on tp_{{ t }}.day = s.day
    {% endfor %}
    left join sentiment as se on se.day = s.day
    {% for f in fred_series %}
    left join {{ ref('fct_macro') }} as m_{{ f | lower }}
        on m_{{ f | lower }}.series_id = '{{ f }}' and m_{{ f | lower }}.day = s.day
    {% endfor %}
    left join onchain_asset as oa on oa.asset = s.asset and oa.day = s.day
    left join onchain_market as om on om.day = s.day
)

select
    *,
    stddev_samp(ret_1d) over (
        partition by asset order by day rows between 6 preceding and current row
    ) * sqrt(365) as rv_7d,
    stddev_samp(ret_1d) over (
        partition by asset order by day rows between 29 preceding and current row
    ) * sqrt(365) as rv_30d,
    {{ zscore('ret_1d', 'asset', 'day') }} as ret_1d_z,
    {{ zscore('range_pct', 'asset', 'day') }} as range_z,
    {{ zscore('rv_intraday', 'asset', 'day') }} as rv_intraday_z,
    {{ zscore('ln(nullif(volume_quote, 0))', 'asset', 'day') }} as volume_z,
    {{ zscore('spread_bps', 'asset', 'day') }} as spread_z,
    {{ zscore('funding_rate', 'asset', 'day') }} as funding_z,
    {{ zscore('oi_change_pct', 'asset', 'day') }} as oi_change_z,
    {{ zscore('ln(1 + news_volume)', 'asset', 'day') }} as news_volume_z,
    {{ zscore('news_tone', 'asset', 'day') }} as news_tone_z,
    {% for t in topics %}
    {{ zscore('ln(1 + news_' ~ t ~ '_volume)', 'asset', 'day') }} as news_{{ t }}_volume_z,
    {{ zscore('news_' ~ t ~ '_tone', 'asset', 'day') }} as news_{{ t }}_tone_z,
    {% endfor %}
    {{ zscore('fear_greed_change', 'asset', 'day') }} as fear_greed_change_z,
    {{ zscore('onchain_tx_count', 'asset', 'day') }} as onchain_tx_count_z,
    {{ zscore('onchain_active_wallets', 'asset', 'day') }} as onchain_active_wallets_z,
    {{ zscore('stablecoin_transfer_amount', 'asset', 'day') }} as stablecoin_transfer_z
from joined
