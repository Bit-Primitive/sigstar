-- FRED observations, value in the series' own units.
{% set columns = {'series_id': 'varchar', 'day': 'date', 'value': 'double'} %}
with raw as ({{ raw_table('fred_observations', columns) }})
select series_id, day, value from raw
