"""Licence modes: which sources' published terms allow commercial use.

`sources: licence_mode:` in sigstar.yml is `personal` (the default: every enabled source,
for your own use) or `commercial`, which switches off every source whose terms restrict
commercial use or do not clearly allow it, and keeps the rest: sources that allow
commercial use (with the attribution they ask for) and chainstar's on-chain data.

The readings below are summarised from SOURCES.md, where each one is quoted with its link
and the date it was checked. A source whose terms are unclear about commercial use is
treated as restricted. This is a reading of published text, not legal advice.
"""

from __future__ import annotations

from dataclasses import dataclass

PERSONAL = "personal"
COMMERCIAL = "commercial"
MODES = (PERSONAL, COMMERCIAL)


@dataclass(frozen=True)
class Terms:
    source: str
    commercial: bool  # True only where the terms clearly allow commercial use
    note: str  # one line: what the terms say, for doctor and reports


TERMS: dict[str, Terms] = {
    t.source: t
    for t in (
        Terms(
            "coinbase",
            False,
            "Market Data Terms: personal or research use only; no redistribution of data "
            "or analytics derived from it",
        ),
        Terms(
            "kraken",
            False,
            "API terms: non-personal commercial use of public market data needs Kraken's "
            "prior permission",
        ),
        Terms("binance", False, "Terms of Use licence: non-commercial personal or internal use"),
        Terms(
            "binance_futures",
            False,
            "same Terms of Use as Binance spot: non-commercial personal or internal use",
        ),
        Terms(
            "okx",
            False,
            "API Agreement 9.4: personal, non-commercial trading use only; no analytics platform",
        ),
        Terms(
            "fear_greed",
            True,
            "commercial use allowed with attribution right next to the data (alternative.me)",
        ),
        Terms(
            "gdelt",
            True,
            "open for academic, commercial or governmental use; cite and link the GDELT Project",
        ),
        Terms(
            "fred",
            True,
            "FRED API terms set no commercial restriction; series owned by others need the "
            "owner's permission beyond personal use, so only public-domain series are kept",
        ),
        Terms(
            "rss",
            False,
            "feed terms do not clearly allow commercial use (Bitcoin Magazine: no "
            "reproduction or distribution without written consent)",
        ),
        Terms(
            "chainstar",
            True,
            "on-chain data and USD prices derived locally from DEX swaps by chainstar; the "
            "RPC provider's own terms still apply to how the data was read",
        ),
    )
}

# FRED series whose pages say "Public Domain: Citation Requested" (checked 2026-10-21, see
# SOURCES.md). Other series are left out in commercial mode: they may be copyrighted.
FRED_PUBLIC_DOMAIN = ("DFF", "DGS10", "DTWEXBGS")


def allows(mode: str, source: str) -> bool:
    """Whether `source` may run in `mode`. Unknown sources count as restricted."""
    if mode == PERSONAL:
        return True
    terms = TERMS.get(source)
    return terms is not None and terms.commercial


def restricted() -> list[str]:
    """Sources commercial mode leaves out."""
    return [name for name, terms in TERMS.items() if not terms.commercial]


def statement(mode: str, used: list[str]) -> str:
    """One line for every report: the mode, and the sources the data came from."""
    used_text = ", ".join(used) if used else "none yet"
    if mode == COMMERCIAL:
        return (
            f"Licence mode: commercial. Sources used: {used_text}. Left out because their "
            f"terms restrict commercial use or do not clearly allow it: "
            f"{', '.join(restricted())}. This is a reading of published terms (SOURCES.md), "
            "not legal advice."
        )
    return (
        f"Licence mode: personal. Sources used: {used_text}. Exchange market data is "
        "licensed for your personal use only (SOURCES.md)."
    )
