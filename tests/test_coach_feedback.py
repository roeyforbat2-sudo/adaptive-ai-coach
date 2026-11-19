"""Workout analysis ("Coach's take"), the weekly review, and its Saturday calendar post.

Synthetic program, activities and recovery only; a fake Calendar service; no network.
"""

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
from coach.engine.safety import NO_INTENSITY, Guardrail, SafetyAssessment
from coach.program import DaySession, Program, session_for
from coach.services import weekly_review as wr
from coach.weekly_review import build_weekly_review, session_text
from coach.workout_analysis import analyze_workout, te_label
from tests.test_calendar_write import GYM
from tests.test_program import PROGRAM

TZ = ZoneInfo("Asia/Jerusalem")
CLEAR = SafetyAssessment("green", [])
LOW = SafetyAssessment("amber", [Guardrail(NO_INTENSITY, "caution", "HRV below range.")])
WEEK2 = [date(2026, 11, 15) + timedelta(days=i) for i in range(7)]  # Sun..Sat, program week 2


@pytest.fixture
def program() -> Program:
    return Program.model_validate({**PROGRAM, **GYM})


def _facts(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "activity_id": 1,
        "date": "2026-11-20",
        "family": "run",
        "sport": "run",
        "duration_min": 63.0,
        "distance_km": 11.0,
        "pace": "5:42/km",
        "pace_s_per_km": 342.0,
        "avg_hr": 147.0,
        "aerobic_te": 3.9,
        "anaerobic_te": 0.0,
        "planned": {
            "workout_type": "long",
            "target_duration_min": 60,
            "duration_vs_target_pct": 105,
        },
        "vs_last_28_days": None,
        "recovery_that_morning": None,
    }
    return {**base, **kw}


# --- workout analysis ------------------------------------------------------------------


def test_te_scale() -> None:
    assert [te_label(v) for v in (0.3, 1.5, 2.5, 3.9, 4.2, 5.0, None)] == [
        "no benefit",
        "minor",
        "maintaining",
        "improving",
        "highly improving",
        "overreaching",
        None,
    ]


def test_long_run_too_fast_is_flagged_against_the_easy_zone(program: Program) -> None:
    fri = session_for(program, date(2026, 11, 20))  # 11 km easy long run
    a = analyze_workout(_facts(), fri, program=program, safety=CLEAR)
    assert a.tag == "Faster than planned" and a.tone == "warn"
    assert a.headline.startswith("Long run (or rest) · 11.0 km at 5:42/km — 23 s/km faster")
    texts = " ".join(p.text for p in a.points)
    assert "Distance on plan: 11.0 of 11 km." in texts
    assert "faster than your easy range (6:05–6:40/km)" in texts
    assert ("Distance", "11.0 km", "of 11 km") in a.stats


def test_easy_pace_in_range_and_short_run(program: Program) -> None:
    wed = session_for(program, date(2026, 11, 18))  # 6 km easy
    ok = analyze_workout(
        _facts(distance_km=6.1, pace="6:20/km", pace_s_per_km=380.0, aerobic_te=2.6),
        wed,
        program=program,
    )
    assert ok.tag == "On target" and ok.tone == "good"
    assert any("right in your easy range" in p.text for p in ok.points)
    short = analyze_workout(
        _facts(distance_km=4.0, pace="6:20/km", pace_s_per_km=380.0), wed, program=program
    )
    assert short.tag == "Shorter than planned" and short.tone == "warn"


def test_mixed_session_is_not_judged_on_average_pace(program: Program) -> None:
    mixed = DaySession(date(2026, 11, 20), "long", "Long run", "12 km: 6 easy + 4 at HMP", 12)
    a = analyze_workout(_facts(distance_km=12.0), mixed, program=program)
    assert not any("easy range" in p.text for p in a.points)
    assert any("Mixed session" in p.text for p in a.points)


def test_extra_session_and_gym(program: Program) -> None:
    extra = analyze_workout(_facts(planned=None), None, program=program)
    assert extra.tag == "Extra session" and extra.tone == "note"
    gym_day = DaySession(
        date(2026, 11, 17), "strength", "Gym: Legs", "Workout B – Lower Body", None
    )
    gym = analyze_workout(
        _facts(
            family="strength",
            sport="strength",
            distance_km=None,
            pace=None,
            pace_s_per_km=None,
            duration_min=40.0,
            aerobic_te=0.3,
            planned={
                "workout_type": "strength_lower",
                "target_duration_min": 60,
                "duration_vs_target_pct": 66,
            },
        ),
        gym_day,
        program=program,
    )
    assert gym.title == "Gym – Legs" and gym.tag == "On target"
    assert gym.points[0].text == "Planned: Workout B – Lower Body."


def test_next_step_follows_calendar_and_safety(program: Program) -> None:
    fri = session_for(program, date(2026, 11, 20))
    rest = DaySession(date(2026, 11, 21), "rest", "Rest day", "", None)
    a = analyze_workout(_facts(), fri, program=program, next_session=rest)
    assert a.next_step is not None and "rest day" in a.next_step.text
    legs = DaySession(date(2026, 11, 22), "strength", "Gym: Legs", "Workout B – Lower Body", None)
    b = analyze_workout(_facts(), fri, program=program, next_session=legs)
    assert b.next_step is not None and "after a hard run" in b.next_step.text
    run = DaySession(date(2026, 11, 23), "quality", "Quality run", "6x400", 7)
    c = analyze_workout(_facts(), fri, program=program, safety=LOW, next_session=run)
    assert c.next_step is not None and c.next_step.tone == "warn"
    assert "Recovery markers are low" in c.next_step.text


# --- weekly review ---------------------------------------------------------------------


def _acts(rows: list[tuple[date, str, float, float, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": i + 1,
                "date": pd.Timestamp(d),
                "family": f,
                "distance_m": km * 1000 if km else None,
                "duration_s": mins * 60,
                "name": name,
                "start_utc": datetime.combine(d, time(4), tzinfo=UTC),
            }
            for i, (d, f, km, mins, name) in enumerate(rows)
        ]
    )


def _recovery(sleep: float, low_hrv_days: int = 0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": [pd.Timestamp(d) for d in WEEK2],
            "sleep_h": [sleep] * 7,
            "hrv_status": ["below normal range"] * low_hrv_days
            + ["within normal range"] * (7 - low_hrv_days),
            "resting_hr": [42.0] * 7,
            "rhr_base": [42.0] * 7,
        }
    )


FULL_WEEK = [
    (WEEK2[0], "strength", 0, 45, "Gym"),
    (WEEK2[1], "run", 7.0, 42, "6x400"),
    (WEEK2[2], "strength", 0, 45, "Gym"),
    (WEEK2[3], "run", 6.0, 38, "Easy"),
    (WEEK2[4], "strength", 0, 45, "Gym"),
    (WEEK2[5], "run", 11.0, 70, "Long"),
]


def test_strong_week_and_next_week_plan(program: Program) -> None:
    r = build_weekly_review(program, _acts(FULL_WEEK), None, _recovery(7.2), CLEAR, WEEK2[6])
    assert r is not None
    assert r.headline == "Strong week: 6 of 6 sessions and 24 km run."
    assert r.tone == "good" and r.title == "Weekly review · Week 2 of 3"
    assert ("Sessions", "6 of 6", "all done") in r.stats
    assert any("Long run done: 11 km" in i.text for i in r.went_well)
    assert r.next_week is not None and r.next_week.week == 3
    assert any(s.kind == "long" and s.date.weekday() == 4 for s in r.next_sessions)
    assert not any(s.date.weekday() == 5 and s.kind == "long" for s in r.next_sessions)
    assert any("Go ahead with Friday's long run: 12 km" in i.text for i in r.suggestions)
    assert any("test run" in i.text for i in r.suggestions)  # week 3 has the 5 km test
    text = r.as_text()
    assert "NEXT WEEK · Week 3" in text and "SUGGESTIONS FOR NEXT WEEK" in text


def test_missed_long_run_caps_next_week_and_flags_recovery(program: Program) -> None:
    short_long = [*FULL_WEEK[:5], (WEEK2[5], "run", 8.0, 50, "Long")]
    r = build_weekly_review(
        program, _acts(short_long), None, _recovery(6.0, low_hrv_days=3), LOW, WEEK2[6]
    )
    assert r is not None
    tips = " ".join(i.text for i in r.suggestions)
    assert "Recovery markers are low" in tips
    assert "Keep Friday's long run to about 9.5 km (plan 12)" in tips
    assert "7+ hours of sleep" in tips
    watch = " ".join(i.text for i in r.watch)
    assert "HRV below your normal range on 3 day(s)" in watch


def test_mid_week_review_does_not_call_future_sessions_missed(program: Program) -> None:
    r = build_weekly_review(
        program, _acts(FULL_WEEK[:3]), None, _recovery(7.0), CLEAR, WEEK2[6], as_of=WEEK2[2]
    )
    assert r is not None
    assert r.headline.startswith("Your week so far: 3 of 3 sessions")
    assert not any("Missed" in i.text for i in r.watch)


def test_session_text_does_not_repeat() -> None:
    d = date(2026, 11, 16)
    assert session_text(DaySession(d, "quality", "Quality run", "Quality: 6x400", 7)) == (
        "Quality run: 6x400"
    )
    assert session_text(DaySession(d, "strength", "Gym: Legs", "Legs", None)) == "Gym – Legs"


# --- Saturday post -----------------------------------------------------------------------


def test_due_saturday_window() -> None:
    sat = date(2026, 11, 21)
    at = lambda d, h, m=0: datetime.combine(d, time(h, m), tzinfo=TZ)  # noqa: E731
    assert wr.due_saturday(at(sat, 19, 59)) is None
    assert wr.due_saturday(at(sat, 20, 0)) == sat
    assert wr.due_saturday(at(sat + timedelta(days=1), 22)) == sat
    assert wr.due_saturday(at(sat + timedelta(days=2), 9)) is None
    assert wr.saturday_of(date(2026, 11, 18)) == sat and wr.saturday_of(sat) == sat


class ReviewCalendar:
    """events().list/insert/patch for the tagged review event."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self._run: Any = None

    def events(self) -> ReviewCalendar:
        return self

    def list(self, **kw: Any) -> ReviewCalendar:
        key, value = kw["privateExtendedProperty"].split("=")
        self._run = lambda: {
            "items": [
                {"id": i, "status": "confirmed"}
                for i, e in self.items.items()
                if e["extendedProperties"]["private"].get(key) == value
            ]
        }
        return self

    def insert(self, calendarId: str, body: dict[str, Any], **kw: Any) -> ReviewCalendar:
        def run() -> dict[str, Any]:
            self.items[f"r{len(self.items)}"] = body
            self.calls.append("insert")
            return {}

        self._run = run
        return self

    def patch(
        self, calendarId: str, eventId: str, body: dict[str, Any], **kw: Any
    ) -> ReviewCalendar:
        def run() -> dict[str, Any]:
            self.items[eventId] = body
            self.calls.append("patch")
            return {}

        self._run = run
        return self

    def execute(self) -> Any:
        return self._run()


def test_publish_creates_then_updates_one_review_event(
    program: Program, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "coach.sqlite"
    conn = connect(db)
    migrate(conn)
    conn.close()
    program_file = tmp_path / "program.json"
    program_file.write_text(program.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, timezone="Asia/Jerusalem", program_file=program_file)
    cal = ReviewCalendar()
    sat = WEEK2[6]
    assert wr.publish(db, settings, sat, service=cal).ok
    assert wr.publish(db, settings, sat, service=cal).ok
    assert cal.calls == ["insert", "patch"] and len(cal.items) == 1
    (event,) = cal.items.values()
    assert event["summary"] == "📋 Weekly review · Week 2 of 3"
    assert event["start"] == {"dateTime": "2026-11-21T20:00:00", "timeZone": "Asia/Jerusalem"}
    assert event["extendedProperties"]["private"] == {"adaptiveCoachReview": "2026-11-21"}
    assert "SUGGESTIONS FOR NEXT WEEK" in event["description"]
    assert wr.last_posted(db) == sat
    # Already posted: nothing to do on the next check that weekend.
    now = datetime.combine(sat, time(21), tzinfo=TZ)
    assert wr.publish_if_due(db, settings, now, service=cal) is None
    conn = sqlite3.connect(db)
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'weekly_review_post'"
    ).fetchone() == (2,)
    conn.close()


def test_publish_needs_write_access_and_real_data(program: Program, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, program_file=tmp_path / "missing.json")
    db = tmp_path / "c.sqlite"
    assert not wr.publish(db, settings, WEEK2[6]).ok  # no program
    program_file = tmp_path / "program.json"
    program_file.write_text(program.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, program_file=program_file)
    out = wr.publish(db, settings, WEEK2[6])
    assert not out.ok and "write access" in out.message
    now = datetime.combine(WEEK2[6], time(20, 30), tzinfo=TZ)
    assert wr.publish_if_due(db, settings, now) is None  # not connected: silently skipped
    secrets.set_secret(secrets.GOOGLE_CALENDAR_WRITE_TOKEN, "fake")

    from coach.synthetic import generate, seed_database

    demo = tmp_path / "demo.sqlite"
    conn = connect(demo)
    migrate(conn)
    seed_database(conn, generate(7, end_date=date(2026, 11, 16), seed=1, tz=TZ))
    conn.close()
    out = wr.publish(demo, settings, WEEK2[6], service=ReviewCalendar())
    assert not out.ok and "demo data" in out.message


def test_review_cli_prints_the_week(
    program: Program,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from coach import review as cli
    from coach.config import get_settings

    db = tmp_path / "coach.sqlite"
    conn = connect(db)
    migrate(conn)
    conn.close()
    program_file = tmp_path / "program.json"
    program_file.write_text(program.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("COACH_DB_PATH", str(db))
    monkeypatch.setenv("COACH_PROGRAM_FILE", str(program_file))
    monkeypatch.setenv("COACH_TIMEZONE", "Asia/Jerusalem")
    get_settings.cache_clear()
    try:
        assert cli.main(["--date", "2026-11-18"]) == 0
        assert "Weekly review · Week 2 of 3" in capsys.readouterr().out
        assert cli.main(["--date", "2030-01-01"]) == 2
    finally:
        get_settings.cache_clear()


def test_mid_week_preview_does_not_block_the_saturday_post(
    program: Program, tmp_path: Path
) -> None:
    db = tmp_path / "coach.sqlite"
    conn = connect(db)
    migrate(conn)
    conn.close()
    program_file = tmp_path / "program.json"
    program_file.write_text(program.model_dump_json(), encoding="utf-8")
    settings = Settings(_env_file=None, timezone="Asia/Jerusalem", program_file=program_file)
    cal = ReviewCalendar()
    sat = WEEK2[6]
    out = wr.publish(db, settings, sat, service=cal, as_of=WEEK2[3])
    assert out.ok and "preview" in out.message
    assert wr.last_posted(db) is None  # not final
    (event,) = cal.items.values()
    assert "Your week so far" in event["description"]
    now = datetime.combine(sat, time(20, 5), tzinfo=TZ)
    final = wr.publish_if_due(db, settings, now, service=cal)
    assert final is not None and final.ok and wr.last_posted(db) == sat
    assert cal.calls == ["insert", "patch"] and len(cal.items) == 1
