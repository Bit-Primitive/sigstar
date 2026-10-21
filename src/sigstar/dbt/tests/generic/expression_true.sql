{# Fails for rows where the SQL expression is false. #}
{% test expression_true(model, expression, column_name=none) %}
select * from {{ model }} where not ({{ expression }})
{% endtest %}
