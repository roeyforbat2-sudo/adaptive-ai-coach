"""Plan -> Google Calendar: event planning, diffing, the writer and the approval service.

Synthetic program and a fake Calendar service only; nothing touches Google.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from coach import secrets
from coach.calendar_plan import (
    DATE_KEY,
    DETAIL_KEY,
    KM_KEY,
    TAG_KEY,
    ExistingEvent,
    diff,
    plan_for_day,
    planned_events,
)
from coach.config import Settings
from coach.db import connect, migrate
from coach.integrations.gcal.sync import map_event
from coach.integrations.gcal.writer import apply_changes, event_body, to_existing
from coach.program import Program
from coach.services import calendar_write as cw
from tests.test_program import PROGRAM

TZ = ZoneInfo("Asia/Jerusalem")
SUN, MON, TUE, WED, THU, FRI, SAT = (date(2026, 11, 22) + timedelta(days=i) for i in range(7))

GYM = {
    "strength_workouts": [
        {
            "key": "A",
            "name": "Workout A – Upper Strength",
            "weekday": 6,
            "exercises": [{"name": "Bench Press", "sets": "4×5–6", "focus": "RIR 2"}],
        },
        {
            "key": "B",
            "name": "Workout B – Lower Body",
            "weekday": 1,
            "exercises": [{"name": "Back Squat", "sets": "3×5–6", "focus": ""}],
        },
        {"key": "C", "name": "Workout C – Upper Hypertrophy", "weekday": 3, "exercises": []},
    ],
    "strength_volume": [{"weeks": [1, 5], "upper": "Full volume", "lower": "80-90%"}],
}


@pytest.fixture
def program() -> Program:
    return Program.model_validate({**PROGRAM, **GYM})


# --- planning ----------------------------------------------------------------------------


def test_one_event_per_training_day_at_six(program: Program) -> None:
    events = planned_events(program, SUN, SAT, TZ)
    assert [(e.day, e.title) for e in events] == [
        (SUN, "Gym – Upper"),
        (MON, "Quality run"),
        (TUE, "Gym – Legs"),
        (WED, "Test run"),
        (THU, "Gym – Full body"),
        (FRI, "Long run"),  # Saturday is the rest day: no event
    ]
    assert all(e.start == datetime.combine(e.day, time(6), tzinfo=TZ) for e in events)
    assert all(e.end - e.start == timedelta(hours=1) for e in events)


def test_descriptions_hold_the_full_session(program: Program) -> None:
    gym = plan_for_day(program, SUN, TZ)
    long_run = plan_for_day(program, FRI, TZ)
    assert gym is not None and long_run is not None
    assert "Workout A – Upper Strength" in gym.description
    assert "• Bench Press: 4×5–6 (RIR 2)" in gym.description
    assert "Volume now: upper body: Full volume" in gym.description
    assert long_run.km == 12 and "Session: 12 km easy" in long_run.description
    assert "Pace: Easy / Recovery 6:05-6:40 /km" in long_run.description
    assert "Week 3 of 3" in long_run.description
    props = long_run.private_props()
    assert props == {TAG_KEY: "1", DATE_KEY: "2026-11-27", DETAIL_KEY: "12 km easy", KM_KEY: "12"}


def test_custom_time_and_race_day(program: Program) -> None:
    seven = planned_events(program, FRI, FRI, TZ, start_time=time(7, 30), minutes=90)
    assert seven[0].start.time() == time(7, 30) and seven[0].end.time() == time(9, 0)
    race = plan_for_day(program, date(2027, 2, 26), TZ)
    assert race is not None and race.title == "Race – Test Half" and race.km == 21.1
    assert planned_events(program, date(2027, 2, 27), date(2027, 3, 5), TZ) == []


# --- diff --------------------------------------------------------------------------------


def _existing(eid: str, title: str, day: date, **kw: Any) -> ExistingEvent:
    start = datetime.combine(day, time(6), tzinfo=TZ)
    return ExistingEvent(
        eid,
        f'"{eid}"',
        day,
        title,
        kw.get("description", ""),
        start,
        start + timedelta(minutes=kw.get("minutes", 60)),
        kw.get("private", {}),
    )


def test_diff_updates_same_kind_in_place_and_creates_the_rest(program: Program) -> None:
    planned = planned_events(program, SUN, SAT, TZ)
    existing = [
        _existing("g1", "מכון- UPPER", SUN),  # gym -> updated in place
        _existing("r1", "ריצה- איכות: 6x400", MON),  # run -> updated in place
        _existing("x1", "Dentist", TUE),  # not training: never touched
        _existing("r2", "ריצה- 6 קל", THU),  # a run on a gym day: left alone
        _existing("r3", "ריצה- 6 קל", SAT),  # rest day in the plan: left alone
    ]
    changes = diff(planned, existing)
    got = {(c.planned.day, c.action, c.existing.event_id if c.existing else None) for c in changes}
    assert got == {
        (SUN, "update", "g1"),
        (MON, "update", "r1"),
        (TUE, "create", None),
        (WED, "create", None),
        (THU, "create", None),
        (FRI, "create", None),
    }


def test_diff_is_idempotent_after_writing(program: Program) -> None:
    planned = planned_events(program, FRI, FRI, TZ)
    p = planned[0]
    written = ExistingEvent(
        "w1", '"w1"', p.day, p.title, p.description, p.start, p.end, p.private_props()
    )
    other = _existing("w0", "ריצה- 6 קל", FRI)
    (change,) = diff(planned, [other, written])
    assert change.action == "unchanged" and change.existing is written


# --- writer ------------------------------------------------------------------------------


class CalendarFake:
    """events().list/get/insert/patch(...).execute() over an in-memory calendar."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = {i["id"]: i for i in items}
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self._next: Any = None

    def events(self) -> CalendarFake:
        return self

    def list(self, **kw: Any) -> CalendarFake:
        self._next = lambda: {"items": list(self.items.values())}
        return self

    def get(self, calendarId: str, eventId: str, **kw: Any) -> CalendarFake:
        self._next = lambda: dict(self.items[eventId])
        return self

    def insert(self, calendarId: str, body: dict[str, Any], **kw: Any) -> CalendarFake:
        def run() -> dict[str, Any]:
            eid = f"new{len(self.items)}"
            self.items[eid] = {"id": eid, "etag": f'"{eid}"', "status": "confirmed", **body}
            self.writes.append(("insert", body))
            return {"id": eid}

        self._next = run
        return self

    def patch(self, calendarId: str, eventId: str, body: dict[str, Any], **kw: Any) -> CalendarFake:
        def run() -> dict[str, Any]:
            self.items[eventId] = {**self.items[eventId], **body, "etag": '"changed"'}
            self.writes.append(("patch", {"id": eventId, **body}))
            return {"id": eventId}

        self._next = run
        return self

    def execute(self) -> Any:
        return self._next()


def _raw(eid: str, title: str, day: date) -> dict[str, Any]:
    s = datetime.combine(day, time(6, 30), tzinfo=TZ)
    return {
        "id": eid,
        "etag": f'"{eid}"',
        "status": "confirmed",
        "summary": title,
        "start": {"dateTime": s.isoformat()},
        "end": {"dateTime": (s + timedelta(hours=1)).isoformat()},
    }


def test_event_body_uses_local_time_and_zone(program: Program) -> None:
    p = plan_for_day(program, FRI, TZ)
    assert p is not None
    body = event_body(p, "Asia/Jerusalem")
    assert body["start"] == {"dateTime": "2026-11-27T06:00:00", "timeZone": "Asia/Jerusalem"}
    assert body["end"]["dateTime"] == "2026-11-27T07:00:00"
    assert body["summary"] == "Long run" and body["extendedProperties"]["private"][KM_KEY] == "12"


def test_apply_creates_updates_and_skips_stale(program: Program) -> None:
    fake = CalendarFake([_raw("g1", "מכון- UPPER", SUN), _raw("r1", "ריצה- איכות", MON)])
    existing = [e for r in fake.items.values() if (e := to_existing(r, TZ))]
    changes = diff(planned_events(program, SUN, TUE, TZ), existing)
    fake.items["r1"]["summary"] = "ריצה- 8 קל"  # the athlete changed it after the preview
    result = apply_changes(fake, "primary", changes, "Asia/Jerusalem")
    assert (result.created, result.updated, result.stale, result.failed) == (1, 1, 1, 0)
    assert fake.items["g1"]["summary"] == "Gym – Upper"
    assert fake.items["g1"]["start"]["dateTime"] == "2026-11-22T06:00:00"
    assert fake.items["r1"]["summary"] == "ריצה- 8 קל"  # stale: untouched
    assert not any(kind == "delete" for kind, _ in fake.writes)


def test_written_events_read_back_with_detail_and_distance(program: Program) -> None:
    p = plan_for_day(program, FRI, TZ)
    assert p is not None
    raw = {"id": "e", "etag": '"e"', "status": "confirmed", **event_body(p, "Asia/Jerusalem")}
    raw["start"]["dateTime"] += "+02:00"
    raw["end"]["dateTime"] += "+02:00"
    mapped = map_event(raw, "primary", TZ)
    assert mapped is not None and mapped[1] is not None
    plan = mapped[1]
    assert plan.workout_type.value == "long" and plan.target_distance_m == 12_000
    assert plan.detail == "12 km easy" and plan.notes == "Long run"


def test_untagged_or_bad_private_props_are_ignored() -> None:
    raw = _raw("x", "Long run", FRI)
    raw["extendedProperties"] = {"private": {"coachKm": "999", "coachDetail": "x"}}
    plan = map_event(raw, "primary", TZ)[1]  # type: ignore[index]
    assert plan is not None and plan.target_distance_m is None and plan.detail is None
    raw["extendedProperties"]["private"][TAG_KEY] = "1"
    plan = map_event(raw, "primary", TZ)[1]  # type: ignore[index]
    assert plan is not None and plan.target_distance_m is None and plan.detail == "x"


# --- service -----------------------------------------------------------------------------


@pytest.fixture
def real_db(tmp_path: Path) -> Path:
    db = tmp_path / "coach.sqlite"
    conn = connect(db)
    migrate(conn)
    conn.close()
    return db


def test_service_preview_then_apply_records_audit(program: Program, real_db: Path) -> None:
    settings = Settings(_env_file=None, timezone="Asia/Jerusalem")
    fake = CalendarFake([_raw("g1", "מכון- UPPER", SUN)])
    preview = cw.preview(settings, program, SUN, TUE, service=fake)
    assert (preview.count("create"), preview.count("update")) == (2, 1)
    assert fake.writes == []  # previewing never writes
    result = cw.apply(real_db, settings, preview, service=fake)
    assert (result.created, result.updated) == (2, 1)
    again = cw.preview(settings, program, SUN, TUE, service=fake)
    assert again.pending == []  # nothing left to do
    conn = sqlite3.connect(real_db)
    (details,) = conn.execute(
        "SELECT details_json FROM audit_log WHERE action = 'calendar_write'"
    ).fetchone()
    conn.close()
    assert '"created": 2' in details


def test_service_requires_connections_and_refuses_demo_db(program: Program, tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(cw.CalendarWriteError, match="not connected"):
        cw.preview(settings, program, SUN, TUE)
    with pytest.raises(cw.CalendarWriteError, match="after the end"):
        cw.preview(settings, program, TUE, SUN, service=CalendarFake([]))
    preview = cw.preview(settings, program, SUN, SUN, service=CalendarFake([]))
    db = tmp_path / "x.sqlite"
    conn = connect(db)
    migrate(conn)
    conn.close()
    with pytest.raises(cw.CalendarWriteError, match="write access"):
        cw.apply(db, settings, preview)
    secrets.set_secret(secrets.GOOGLE_CALENDAR_WRITE_TOKEN, "fake-write-token")
    assert cw.write_connected()

    from coach.synthetic import generate, seed_database

    demo = tmp_path / "demo.sqlite"
    conn = connect(demo)
    migrate(conn)
    seed_database(conn, generate(7, end_date=date(2026, 11, 16), seed=1, tz=TZ))
    conn.close()
    with pytest.raises(cw.CalendarWriteError, match="demo data"):
        cw.apply(demo, settings, preview, service=CalendarFake([]))


def test_only_the_service_imports_the_writer() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "coach"
    importers = {
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "gcal.writer" in p.read_text(encoding="utf-8")
    }
    # Training events: preview + click. Review event: automatic, its own tagged event only.
    assert importers == {
        "services/calendar_write.py",
        "services/weekly_review.py",
        "services/daily_messages.py",
    }


def test_repeating_series_etag_changes_do_not_block_updates(program: Program) -> None:
    """Editing one instance of a weekly series changes every instance's etag at Google."""
    week2 = SUN - timedelta(days=7)  # program week 2
    fake = CalendarFake([_raw("s_1", "מכון- UPPER", SUN), _raw("s_2", "מכון- UPPER", week2)])
    existing = [e for r in fake.items.values() if (e := to_existing(r, TZ))]
    planned = [*planned_events(program, SUN, SUN, TZ), *planned_events(program, week2, week2, TZ)]
    changes = diff(planned, existing)
    original_patch = fake.patch

    def patch_and_bump_series(**kw: Any) -> CalendarFake:
        for item in fake.items.values():
            item["etag"] = '"series-bumped"'
        return original_patch(**kw)

    fake.patch = patch_and_bump_series  # type: ignore[method-assign]
    result = apply_changes(fake, "primary", changes, "Asia/Jerusalem")
    assert (result.updated, result.stale, result.errors) == (2, 0, [])
    assert fake.items["s_2"]["summary"] == "Gym – Upper"
