-- Grain: one row per asset, chain, interval (1h/1d) and ts (bucket start, UTC).
-- chainstar's USD price from on-chain DEX swaps, with its method and confidence carried
-- through as chainstar wrote them: usd_price is the lower median of the bucket's swap
-- observations (not a closing price), price_low/price_high the lowest and highest kept,
-- observations the swaps kept, volume_usd their USD volume, sources the distinct pools
-- (Solana: traders), price_id the chainstar row it came from. Only high and medium
-- confidence; ETH has one row per chain (Ethereum, Base), and fct_prices picks one.
select
    asset,
    chain,
    token_address,
    "interval",
    ts,
    day,
    usd_price,
    observed_price,
    price_low,
    price_high,
    price_method,
    confidence,
    observations,
    rejected_observations,
    volume_usd,
    sources,
    price_id
from {{ ref('stg_chainstar_prices') }}
