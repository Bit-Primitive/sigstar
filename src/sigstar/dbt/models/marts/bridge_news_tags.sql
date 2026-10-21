-- Grain: one row per news item and matched tag (asset or topic), with the keyword that
-- matched, so every tag is explainable.
select
    md5(n.url) as article_id,
    r.tag,
    r.tag_kind,
    min(r.keyword) as matched_keyword
from {{ ref('stg_news_items') }} as n
join {{ ref('news_rules') }} as r
    on regexp_matches(lower(n.title || ' ' || coalesce(n.snippet, '')), r.pattern)
group by all
