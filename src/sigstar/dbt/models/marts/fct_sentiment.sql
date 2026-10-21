-- Grain: one row per indicator and UTC day. Today only the Crypto Fear & Greed Index
-- (alternative.me), 0 = extreme fear, 100 = extreme greed.
select
    'fear_greed' as indicator,
    day,
    cast(value as double) as value,
    classification,
    value - lag(value) over (order by day) as change_1d
from {{ ref('stg_fear_greed') }}
