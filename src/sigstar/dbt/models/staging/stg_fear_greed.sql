-- Crypto Fear & Greed Index (alternative.me), 0 = extreme fear, 100 = extreme greed.
{% set columns = {'day': 'date', 'value': 'bigint', 'classification': 'varchar'} %}
with raw as ({{ raw_table('fear_greed', columns) }})
select day, value, classification from raw
{% if not var('licence')['fear_greed'] %}where false{% endif %}
