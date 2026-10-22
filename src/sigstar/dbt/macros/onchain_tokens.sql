{# (asset, chain, token) for every asset's `onchain.prices` or `onchain.dex_tokens` in
   sigstar.yml, as a values list of (asset, chain, token_address). Tokens are matched by
   address (mint on Solana) or `native`, never by symbol; EVM addresses arrive lowercased. #}
{% macro onchain_token_rows(key) -%}
    {%- set rows = [] -%}
    {%- for a in var('assets') -%}
        {%- for t in a.get(key, []) -%}{%- do rows.append((a.symbol, t.chain, t.token)) -%}{%- endfor -%}
    {%- endfor -%}
    {{ return(rows) }}
{%- endmacro %}

{# chainstar writes EVM addresses lowercase; compare case-insensitively anyway. #}
{% macro normalised_address(column) -%}
    case when {{ column }} like '0x%' then lower({{ column }}) else {{ column }} end
{%- endmacro %}
