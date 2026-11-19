"""Training program loading, day mapping and weekly suggestions (synthetic program)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from coach.engine.safety import NO_INTENSITY, SLEEP_CAUTION, Guardrail, SafetyAssessment
from coach.program import Program, load_program, session_for, week_sessions
from coach.ui import components
from coach.week_plan import build_week_plan

CLEAR = SafetyAssessment("green", [])
LOW = SafetyAssessment("amber", [Guardrail(NO_INTENSITY, "caution", "HRV below range.")])


def _week(n: int, start: date, long_km: float, total: float) -> dict[str, Any]:
    return {
        "week": n,
        "start": start.isoformat(),
        "end": (start + timedelta(days=6)).isoformat(),
        "phase": "Build",
        "monday": "7 km: 2 km warm-up, 6x400 m at 5:10-5:20, cool-down",
        "monday_km": 7,
        "wednesday": "6 km easy",
        "wednesday_km": 6,
        "long": f"{long_km:g} km easy",
        "long_km": long_km,
        "total_km": total,
    }


PROGRAM: dict[str, Any] = {
    "title": "Test plan",
    "race": {
        "name": "Test Half",
        "date": "2027-02-26",
        "distance_km": 21.1,
        "goal_time": "1:50:00",
        "goal_pace": "5:13 /km",
    },
    "start_date": "2026-11-08",
    "paces": [
        {"type": "Easy / Recovery", "pace": "6:05-6:40 /km", "rpe": "2-3/10", "purpose": "easy"}
    ],
    "blocks": [{"number": 2, "name": "Build", "weeks": [1, 2], "goal": "Build."}],
    "weeks": [
        _week(1, date(2026, 11, 8), 10, 23),
        _week(2, date(2026, 11, 15), 11, 24),
        {
            **_week(3, date(2026, 11, 22), 12, 26),
            "wednesday": "2 km warm-up + 5 km test + 1.5 km cool-down",
        },
    ],
    "race_week": [{"date": "2027-02-26", "plan": "Race."}],
}


@pytest.fixture
def program() -> Program:
    return Program.model_validate(PROGRAM)


def _acts(rows: list[tuple[str, str, float, float, str]]) -> pd.DataFrame:
    """rows: (date, family, km, minutes, name)."""
    df = pd.DataFrame(
        [
            {
                "id": i + 1,
                "date": pd.Timestamp(d),
                "family": f,
                "distance_m": km * 1000 if km else None,
                "duration_s": mins * 60,
                "name": name,
                "start_utc": datetime.fromisoformat(d).replace(hour=4, tzinfo=UTC),
            }
            for i, (d, f, km, mins, name) in enumerate(rows)
        ]
    )
    return df


def test_load_and_lookup(tmp_path: Path, program: Program) -> None:
    path = tmp_path / "program.json"
    path.write_text(json.dumps(PROGRAM), encoding="utf-8")
    loaded = load_program(path)
    assert loaded == program
    assert load_program(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_program(tmp_path / "bad.json") is None
    assert program.week_for(date(2026, 11, 17)).week == 2  # type: ignore[union-attr]
    assert program.pace_range("Easy") == (365, 400)
    assert program.peak_long_run == (12.0, date(2026, 11, 28))


def test_day_mapping(program: Program) -> None:
    week = program.week_for(date(2026, 11, 17))
    assert week is not None
    kinds = [(s.date.strftime("%a"), s.kind) for s in week_sessions(program, week)]
    assert kinds == [
        ("Sun", "strength"),
        ("Mon", "quality"),
        ("Tue", "strength"),
        ("Wed", "easy"),
        ("Thu", "strength"),
        ("Fri", "long"),
        ("Sat", "long"),
    ]
    assert session_for(program, date(2026, 11, 25)).kind == "test"
    assert session_for(program, date(2027, 2, 26)).kind == "race"
    assert session_for(program, date(2030, 1, 1)).kind == "none"


def test_week_plan_statuses_and_suggestions(program: Program) -> None:
    acts = _acts(
        [
            ("2026-11-09", "run", 6.0, 36, "Easy"),
            ("2026-11-11", "run", 6.0, 36, "Easy"),
            ("2026-11-14", "run", 10.0, 62, "Long"),  # last week: long run hit (10 of 10)
            ("2026-11-15", "strength", 0, 45, "Gym"),
            ("2026-11-16", "run", 7.0, 40, "6x400"),
        ]
    )
    view = build_week_plan(program, acts, CLEAR, date(2026, 11, 17))
    assert view is not None
    status = {d.session.date.strftime("%a"): d.status for d in view.days}
    assert status == {
        "Sun": "done",
        "Mon": "done",
        "Tue": "today",
        "Wed": "upcoming",
        "Thu": "upcoming",
        "Fri": "upcoming",
        "Sat": "upcoming",
    }
    assert view.done_km == 7.0 and view.target_km == 24
    texts = " | ".join(s.text for s in view.suggestions)
    assert "Quality run done" in texts
    assert "hit the plan" in texts and "go ahead with 11 km" in texts
    assert "Still to run this week: Wed 6 km easy · Fri/Sat 11 km easy" in texts


def test_long_run_capped_after_short_one(program: Program) -> None:
    acts = _acts([("2026-11-14", "run", 7.0, 45, "Long")])  # 7 of 10 km planned
    view = build_week_plan(program, acts, CLEAR, date(2026, 11, 17))
    assert view is not None
    texts = " | ".join(s.text for s in view.suggestions)
    assert "~8.5 km instead of 11 km" in texts
    assert "ran 7.0 of 23 km planned" in texts


def test_safety_and_easy_pace_rules(program: Program) -> None:
    acts = _acts(
        [("2026-11-10", "run", 8.0, 44, "Easy"), ("2026-11-12", "run", 8.0, 45, "Easy")]
    )  # ~5:30
    sleepy = SafetyAssessment(
        "amber", [*LOW.guardrails, Guardrail(SLEEP_CAUTION, "caution", "Short sleep.")]
    )
    view = build_week_plan(program, acts, sleepy, date(2026, 11, 15))
    assert view is not None
    tones = {s.text.split(" ")[0]: s.tone for s in view.suggestions}
    texts = " | ".join(s.text for s in view.suggestions)
    assert "swap the quality session for an easy run" in texts
    assert "faster than the plan's easy range" in texts
    assert "Saturday" in texts
    assert tones["Recovery"] == "warn"


def test_flexible_long_run_day(program: Program) -> None:
    acts = _acts([("2026-11-20", "run", 11.0, 68, "Long")])  # done on Friday
    view = build_week_plan(program, acts, CLEAR, date(2026, 11, 21))
    assert view is not None
    status = {d.session.date.strftime("%a"): d.status for d in view.days}
    assert status["Fri"] == "done" and status["Sat"] == "rest"


def test_outside_program_returns_none(program: Program) -> None:
    assert (
        build_week_plan(
            program, _acts([("2026-11-09", "run", 5, 30, "x")]), CLEAR, date(2030, 1, 1)
        )
        is None
    )


def test_program_components_escape() -> None:
    evil = "<img src=x onerror=alert(1)>"
    out = components.program_banner(
        evil,
        evil,
        evil,
        evil,
        50,
        [
            {
                "day": evil,
                "date": evil,
                "title": evil,
                "detail": evil,
                "kind": evil,
                "status": "done",
            }
        ],
        evil,
        20,
        [],
    )
    out += components.table([evil], [[evil]], [evil])
    assert "<img" not in out and "&lt;img" in out
