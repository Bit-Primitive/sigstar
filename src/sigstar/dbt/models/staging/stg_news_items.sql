-- Headlines with link, source and time; snippet only when the feed itself provides one.
{% set columns = {
    'url': 'varchar', 'title': 'varchar', 'published_at': 'timestamptz', 'provider': 'varchar',
    'source_name': 'varchar', 'domain': 'varchar', 'language': 'varchar', 'snippet': 'varchar',
    'query_key': 'varchar'} %}
with raw as ({{ raw_table('news_items', columns) }})
select
    url,
    -- GDELT hands titles over tokenised, with a space around every punctuation mark
    -- ("Bitcoin price approaches $83 , 000"); put them back the way the publisher wrote
    -- them. Numbers first (decimals "1 . 1B", thousands "83 , 000"), then the space before closing marks.
    -- Feed titles are left exactly as published.
    case when provider = 'gdelt' then
        trim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
            title,
            '(\d) \. (\d)', '\1.\2', 'g'),
            '(\d) , (\d{3})\b', '\1,\2', 'g'),
            ' +([,.!?:;%)])', '\1', 'g'),
            '\( +', '(', 'g'))
    else title end as title,
    timezone('UTC', published_at) as published_at,
    provider,
    source_name,
    domain,
    language,
    snippet,
    query_key
from raw
where url is not null and title is not null {{ licence_exclude('provider', 'news') }}
