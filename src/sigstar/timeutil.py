"""UTC time helpers. Everything sigstar stores is UTC; venues disagree on units."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

INTERVALS: dict[str, timedelta] = {"1d": timedelta(days=1), "1h": timedelta(hours=1)}


def utcnow() -> datetime:
    return datetime.now(UTC)


def from_seconds(value: float | str) -> datetime:
    return datetime.fromtimestamp(float(value), UTC)


def from_millis(value: float | str) -> datetime:
    return datetime.fromtimestamp(float(value) / 1000, UTC)


def to_millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_closed(open_time: datetime, interval: str, now: datetime) -> bool:
    """A candle is final once its whole interval has passed. Open candles are not stored,
    so a half-finished day never shows up as a 'move'."""
    return open_time + INTERVALS[interval] <= now


def start_of_day(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def days_ago(days: int, now: datetime | None = None) -> datetime:
    return start_of_day((now or utcnow()) - timedelta(days=days))


def date_range(start: date, end: date) -> list[date]:
    """Inclusive list of dates."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
