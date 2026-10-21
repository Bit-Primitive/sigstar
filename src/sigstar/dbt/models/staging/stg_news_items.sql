-- Headlines with link, source and time; snippet only when the feed itself provides one.
{% set columns = {
    'url': 'varchar', 'title': 'varchar', 'published_at': 'timestamptz', 'provider': 'varchar',
    'source_name': 'varchar', 'domain': 'varchar', 'language': 'varchar', 'snippet': 'varchar',
    'query_key': 'varchar'} %}
with raw as ({{ raw_table('news_items', columns) }})
select
    url,
    title,
    timezone('UTC', published_at) as published_at,
    provider,
    source_name,
    domain,
    language,
    snippet,
    query_key
from raw
where url is not null and title is not null
