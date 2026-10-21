-- Open interest snapshots (value at ts). oi_usd is USD on OKX and USDT on Binance.
{% set columns = {
    'venue': 'varchar', 'asset': 'varchar', 'instrument': 'varchar', 'period': 'varchar',
    'ts': 'timestamptz', 'oi_base': 'double', 'oi_usd': 'double'} %}
with raw as ({{ raw_table('open_interest', columns) }})
select
    venue,
    asset,
    instrument,
    period,
    timezone('UTC', ts) as ts,
    oi_base,
    oi_usd
from raw
where oi_usd >= 0
