-- Closed OHLCV candles, one row per venue, market, interval and open time (UTC).
{% set columns = {
    'venue': 'varchar', 'asset': 'varchar', 'market': 'varchar', 'quote': 'varchar',
    'interval': 'varchar', 'ts': 'timestamptz', 'open': 'double', 'high': 'double',
    'low': 'double', 'close': 'double', 'volume': 'double', 'quote_volume': 'double',
    'vwap': 'double', 'trades': 'bigint'} %}
with raw as ({{ raw_table('candles', columns) }})
select
    venue,
    asset,
    market,
    quote,
    "interval",
    timezone('UTC', ts) as ts,
    open,
    high,
    low,
    close,
    volume,
    quote_volume,
    vwap,
    trades
from raw
where close > 0 {{ licence_exclude('venue', 'candles') }}
