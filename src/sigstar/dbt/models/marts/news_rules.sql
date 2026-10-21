-- The keyword rules that tag headlines with assets and topics, one row per keyword, from
-- news_keywords: in sigstar.yml. Kept as a table so every tag can be traced to its rule.
-- pattern is a whole-word, case-insensitive regular expression built by sigstar.
{% set rules = var('news_rules') %}
{% if rules %}
select * from (values
    {% for r in rules %}('{{ r.tag }}', '{{ r.kind }}', '{{ r.keyword | replace("'", "''") }}', '{{ r.pattern | replace("'", "''") }}'){{ "," if not loop.last }}
    {% endfor %}
) as t(tag, tag_kind, keyword, pattern)
{% else %}
select cast(null as varchar) as tag, cast(null as varchar) as tag_kind,
    cast(null as varchar) as keyword, cast(null as varchar) as pattern
where false
{% endif %}
