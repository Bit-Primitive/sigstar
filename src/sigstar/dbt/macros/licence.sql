{# `and <column> not in (...)` for the values the licence mode excludes, or nothing.
   sigstar passes var('licence') from sources.licence_mode in sigstar.yml; in commercial
   mode it lists the venues and news providers whose terms restrict commercial use, so
   rows collected earlier in personal mode are left out too. #}
{% macro licence_exclude(column, kind) -%}
    {%- set values = var('licence')['excluded'][kind] -%}
    {%- if values %} and {{ column }} not in ({% for v in values %}'{{ v }}'{{ ", " if not loop.last }}{% endfor %}){% endif -%}
{%- endmacro %}
