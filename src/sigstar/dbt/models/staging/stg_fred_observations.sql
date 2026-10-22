-- FRED observations, value in the series' own units.
{% set columns = {'series_id': 'varchar', 'day': 'date', 'value': 'double'} %}
with raw as ({{ raw_table('fred_observations', columns) }})
{% set allowed = var('licence')['fred_series'] %}
select series_id, day, value from raw
{% if allowed is not none %}
-- Commercial mode: only the public-domain series sigstar.yml asks for.
where series_id in ({% for s in allowed %}'{{ s }}', {% endfor %}'')
{% endif %}
