"""Collection through dlt: cursors persist, failures stay isolated, scarce sources are not
re-read too soon, and unread streams go first."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from sigstar.collect import StreamResult, _source, pipeline
from sigstar.config import load_config
from sigstar.http import PoliteClient, SourceError
from sigstar.sources import Stream


def _fg_row(day: int) -> dict:
    return {
        "day": datetime(2026, 10, day, tzinfo=UTC).date(),
        "value": 50 + day,
        "classification": "x",
    }


def test_cursors_failures_refresh_and_order(tmp_path: Path) -> None:
    (tmp_path / "sigstar.yml").write_text("assets:\n  BTC: {coinbase: BTC-USD}\n")
    cfg = load_config(tmp_path / "sigstar.yml")
    seen: list[tuple[str, str | None]] = []

    def ok(client: PoliteClient, cursor: str | None):  # type: ignore[no-untyped-def]
        seen.append(("ok", cursor))
        return [_fg_row(1), _fg_row(2)], "2026-10-02"

    def scarce(client: PoliteClient, cursor: str | None):  # type: ignore[no-untyped-def]
        seen.append(("scarce", cursor))
        return [_fg_row(3)], "2026-10-03"

    def broken(client: PoliteClient, cursor: str | None):  # type: ignore[no-untyped-def]
        seen.append(("broken", cursor))
        raise SourceError("down", "later")

    streams = [
        Stream("t", "fear_greed", "ok", ok),
        Stream("t", "fear_greed", "scarce", scarce, refresh_hours=6),
        Stream("t", "fear_greed", "broken", broken),
    ]
    pipe = pipeline(cfg)
    results: list[StreamResult] = []
    pipe.run(_source("t", streams, PoliteClient(), results))
    assert {r.stream: r.rows for r in results} == {"ok": 2, "scarce": 1, "broken": 0}
    assert next(r for r in results if r.stream == "broken").error == "down"

    seen.clear()
    results = []
    pipe.run(_source("t", streams, PoliteClient(), results))
    # The never-collected stream goes first; the scarce one is skipped as fresh.
    assert seen[0] == ("broken", None)
    assert ("ok", "2026-10-02") in seen
    assert all(name != "scarce" for name, _ in seen)
    assert next(r for r in results if r.stream == "scarce").note.startswith("up to date")

    with duckdb.connect(str(cfg.warehouse), read_only=True) as con:
        (n,) = con.execute("select count(*) from sigstar_raw.fear_greed").fetchone()
    assert n == 3  # merge on the primary key: re-read rows are not duplicated
