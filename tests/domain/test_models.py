from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from coach.domain import DailyWellness, Feedback, Source, StrengthSession, StrengthSet
from tests.conftest import DAY, make_activity


def test_start_time_is_normalized_to_utc() -> None:
    jerusalem_winter = timezone(timedelta(hours=2))
    activity = make_activity(start_utc=datetime(2026, 11, 1, 7, 30, tzinfo=jerusalem_winter))
    assert activity.start_utc == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert activity.start_utc.utcoffset() == timedelta(0)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError):
        make_activity(start_utc=datetime(2026, 11, 1, 7, 30))  # noqa: DTZ001


@pytest.mark.parametrize(
    "overrides",
    [
        {"avg_hr": 300},
        {"avg_hr": 170, "max_hr": 160},
        {"duration_s": 0},
        {"distance_m": -1},
        {"source": "scraper"},
        {"source_id": "has spaces; DROP TABLE"},
        {"unexpected_field": 1},
    ],
)
def test_invalid_activity_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_activity(**overrides)


def test_untrusted_name_is_cleaned_and_truncated() -> None:
    activity = make_activity(name="  Morning\x00 run\x1b[31m " + "x" * 500)
    assert activity.name is not None
    assert "\x00" not in activity.name and "\x1b" not in activity.name
    assert len(activity.name) == 200
    assert make_activity(name="   ").name is None


def test_models_are_immutable() -> None:
    activity = make_activity()
    with pytest.raises(ValidationError):
        activity.duration_s = 1  # type: ignore[misc]


def test_wellness_battery_order() -> None:
    with pytest.raises(ValidationError):
        DailyWellness(source=Source.SYNTHETIC, date=DAY, body_battery_high=20, body_battery_low=50)


def test_feedback_pain_location_requires_flag() -> None:
    with pytest.raises(ValidationError):
        Feedback(date=DAY, pain_location="left calf")
    ok = Feedback(date=DAY, pain_flag=True, pain_location="left calf")
    assert ok.pain_flag


def test_feedback_ranges() -> None:
    with pytest.raises(ValidationError):
        Feedback(date=DAY, rpe=11)
    with pytest.raises(ValidationError):
        Feedback(date=DAY, mood=0)


def test_strength_session_rejects_duplicate_set_index() -> None:
    a_set = StrengthSet(exercise="Back Squat", set_index=0, reps=5, load_kg=80)
    with pytest.raises(ValidationError, match="unique"):
        StrengthSession(source=Source.MANUAL, source_id="m-1", date=DAY, sets=(a_set, a_set))
