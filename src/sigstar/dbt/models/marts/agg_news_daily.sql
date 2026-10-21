-- Grain: one row per tag (asset or topic) and UTC day.
-- gdelt_articles: articles GDELT matched for the tag's query that day (English sources);
-- gdelt_volume_share: that count as a share of all articles GDELT monitored that day;
-- gdelt_tone: article-weighted average tone (about -10 negative to +10 positive);
-- headlines: items sigstar holds (GDELT lists + RSS) tagged by the keyword rules;
-- gdelt_gap: GDELT monitored under 10% of its usual volume that day (its numbers blanked).
with timeline as (
    select query_key as tag, cast(ts as date) as day, metric, ts, value
    from {{ ref('stg_gdelt_timeline') }}
    -- Daily steps only: finer steps for the same day would be counted twice.
    where resolution = 'day'
),

steps as (
    select
        tag,
        day,
        ts,
        max(case when metric = 'article_count' then value end) as articles,
        max(case when metric = 'all_articles' then value end) as all_articles,
        max(case when metric = 'tone' then value end) as tone
    from timeline
    group by all
),

gdelt as (
    select
        tag,
        day,
        sum(articles) as gdelt_articles,
        sum(all_articles) as gdelt_all_articles,
        -- Weight each step's tone by its article count; steps without articles report 0.
        sum(tone * articles) / nullif(sum(case when tone is not null then articles end), 0)
            as gdelt_tone
    from steps
    group by all
),

gdelt_checked as (
    -- GDELT has outage days when it monitored only a sliver of its usual volume (seen in
    -- October 2026: under 1% of a normal day). A count of 0 then means "no data", not
    -- "no news", so such days are blanked rather than read as a collapse in coverage.
    select
        *,
        gdelt_all_articles < 0.1 * median(gdelt_all_articles) over (partition by tag)
            as gdelt_gap
    from gdelt
),

headlines as (
    select
        b.tag,
        n.day,
        count(*) as headlines,
        count(*) filter (where n.provider = 'rss') as rss_headlines
    from {{ ref('bridge_news_tags') }} as b
    join {{ ref('fct_news') }} as n using (article_id)
    group by all
)

select
    coalesce(g.tag, h.tag) as tag,
    coalesce(g.day, h.day) as day,
    case when not g.gdelt_gap then g.gdelt_articles end as gdelt_articles,
    g.gdelt_all_articles,
    case when not g.gdelt_gap then g.gdelt_articles / nullif(g.gdelt_all_articles, 0) end
        as gdelt_volume_share,
    case when not g.gdelt_gap then g.gdelt_tone end as gdelt_tone,
    coalesce(g.gdelt_gap, false) as gdelt_gap,
    coalesce(h.headlines, 0) as headlines,
    coalesce(h.rss_headlines, 0) as rss_headlines
from gdelt_checked as g
full outer join headlines as h on g.tag = h.tag and g.day = h.day
