-- GDELT DOC 2.0 timeline points (article_count, all_articles, tone) per query and step.
{% set columns = {
    'query_key': 'varchar', 'query': 'varchar', 'metric': 'varchar', 'ts': 'timestamptz',
    'resolution': 'varchar', 'value': 'double'} %}
with raw as ({{ raw_table('gdelt_timeline', columns) }})
select
    query_key,
    query,
    metric,
    timezone('UTC', ts) as ts,
    resolution,
    value
from raw
