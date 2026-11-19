"""Rule-based insights: hand-built facts with known conclusions."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from coach.domain import Goal, GoalKind
from coach.insights import goal_progress, week_review, workout_insights
from coach.ui import components


def _run(**overrides: Any) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "family": "run",
        "duration_min": 60.0,
        "distance_km": 10.0,
        "pace_s_per_km": 360.0,
        "avg_hr": 145.0,
        "anaerobic_te": 0.5,
        "training_load": 80.0,
        "planned": {
            "workout_type": "easy",
            "target_duration_min": 60.0,
            "duration_vs_target_pct": 100.0,
        },
        "vs_last_28_days": {
            "median_pace_s_per_km": 360.0,
            "median_avg_hr": 145.0,
            "longest_km": 12.0,
            "median_duration_min": 55.0,
        },
        "recovery_that_morning": None,
    }
    facts.update(overrides)
    return facts


def _texts(facts: dict[str, Any]) -> list[tuple[str, str]]:
    return [(i.tone, i.text) for i in workout_insights(facts)]


def test_steady_planned_run() -> None:
    got = _texts(_run())
    assert ("good", "On plan: Easy run, 60 of 60 min.") in got
    assert any(t == "good" and "Steady" in x for t, x in got)


def test_faster_without_higher_hr_is_fitness_sign() -> None:
    got = _texts(_run(pace_s_per_km=340.0, avg_hr=144.0))
    assert any(t == "good" and "20 s/km faster" in x and "fitness" in x for t, x in got)


def test_slower_with_higher_hr_warns() -> None:
    got = _texts(_run(pace_s_per_km=380.0, avg_hr=152.0))
    assert any(t == "warn" and "20 s/km slower" in x for t, x in got)


def test_easy_run_done_too_hard_warns() -> None:
    got = _texts(_run(pace_s_per_km=330.0, avg_hr=158.0, anaerobic_te=2.4))
    assert any(t == "warn" and "harder than easy" in x for t, x in got)


def test_longest_run_and_extra_session() -> None:
    got = _texts(_run(distance_km=14.0, planned=None))
    assert ("note", "Extra session: not in your calendar plan.") in got
    assert ("good", "Your longest run in 4 weeks: 14.0 km.") in got


def test_no_comparison_without_history() -> None:
    got = _texts(_run(vs_last_28_days=None))
    assert not any("faster" in x or "slower" in x or "Steady" in x for _, x in got)


def test_gym_in_a_60_min_calendar_slot_counts_as_done() -> None:
    gym = _run(
        family="strength",
        distance_km=None,
        pace_s_per_km=None,
        duration_min=40.0,
        planned={
            "workout_type": "strength_full",
            "target_duration_min": 60.0,
            "duration_vs_target_pct": 67.0,
        },
        vs_last_28_days=None,
    )
    assert _texts(gym)[0] == ("good", "Done as planned: Gym session, 40 min active.")


def test_recovery_flags() -> None:
    got = _texts(
        _run(recovery_that_morning={"hrv_status_7d": "below normal range", "sleep_h": 5.2})
    )
    assert any(t == "warn" and "HRV below" in x for t, x in got)
    assert any("5.2 h" in x for _, x in got)


def _week(
    start: str, km: float, long_km: float | None, gym: int, complete: bool = True
) -> dict[str, Any]:
    return {
        "week_start": start,
        "complete": complete,
        "runs": 3,
        "run_km": km,
        "long_run_km": long_km,
        "strength_sessions": gym,
        "total_hours": 5.0,
    }


def test_week_review_rules() -> None:
    prior = [_week(f"2026-10-{d:02d}", 20.0, 8.0, 3) for d in (5, 12, 19, 26)]
    spike = week_review(
        [*prior, _week("2026-11-02", 30.0, 12.0, 1)], {"completed": 4, "missed": 2}, 5.9
    )
    assert spike is not None
    tones = {i.text.split(":")[0].split(" ")[0]: i.tone for i in spike.insights}
    texts = " | ".join(i.text for i in spike.insights)
    assert "up 50%" in texts and "New longest run" in texts and "Only 1 gym" in texts
    assert "4 of 6 sessions (67%)" in texts and "5.9 h" in texts
    assert tones["Running"] == "warn"
    steady = week_review([*prior, _week("2026-11-02", 21.0, 8.0, 3)])
    assert steady is not None
    assert [i.tone for i in steady.insights] == ["good", "good"]


HALF = Goal(
    kind=GoalKind.RACE,
    description="Half marathon",
    target_date=date(2027, 2, 26),
    target_value=21.1,
    unit="km",
    priority=1,
)


@pytest.mark.parametrize(
    ("today", "longest", "status"),
    [
        (date(2026, 11, 17), 10.0, "comfortable"),  # 8 km to add over ~12.4 weeks
        (date(2027, 1, 12), 10.0, "behind"),  # 8 km in ~4.4 weeks: 1.8 km/week > 1.5
        (date(2026, 11, 17), 18.5, "peak_reached"),
        (date(2027, 2, 15), 16.0, "taper"),
    ],
)
def test_goal_progress(today: date, longest: float, status: str) -> None:
    p = goal_progress(HALF, today, longest)
    assert p.status == status
    assert p.peak_long_km == 18.0 or status == "taper"


def test_goal_progress_without_date_or_runs() -> None:
    no_date = HALF.model_copy(update={"target_date": None})
    assert goal_progress(no_date, date(2026, 11, 17), 10.0).status == "no_date"
    no_runs = goal_progress(HALF, date(2026, 11, 17), None)
    assert no_runs.current_long_km is None and "No runs" in no_runs.insights[0].text


def test_components_escape_everything() -> None:
    evil = '<script>alert("x")</script>'
    from coach.insights import Insight

    out = components.card(
        evil, evil, evil, stats=[(evil, evil, evil)], insights=[Insight("warn", evil)]
    )
    out += components.workout("run", evil, evil, evil, [(evil, evil)], evil, False, [])
    out += components.hero(evil, evil, evil, "green", 50, evil)
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_quality_session_not_judged_as_easy_run() -> None:
    got = _texts(
        _run(
            session_hint="quality",
            pace_s_per_km=400.0,
            avg_hr=160.0,
            anaerobic_te=3.0,
            vs_last_28_days=None,
        )
    )
    assert ("good", "Quality session (intervals, tempo or test): a key workout.") in got
    assert not any("slower" in x or "harder than easy" in x for _, x in got)


def test_quality_hint_from_names() -> None:
    from coach.analytics.workout import run_kind_hint

    assert run_kind_hint("איכות 6*400") == "quality"
    assert run_kind_hint("1 חימום+ טסט 5 קמ+ שחרור") == "quality"
    assert run_kind_hint("Tempo 3x10") == "quality"
    assert run_kind_hint("10 זון 2") is None
    assert run_kind_hint("contest of shoes") is None
    assert run_kind_hint(None) is None
