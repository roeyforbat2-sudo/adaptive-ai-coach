from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from coach.timeutil import from_db, to_db


def test_round_trip() -> None:
    value = datetime(2026, 11, 17, 4, 30, tzinfo=UTC)
    assert to_db(value) == "2026-11-17T04:30:00Z"
    assert from_db(to_db(value)) == value


def test_offset_converted_to_utc() -> None:
    value = datetime(2026, 11, 17, 6, 30, tzinfo=timezone(timedelta(hours=2)))
    assert to_db(value) == "2026-11-17T04:30:00Z"


def test_naive_rejected() -> None:
    with pytest.raises(ValueError, match="naive"):
        to_db(datetime(2026, 11, 17, 6, 30))  # noqa: DTZ001
