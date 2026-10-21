-- Grain: one row per UTC day, chain, metric and token_address ('' for chain-wide metrics).
-- Metrics: transfer_count / transfer_amount (native coin, or a stablecoin by address),
-- stablecoin_transfer_count / _amount (all tracked stablecoins), tx_count, fees_native,
-- active_wallets (distinct fee payers) and coverage_seconds (how much of the day the
-- chainstar data covers: a short slice is a sample, not the whole day).
select day, chain, metric, token_address, value
from {{ ref('stg_chainstar_activity') }}
