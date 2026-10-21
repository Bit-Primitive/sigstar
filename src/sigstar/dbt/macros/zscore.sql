{# z-score of `expr` against its own trailing window: the previous `window` rows of the
   same partition, never including the current row, so today's value cannot dilute the
   baseline it is compared with. Null until `min_obs` earlier values exist or when the
   baseline does not vary. Rows must be one per day (features use a full date spine). #}
{% macro zscore(expr, partition_by, order_by, window=none, min_obs=none) -%}
    {%- set window = window or var('zscore_window') -%}
    {%- set min_obs = min_obs or var('zscore_min_obs') -%}
    {%- set frame -%}
        over (partition by {{ partition_by }} order by {{ order_by }}
              rows between {{ window }} preceding and 1 preceding)
    {%- endset -%}
    case
        when count({{ expr }}) {{ frame }} >= {{ min_obs }}
            -- "Does not vary" includes floating-point noise around a constant.
            and stddev_samp({{ expr }}) {{ frame }} > 0
            and stddev_samp({{ expr }}) {{ frame }} > 1e-9 * abs(avg({{ expr }}) {{ frame }})
        then ({{ expr }} - avg({{ expr }}) {{ frame }}) / stddev_samp({{ expr }}) {{ frame }}
    end
{%- endmacro %}
