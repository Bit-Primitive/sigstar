-- Grain: one row per FRED series and observation day (no rows on days without a value,
-- such as weekends). change is against the previous observation, in the series' units;
-- change_z compares it with the previous observations' changes.
with obs as (
    select
        series_id,
        day,
        value,
        value - lag(value) over (partition by series_id order by day) as change
    from {{ ref('stg_fred_observations') }}
)

select
    series_id,
    day,
    value,
    change,
    {{ zscore('change', 'series_id', 'day') }} as change_z
from obs
