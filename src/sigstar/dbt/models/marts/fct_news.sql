-- Grain: one news item (headline) per URL, from GDELT article lists and RSS feeds.
-- Holds the headline, link, source and time, plus the feed's own short snippet when it has
-- one; never article text. tags lists the assets and topics its keyword rules matched.
with items as (
    select
        md5(url) as article_id,
        url,
        title,
        published_at,
        cast(published_at as date) as day,
        provider,
        source_name,
        domain,
        language,
        snippet,
        query_key
    from {{ ref('stg_news_items') }}
),

tags as (
    select article_id, string_agg(distinct tag, ',' order by tag) as tags
    from {{ ref('bridge_news_tags') }}
    group by article_id
)

select items.*, tags.tags
from items
left join tags using (article_id)
