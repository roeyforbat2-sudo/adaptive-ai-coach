"""UTC timestamp helpers. The DB stores timestamps as ISO-8601 UTC text ending in 'Z'."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def ensure_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC; reject naive datetimes (ambiguous local time)."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime: a timezone is required")
    return value.astimezone(UTC)


def to_db(value: datetime) -> str:
    return ensure_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def from_db(value: str) -> datetime:
    return ensure_utc(datetime.fromisoformat(value))
