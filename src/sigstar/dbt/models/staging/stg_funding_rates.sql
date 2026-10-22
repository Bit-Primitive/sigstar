-- Funding settlements: fraction paid per funding period, positive when longs pay shorts.
{% set columns = {
    'venue': 'varchar', 'asset': 'varchar', 'instrument': 'varchar', 'ts': 'timestamptz',
    'funding_rate': 'double', 'mark_price': 'double'} %}
with raw as ({{ raw_table('funding_rates', columns) }})
select
    venue,
    asset,
    instrument,
    timezone('UTC', ts) as ts,
    funding_rate,
    mark_price
from raw
where true {{ licence_exclude('venue', 'derivatives') }}
