"""How sigstar words what it found: day lists, chainstar coverage notes."""

from __future__ import annotations

from datetime import date

import pandas as pd

from sigstar.analysis.explain import coverage_notes
from sigstar.workflow import day_ranges


def test_consecutive_days_read_as_one_range() -> None:
    days = pd.date_range("2026-07-23", "2026-10-22").date
    assert day_ranges(days) == "92 days (2026-07-23 to 2026-10-22)"


def test_gaps_split_the_ranges() -> None:
    days = [
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 7),
        date(2026, 10, 8),
    ]
    assert (
        day_ranges(days)
        == "5 days (2026-10-01 to 2026-10-02, 2026-10-05, 2026-10-07 to 2026-10-08)"
    )
    assert day_ranges([date(2026, 10, 1)]) == "1 day (2026-10-01)"
    assert day_ranges([]) == "no days"


def test_a_single_block_is_not_called_zero_seconds() -> None:
    notes = coverage_notes(
        pd.Series({"onchain_coverage_seconds": 0.0, "onchain_dex_coverage_seconds": 228.0})
    )
    assert any("come from a single moment (one block) of this day" in n for n in notes)
    assert any("DEX swaps span only 228 seconds of this day" in n for n in notes)
    assert not any("0 seconds" in n and "228" not in n for n in notes)
