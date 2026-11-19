"""Daily check-in, morning brief, and their calendar messages (synthetic data, fake calendar)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from coach.checkins import between, for_day, source_id
from coach.config import Settings
from coach.db import connect, migrate
from coach.db.read_models import feedback_frame
from coach.engine.safety import NO_INTENSITY, Guardrail, SafetyAssessment
from coach.morning_brief import build_morning_brief
from coach.program import DaySession, Program, session_for
from coach.services import checkin as checkin_service
from coach.services import daily_messages as dm
from coach.workout_analysis import checkin_points
from tests.test_calendar_write import GYM
from tests.test_coach_feedback import FULL_WEEK, WEEK2, _acts, _recovery
from tests.test_program import PROGRAM

TZ = ZoneInfo("Asia/Jerusalem")
CLEAR = SafetyAssessment("green", [])
LOW = SafetyAssessment("amber", [Guardrail(NO_INTENSITY, "caution", "HRV below range.")])


@pytest.fixture
def program() -> Program:
    return Program.model_validate({**PROGRAM, **GYM})


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "coach.sqlite"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


def _feedback(db: Path) -> pd.DataFrame:
    conn = connect(db, read_only=True)
    try:
        return feedback_frame(conn)
    finally:
        conn.close()


# --- check-in storage ---------------------------------------------------------------------


def test_checkin_saves_once_per_day_and_updates(db: Path) -> None:
    day = date(2026, 11, 18)
    first = checkin_service.CheckIn(day, rpe=6, soreness=2, fatigue=3, pain=None, ill=False)
    checkin_service.save(db, first)
    again = checkin_service.CheckIn(day, rpe=7, soreness=5, fatigue=4, pain="Calf", ill=False)
    checkin_service.save(db, again)
    fb = _feedback(db)
    assert len(fb) == 1 and fb.iloc[0]["source_id"] == source_id(day)
    got = for_day(fb, day)
    assert got == {
        "rpe": 7,
        "soreness": 5,
        "fatigue": 4,
        "pain": True,
        "pain_location": "Calf",
        "ill": False,
        "activity_id": None,
    }
    assert for_day(fb, day + timedelta(days=1)) is None
    assert [c["date"] for c in between(fb, day - timedelta(days=2), day)] == [day]


def test_pain_checkin_is_stored_for_the_safety_rules(db: Path) -> None:
    day = date(2026, 11, 18)
    checkin_service.save(
        db, checkin_service.CheckIn(day, rpe=None, soreness=3, fatigue=2, pain="Knee", ill=False)
    )
    row = _feedback(db).iloc[0]
    # engine.safety reads pain_flag / illness_flag from this frame (tested in tests/engine).
    assert bool(row["pain_flag"]) and row["pain_location"] == "Knee" and not row["illness_flag"]


# --- check-in in the workout analysis -------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "rpe", "tone", "text"),
    [
        ("easy", 7, "warn", "felt hard"),
        ("long", 3, "good", "exactly right"),
        ("quality", 9, "note", "next two days easy"),
        ("quality", 4, "note", "conservative"),
        ("easy", 5, "good", "matches the session"),
    ],
)
def test_effort_against_the_session(kind: str, rpe: int, tone: str, text: str) -> None:
    (point,) = checkin_points(
        {"rpe": rpe, "soreness": 1, "fatigue": 1, "pain": False, "ill": False}, kind, "run"
    )
    assert point.tone == tone and text in point.text


def test_soreness_pain_and_illness_points() -> None:
    pts = checkin_points(
        {"rpe": None, "soreness": 7, "pain": True, "pain_location": "Calf", "ill": True},
        "easy",
        "run",
    )
    texts = " ".join(p.text for p in pts)
    assert "Soreness 7/10" in texts and "pain (Calf)" in texts and "feeling ill" in texts
    assert checkin_points(None, "easy", "run") == []


# --- check-ins in the weekly review ----------------------------------------------------------


def test_weekly_review_uses_checkins(program: Program) -> None:
    from coach.weekly_review import build_weekly_review

    fb = pd.DataFrame(
        [
            {
                "date": pd.Timestamp(d),
                "source_id": source_id(d),
                "rpe": rpe,
                "soreness": 7,
                "fatigue": 8,
                "pain_flag": d == WEEK2[3],
                "pain_location": "Shin" if d == WEEK2[3] else None,
                "illness_flag": False,
                "activity_id": None,
            }
            for d, rpe in ((WEEK2[3], 7), (WEEK2[5], 6))
        ]
    )
    r = build_weekly_review(
        program, _acts(FULL_WEEK), None, _recovery(7.0), CLEAR, WEEK2[6], feedback=fb
    )
    assert r is not None
    assert ("Check-ins", "2 of 7", "days") in r.stats
    watch = " ".join(i.text for i in r.watch)
    assert "Easy runs felt hard: average effort 6.5/10" in watch
    assert "Pain reported on 1 day(s) (Shin)" in watch
    assert any("Pain this week" in i.text for i in r.suggestions)


# --- morning brief ------------------------------------------------------------------------------


def test_brief_for_each_kind_of_day(program: Program) -> None:
    rec = _recovery(7.1)
    gym = build_morning_brief(program, session_for(program, WEEK2[0]), rec, CLEAR, None, None)
    assert gym is not None and gym.title == "☀️ Today: Strength: Upper body (strength)"
    assert "Workout A – Upper Strength" in gym.plan
    assert any(line.startswith("Bench Press: 4×5–6") for line in gym.plan)
    quality = build_morning_brief(program, session_for(program, WEEK2[1]), rec, CLEAR, None, None)
    assert quality is not None and any("Warm-up: 2 km easy" in p for p in quality.plan)
    long_run = build_morning_brief(program, session_for(program, WEEK2[5]), rec, CLEAR, None, None)
    assert long_run is not None and long_run.title == "☀️ Today: Long run (or rest) · 11 km"
    assert any("Pace: Easy / Recovery 6:05-6:40 /km" in p for p in long_run.plan)
    assert long_run.readiness[0].startswith("Last night: 7.1 h of sleep")
    rest = DaySession(WEEK2[6], "rest", "Rest day", "", None)
    assert build_morning_brief(program, rest, rec, CLEAR, None, None) is None


def test_brief_adjusts_for_safety_and_yesterdays_checkin(program: Program) -> None:
    fb = pd.DataFrame(
        [
            {
                "date": pd.Timestamp(WEEK2[0]),
                "source_id": source_id(WEEK2[0]),
                "rpe": 6,
                "soreness": 7,
                "fatigue": 8,
                "pain_flag": False,
                "pain_location": None,
                "illness_flag": False,
                "activity_id": None,
            }
        ]
    )
    b = build_morning_brief(program, session_for(program, WEEK2[1]), None, LOW, fb, None)
    assert b is not None
    texts = " ".join(a.text for a in b.adjustments)
    assert "Swap the quality session for an easy run" in texts
    assert "Soreness was 7/10 yesterday" in texts and "tired yesterday (8/10)" in texts
    assert "Read the adjustments" in b.headline
    assert "THE PLAN" in b.as_text() and "TODAY'S ADJUSTMENTS" in b.as_text()


def test_brief_flags_easy_runs_that_were_too_fast(program: Program) -> None:
    acts = _acts([(WEEK2[1], "run", 6.0, 33, "Easy"), (WEEK2[3], "run", 6.0, 34, "Easy")])
    b = build_morning_brief(program, session_for(program, WEEK2[5]), None, CLEAR, None, acts)
    assert b is not None and b.focus is not None and "stay in 6:05–6:40/km" in b.focus.text


# --- calendar messages -----------------------------------------------------------------------


class MessageCalendar:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self._run: Any = None

    def events(self) -> MessageCalendar:
        return self

    def list(self, **kw: Any) -> MessageCalendar:
        key, value = kw["privateExtendedProperty"].split("=")
        self._run = lambda: {
            "items": [
                {"id": i, "status": "confirmed"}
                for i, e in self.items.items()
                if e["extendedProperties"]["private"].get(key) == value
            ]
        }
        return self

    def insert(self, calendarId: str, body: dict[str, Any], **kw: Any) -> MessageCalendar:
        def run() -> dict[str, Any]:
            self.items[f"e{len(self.items)}"] = body
            self.calls.append("insert")
            return {}

        self._run = run
        return self

    def patch(
        self,
        calendarId: str,
        eventId: str,
        body: dict[str, Any],
        **kw: Any,
    ) -> MessageCalendar:
        def run() -> dict[str, Any]:
            self.items[eventId] = body
            self.calls.append("patch")
            return {}

        self._run = run
        return self

    def execute(self) -> Any:
        return self._run()


def _settings(program: Program, tmp_path: Path) -> Settings:
    f = tmp_path / "program.json"
    f.write_text(program.model_dump_json(), encoding="utf-8")
    return Settings(_env_file=None, timezone="Asia/Jerusalem", program_file=f)


def test_brief_posted_once_in_the_morning_window(
    program: Program, db: Path, tmp_path: Path
) -> None:
    settings, cal = _settings(program, tmp_path), MessageCalendar()
    day = WEEK2[1]
    at = lambda h, m=0: datetime.combine(day, time(h, m), tzinfo=TZ)  # noqa: E731
    assert dm.publish_brief_if_due(db, settings, at(4, 0), service=cal) is None  # too early
    out = dm.publish_brief_if_due(db, settings, at(5, 25), service=cal)
    assert out is not None and out.ok
    assert dm.publish_brief_if_due(db, settings, at(6, 0), service=cal) is None  # done today
    (event,) = cal.items.values()
    assert event["summary"].startswith("☀️ Today: Quality run")
    assert event["start"] == {"dateTime": f"{day}T05:30:00", "timeZone": "Asia/Jerusalem"}
    assert event["reminders"]["overrides"] == [{"method": "popup", "minutes": 0}]
    assert event["extendedProperties"]["private"] == {"adaptiveCoachBrief": day.isoformat()}
    assert dm.brief_posted_for(db) == day
    assert dm.publish_brief_if_due(db, settings, at(9, 30), service=cal) is None  # too late


def test_no_brief_event_on_a_rest_day(program: Program, db: Path, tmp_path: Path) -> None:
    settings, cal = _settings(program, tmp_path), MessageCalendar()
    out = dm.publish_brief(db, settings, date(2030, 1, 1), service=cal)  # outside the program
    assert out.ok and "no brief" in out.message and cal.items == {}


def test_checkin_reminder_created_once(program: Program, db: Path, tmp_path: Path) -> None:
    settings, cal = _settings(program, tmp_path), MessageCalendar()
    out = dm.ensure_checkin_reminder(db, settings, service=cal)
    assert out is not None and out.ok
    assert dm.ensure_checkin_reminder(db, settings, service=cal) is None  # already there
    forced = dm.ensure_checkin_reminder(db, settings, service=cal, force=True)
    assert forced is not None and cal.calls == ["insert", "patch"]
    (event,) = cal.items.values()
    assert event["recurrence"] == ["RRULE:FREQ=DAILY"]
    assert event["start"]["dateTime"].endswith("T17:30:00")
    assert "localhost:8501" in event["description"]


def test_messages_need_write_access(program: Program, db: Path, tmp_path: Path) -> None:
    settings = _settings(program, tmp_path)
    assert not dm.publish_brief(db, settings, WEEK2[1]).ok
    now = datetime.combine(WEEK2[1], time(5, 30), tzinfo=TZ)
    assert dm.publish_brief_if_due(db, settings, now) is None
    assert dm.ensure_checkin_reminder(db, settings) is None


def test_launch_routes_morning_brief(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from coach import launch

    monkeypatch.setattr(launch, "LOGS", tmp_path)
    monkeypatch.setattr(launch, "post_morning_brief", lambda: 0)
    assert launch.main(["morning-brief"]) == 0
    assert (tmp_path / "morning-brief.log").is_file()


def test_duplicate_tagged_events_are_cleaned_up() -> None:
    """Two posters racing once left two copies of the brief: the next update keeps one."""
    from coach.integrations.gcal.writer import upsert_tagged_event

    class Fake(MessageCalendar):
        def delete(self, calendarId: str, eventId: str, **kw: Any) -> Fake:
            def run() -> dict[str, Any]:
                del self.items[eventId]
                self.calls.append("delete")
                return {}

            self._run = run
            return self

    cal = Fake()
    tag = {"extendedProperties": {"private": {"adaptiveCoachBrief": "2026-11-19"}}}
    cal.items = {"a": {"summary": "old", **tag}, "b": {"summary": "old", **tag}}
    cal.items["x"] = {"summary": "someone else's", "extendedProperties": {"private": {}}}
    window = (datetime(2026, 11, 19, tzinfo=TZ), datetime(2026, 11, 20, tzinfo=TZ))
    action = upsert_tagged_event(
        cal, "primary", ("adaptiveCoachBrief", "2026-11-19"), {"summary": "new"}, window
    )
    assert action == "updated" and cal.calls == ["patch", "delete"]
    assert sorted(cal.items) == ["a", "x"] and cal.items["a"]["summary"] == "new"


@pytest.mark.parametrize(
    "tag", ["adaptiveCoachBrief", "adaptiveCoachReview", "adaptiveCoachCheckin"]
)
def test_app_message_events_are_not_training(tag: str) -> None:
    from coach.integrations.gcal.sync import map_event
    from tests.integrations.gcal.test_gcal import ev

    raw = ev("m1", "☀️ Today: Gym – Full body", date(2026, 11, 19), hour=5, minutes=15)
    raw["extendedProperties"] = {"private": {tag: "2026-11-19"}}
    row, plan = map_event(raw, "primary", TZ)  # type: ignore[misc]
    assert plan is None and not row.is_training and row.title is None
    plain = ev("m2", "Gym – Full body", date(2026, 11, 19))
    assert map_event(plain, "primary", TZ)[1] is not None  # type: ignore[index]
