{# Select from a raw table loaded by dlt, or an empty table with the same columns when the
   source has never been collected (disabled, no API key, not run yet). This keeps every
   mart buildable from whatever has been collected so far. #}
{% macro raw_table(name, columns) -%}
    {%- set relation = none -%}
    {%- if execute -%}
        {%- set relation = adapter.get_relation(
            database=target.database, schema='sigstar_raw', identifier=name) -%}
    {%- endif -%}
    {%- if relation is not none -%}
        select {% for col in columns %}{{ adapter.quote(col) }}{{ ", " if not loop.last }}{% endfor %}
        from {{ relation }}
    {%- else -%}
        select {% for col, type in columns.items() %}cast(null as {{ type }}) as {{ adapter.quote(col) }}{{ ", " if not loop.last }}{% endfor %}
        where false
    {%- endif -%}
{%- endmacro %}
