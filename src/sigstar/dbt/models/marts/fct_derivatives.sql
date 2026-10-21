-- Grain: one row per asset, venue and UTC day.
-- funding_rate_sum adds up that day's funding settlements (fraction of position value
-- paid by longs over the day; 0.0003 = 0.03%). Open interest is a snapshot series, so the
-- day's change is the snapshot at the next midnight against the one at this midnight.
with funding as (
    select
        asset,
        venue,
        cast(ts as date) as day,
        sum(funding_rate) as funding_rate_sum,
        avg(funding_rate) as funding_rate_mean,
        count(*) as funding_events
    from {{ ref('stg_funding_rates') }}
    group by all
),

oi_snapshots as (
    -- Daily snapshots taken at 00:00 UTC; keep one per venue and day.
    select asset, venue, cast(ts as date) as day, max_by(oi_usd, ts) as oi_usd,
        max_by(oi_base, ts) as oi_base
    from {{ ref('stg_open_interest') }}
    where period = '1d'
    group by all
),

oi as (
    select
        asset,
        venue,
        day,
        oi_usd as oi_usd_open,
        lead(oi_usd) over (partition by asset, venue order by day) as oi_usd_close,
        oi_base as oi_base_open,
        lead(oi_base) over (partition by asset, venue order by day) as oi_base_close,
        lead(day) over (partition by asset, venue order by day) as next_snapshot_day
    from oi_snapshots
)

select
    coalesce(f.asset, o.asset) as asset,
    coalesce(f.venue, o.venue) as venue,
    coalesce(f.day, o.day) as day,
    f.funding_rate_sum,
    f.funding_rate_mean,
    f.funding_events,
    o.oi_usd_open,
    o.oi_base_open,
    -- Only a snapshot from the very next day closes this day.
    case when o.next_snapshot_day = o.day + 1 then o.oi_usd_close end as oi_usd_close,
    case when o.next_snapshot_day = o.day + 1 then o.oi_base_close end as oi_base_close,
    case when o.next_snapshot_day = o.day + 1 and o.oi_base_open > 0
        then o.oi_base_close / o.oi_base_open - 1 end as oi_change_pct
from funding as f
full outer join oi as o
    on f.asset = o.asset and f.venue = o.venue and f.day = o.day
