"""The weekly view follows the calendar; titles are translated; in-app sync detects changes."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from coach import secrets
from coach.config import Settings
from coach.db import connect, migrate
from coach.domain import WorkoutType
from coach.engine.safety import SafetyAssessment
from coach.integrations.gcal.classify import classify
from coach.integrations.gcal.translate import distance_km, translate_title
from coach.program import Program
from coach.services import sync as live_sync
from coach.week_plan import build_week_plan, calendar_sessions, scheduled_session
from tests.integrations.gcal.test_gcal import FakeService, ev
from tests.test_program import PROGRAM

TZ = ZoneInfo("Asia/Jerusalem")
CLEAR = SafetyAssessment("green", [])
WEEK2 = date(2026, 11, 15)  # Sunday of program week 2


@pytest.mark.parametrize(
    ("title", "english", "km", "kind"),
    [
        ("מכון- UPPER", "Upper body", None, WorkoutType.STRENGTH_UPPER),
        ("מכון- רגליים", "Legs", None, WorkoutType.STRENGTH_LOWER),
        ("מכון- FB", "Full body", None, WorkoutType.STRENGTH_FULL),
        ("ריצה- 6 קל", "6 km easy", 6.0, WorkoutType.EASY),
        ("ריצה- 11 קל", "11 km easy", 11.0, WorkoutType.EASY),
        (
            "ריצה- איכות: 2 חימום+ 6x400 (5:00) + 2 min jog",
            "Quality: 2 km warm-up + 6x400 (5:00) + 2 min jog",
            None,
            WorkoutType.INTERVALS,
        ),
        (
            "ריצה- איכות: 2 חימום+ 10 עליות מחוץ לבית",
            "Quality: 2 km warm-up + 10 hill repeats outdoors",
            None,
            WorkoutType.INTERVALS,
        ),
    ],
)
def test_translate_and_classify_real_title_patterns(
    title: str, english: str, km: float | None, kind: WorkoutType
) -> None:
    assert translate_title(title) == english
    got = classify(title)
    assert got is not None and got[1] is kind
    if kind is not WorkoutType.INTERVALS:
        assert distance_km(title) == km


def test_translation_keeps_unknown_words() -> None:
    assert translate_title("ריצה- 5 קל עם חבר") == "5 km easy עם חבר"
    assert translate_title(None) == "" and distance_km("ריצה- איכות") is None


def _planned(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    """rows: (date, workout_type, title) as stored from the calendar."""
    return pd.DataFrame(
        [
            {
                "id": i + 1,
                "source": "calendar",
                "date": pd.Timestamp(d),
                "sport": "run",
                "workout_type": t,
                "target_duration_s": 3600,
                "target_distance_m": None,
                "notes": title,
            }
            for i, (d, t, title) in enumerate(rows)
        ]
    )


BASE_WEEK = [
    ("2026-11-15", "strength_full", "מכון- UPPER"),
    ("2026-11-16", "intervals", "ריצה- איכות: 2 חימום+ 6x400 (5:00) + 2 min jog"),
    ("2026-11-17", "strength_full", "מכון- רגליים"),
    ("2026-11-18", "easy", "ריצה- 6 קל"),
    ("2026-11-19", "strength_full", "מכון- FB"),
    ("2026-11-20", "easy", "ריצה- 11 קל"),
]


@pytest.fixture
def program() -> Program:
    return Program.model_validate(PROGRAM)


def _kinds(program: Program, planned: pd.DataFrame) -> list[tuple[str, str, str]]:
    week = program.week_for(WEEK2)
    assert week is not None
    sessions = calendar_sessions(program, week, planned)
    assert sessions is not None
    return [(f"{s.date:%a}", s.kind, s.detail) for s in sessions]


def test_week_from_calendar(program: Program) -> None:
    assert _kinds(program, _planned(BASE_WEEK)) == [
        ("Sun", "strength", "Upper body"),
        ("Mon", "quality", "Quality: 2 km warm-up + 6x400 (5:00) + 2 min jog"),
        ("Tue", "strength", "Legs"),
        ("Wed", "easy", "6 km easy"),
        ("Thu", "strength", "Full body"),
        ("Fri", "long", "11 km easy"),
        ("Sat", "rest", ""),
    ]


def test_moving_and_editing_in_calendar_changes_the_week(program: Program) -> None:
    moved = [r for r in BASE_WEEK if r[0] != "2026-11-20"] + [("2026-11-21", "easy", "ריצה- 13 קל")]
    kinds = _kinds(program, _planned(moved))
    assert kinds[5] == ("Fri", "rest", "") and kinds[6] == ("Sat", "long", "13 km easy")
    view = build_week_plan(
        program,
        pd.DataFrame(
            columns=["id", "date", "family", "distance_m", "duration_s", "name", "start_utc"]
        ),
        CLEAR,
        date(2026, 11, 15),
        _planned(moved),
    )
    assert view is not None and view.from_calendar
    assert view.target_km == 7 + 6 + 13  # quality km from the program, the rest from titles


def test_deleted_event_becomes_rest_and_extra_event_is_shown(program: Program) -> None:
    edited = [r for r in BASE_WEEK if r[0] != "2026-11-18"] + [
        ("2026-11-21", "strength_full", "מכון- FB")
    ]
    kinds = _kinds(program, _planned(edited))
    assert kinds[3] == ("Wed", "rest", "")
    assert kinds[6] == ("Sat", "strength", "Full body")


def test_falls_back_to_program_without_calendar_events(program: Program) -> None:
    assert scheduled_session(program, None, date(2026, 11, 18)).detail == "6 km easy"
    other_week = _planned([("2026-11-25", "easy", "ריצה- 9 קל")])
    assert scheduled_session(program, other_week, date(2026, 11, 18)).from_calendar is False
    assert scheduled_session(program, _planned(BASE_WEEK), date(2026, 11, 18)).from_calendar is True


# --- in-app sync ----------------------------------------------------------------------------


@pytest.fixture
def real_db(tmp_path: Path) -> Path:
    db = tmp_path / "coach.sqlite"
    conn = connect(db)
    migrate(conn)
    conn.close()
    return db


def test_sync_skips_unconnected_sources(real_db: Path) -> None:
    settings = Settings(_env_file=None)
    assert live_sync.sync_calendar(real_db, settings).ok is False
    assert live_sync.sync_garmin(real_db, settings).ok is False
    assert live_sync.is_stale(real_db, live_sync.GCAL, timedelta(minutes=5))
    assert live_sync.last_success(real_db, live_sync.GCAL) is None


def test_calendar_sync_detects_changes(real_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets.set_secret(secrets.GOOGLE_CALENDAR_READONLY_TOKEN, "fake-refresh-token")
    monkeypatch.setenv("COACH_TIMEZONE", "Asia/Jerusalem")
    settings = Settings(_env_file=None)
    today = datetime.now(TZ).date()
    events: list[dict[str, Any]] = [ev("e1", "ריצה- 6 קל", today + timedelta(days=1))]

    first = live_sync.sync_calendar(real_db, settings, service=FakeService({"primary": [events]}))
    assert first.ok and first.changed
    again = live_sync.sync_calendar(real_db, settings, service=FakeService({"primary": [events]}))
    assert again.ok and not again.changed  # nothing new in the calendar
    edited = [ev("e1", "ריצה- 8 קל", today + timedelta(days=2))]  # moved + longer
    third = live_sync.sync_calendar(real_db, settings, service=FakeService({"primary": [edited]}))
    assert third.changed
    conn = sqlite3.connect(real_db)
    (row,) = conn.execute(
        "SELECT date, notes FROM planned_workouts WHERE source = 'calendar'"
    ).fetchall()
    conn.close()
    assert row == ((today + timedelta(days=2)).isoformat(), "ריצה- 8 קל")
    assert not live_sync.is_stale(real_db, live_sync.GCAL, timedelta(minutes=5))


def test_sync_refuses_demo_database(tmp_path: Path) -> None:
    from coach.synthetic import generate, seed_database

    db = tmp_path / "dev.sqlite"
    conn = connect(db)
    migrate(conn)
    seed_database(conn, generate(14, end_date=date(2026, 11, 16), seed=1, tz=TZ))
    conn.close()
    secrets.set_secret(secrets.GOOGLE_CALENDAR_READONLY_TOKEN, "fake-refresh-token")
    out = live_sync.sync_calendar(
        db, Settings(_env_file=None), service=FakeService({"primary": [[]]})
    )
    assert not out.ok and "demo data" in out.message


def test_ev_helper_is_timezone_aware() -> None:
    e = ev("x", "Run", date(2026, 11, 18))
    assert datetime.fromisoformat(e["start"]["dateTime"]).tzinfo is not None
    assert datetime.combine(date(2026, 11, 18), time(6), tzinfo=UTC)


def test_app_written_events_show_their_detail_and_distance(program: Program) -> None:
    planned = _planned(
        [
            ("2026-11-15", "strength_upper", "Gym – Upper"),
            ("2026-11-16", "intervals", "Quality run"),
            ("2026-11-18", "easy", "Easy run"),
            ("2026-11-20", "long", "Long run"),
        ]
    ).assign(
        target_distance_m=[None, None, 6000.0, 11000.0],
        detail=["Workout A – Upper Strength", "2 km warm-up + 6x400", "6 km easy", "11 km easy"],
    )
    kinds = _kinds(program, planned)
    assert kinds[0] == ("Sun", "strength", "Workout A – Upper Strength")
    assert kinds[1] == ("Mon", "quality", "2 km warm-up + 6x400")
    assert kinds[3] == ("Wed", "easy", "6 km easy")
    assert kinds[5] == ("Fri", "long", "11 km easy")
    week = program.week_for(WEEK2)
    assert week is not None
    sessions = calendar_sessions(program, week, planned)
    assert sessions is not None and sessions[5].km == 11.0 and sessions[3].km == 6.0
