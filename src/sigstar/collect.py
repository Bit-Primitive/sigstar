"""Collect: run each enabled source's streams through dlt into schema sigstar_raw.

dlt does the loading (merge on each table's primary key, so re-reading overlaps is safe)
and keeps each stream's cursor in its pipeline state, which is only saved when the load
succeeds. One failing stream (a venue blocked in your country, a busy API) is reported
and skipped; the others still load.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

import dlt

from sigstar.config import Config
from sigstar.errors import SigstarError
from sigstar.http import PoliteClient
from sigstar.sources import Row, Stream
from sigstar.sources.tables import TABLES, dlt_columns
from sigstar.streams import plan_streams
from sigstar.timeutil import parse_iso, utcnow

RAW_SCHEMA = "sigstar_raw"
PIPELINE_NAME = "sigstar"


@dataclass
class StreamResult:
    stream: str
    source: str
    rows: int = 0
    error: str | None = None
    hint: str | None = None
    note: str | None = None


def make_client(cfg: Config) -> PoliteClient:
    return PoliteClient(cache_dir=cfg.state_dir / "http-cache")


def pipeline(cfg: Config) -> dlt.Pipeline:
    cfg.warehouse.parent.mkdir(parents=True, exist_ok=True)
    return dlt.pipeline(
        pipeline_name=PIPELINE_NAME,
        pipelines_dir=str(cfg.state_dir / "dlt"),
        destination=dlt.destinations.duckdb(str(cfg.warehouse)),
        dataset_name=RAW_SCHEMA,
    )


def _resource_rows(
    streams: list[Stream], client: PoliteClient, results: list[StreamResult]
) -> Iterator[list[Row]]:
    state = dlt.current.resource_state()
    cursors: dict[str, str | None] = state.setdefault("cursors", {})
    fetched_at: dict[str, str] = state.setdefault("fetched_at", {})
    now = utcnow()
    # Streams never read, then the longest unread, go first: when a source allows only a
    # few requests, they go to the data that is missing rather than to re-reads.
    ordered = sorted(streams, key=lambda s: (s.key in cursors, fetched_at.get(s.key, "")))
    for stream in ordered:
        result = StreamResult(stream.key, stream.source)
        results.append(result)
        last = fetched_at.get(stream.key)
        if stream.refresh_hours and last and stream.key in cursors:
            age = (now - parse_iso(last)).total_seconds() / 3600
            if age < stream.refresh_hours:
                result.note = f"up to date (read {age:.1f}h ago)"
                continue
        try:
            rows, cursor = stream.fetch(client, cursors.get(stream.key))
        except SigstarError as exc:
            result.error, result.hint = str(exc), exc.hint
            continue
        result.rows = len(rows)
        fetched_at[stream.key] = now.isoformat()
        if cursor is not None:
            cursors[stream.key] = cursor
        if rows:
            yield rows


def _source(
    name: str, streams: list[Stream], client: PoliteClient, results: list[StreamResult]
) -> dlt.sources.DltSource:
    by_table: dict[str, list[Stream]] = defaultdict(list)
    for stream in streams:
        by_table[stream.table].append(stream)

    @dlt.source(name=name)
    def source() -> Iterator[dlt.sources.DltResource]:
        for table, table_streams in by_table.items():
            yield dlt.resource(
                _resource_rows(table_streams, client, results),
                name=table,
                table_name=table,
                primary_key=TABLES[table]["primary_key"],
                write_disposition="merge",
                columns=dlt_columns(table),
            )

    return source()


def collect(
    cfg: Config, only: set[str] | None = None, client: PoliteClient | None = None
) -> tuple[list[StreamResult], list[str], float]:
    """Collect every planned stream. Returns per-stream results, notices and seconds."""
    started = time.monotonic()
    streams, notices = plan_streams(cfg, only)
    if not streams:
        raise SigstarError(
            "nothing to collect: no enabled source matches",
            "; ".join(notices)
            if notices
            else "check sources: in sigstar.yml and the --source names "
            "(see `sigstar collect --help`)",
        )
    client = client or make_client(cfg)
    pipe = pipeline(cfg)
    results: list[StreamResult] = []
    by_source: dict[str, list[Stream]] = defaultdict(list)
    for stream in streams:
        by_source[stream.source].append(stream)
    for name, source_streams in by_source.items():
        pipe.run(_source(name, source_streams, client, results))
    return results, notices, time.monotonic() - started
