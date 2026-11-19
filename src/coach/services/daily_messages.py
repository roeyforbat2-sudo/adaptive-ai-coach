"""Daily calendar messages: the 05:30 morning brief and the 17:30 check-in reminder.

Automatic by the athlete's choice (2026-11-18), like the Saturday review, and limited the
same way: each message is ONE event carrying the app's private tag, so these writes can only
create or update the app's own events, never training events, never deletions.
- Morning brief: "☀️ Today: …" 05:30-05:45 on training days (tag adaptiveCoachBrief=<date>),
  posted once per day between 04:30 and 09:00, by the 05:25 scheduled task or the dashboard.
- Check-in reminder: one repeating daily event "✅ Daily check-in" 17:30-17:35
  (tag adaptiveCoachCheckin=daily), created once.
Both pop up on the phone at the event time (reminder at 0 minutes) and show as free time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from coach.config import Settings
from coach.db import connect, migrate
from coach.db.connection import DbTarget, db_exists
from coach.db.repositories import AuditLogRepository, SyncStateRepository, has_synthetic_rows
from coach.engine.safety import assess
from coach.integrations.gcal.writer import ensure_recurring_tagged_event, upsert_tagged_event
from coach.logging import redact
from coach.morning_brief import MorningBrief, build_morning_brief
from coach.program import Program, load_program
from coach.services.dashboard import build_view, load_dashboard_data
from coach.week_plan import scheduled_session

BRIEF_SOURCE, BRIEF_TAG = "morning_brief", "adaptiveCoachBrief"
BRIEF_TIME, BRIEF_MINUTES = time(5, 30), 15
BRIEF_WINDOW = (time(4, 30), time(9, 0))
CHECKIN_SOURCE, CHECKIN_TAG = "checkin_reminder", "adaptiveCoachCheckin"
CHECKIN_TIME, CHECKIN_MINUTES = time(17, 30), 5
APP_URL = "http://localhost:8501"
POPUP_NOW = {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}]}


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str


def _state(db_path: DbTarget, source: str) -> str | None:
    if not db_exists(db_path):
        return None
    conn = connect(db_path, read_only=True)
    try:
        row = conn.execute("SELECT cursor FROM sync_state WHERE source = ?", (source,)).fetchone()
    except Exception:  # table missing on a brand-new database
        return None
    finally:
        conn.close()
    return row[0] if row else None


def brief_posted_for(db_path: DbTarget) -> date | None:
    cursor = _state(db_path, BRIEF_SOURCE)
    try:
        return date.fromisoformat(cursor) if cursor else None
    except ValueError:
        return None


def build_brief(
    db_path: DbTarget, settings: Settings, program: Program, day: date
) -> MorningBrief | None:
    data = load_dashboard_data(db_path, settings.tz, day)
    view = build_view(data, 8)
    safety = assess(view.recovery, data.activities, data.feedback, data.as_of)
    session = scheduled_session(program, data.planned, day)
    return build_morning_brief(
        program, session, view.recovery, safety, data.feedback, data.activities
    )


def _write_access() -> bool:
    from coach.integrations.gcal.auth import calendar_access

    return calendar_access(write=True)


def _service(service: Any) -> Any:
    if service is not None:
        return service
    from coach.integrations.gcal.auth import calendar_service

    return calendar_service(write=True)


def _writable(db_path: DbTarget, service: Any) -> str | None:
    """Why posting is not possible right now, or None."""
    if service is None and not _write_access():
        return "Calendar write access is not connected."
    conn = connect(db_path)
    try:
        migrate(conn)
        if has_synthetic_rows(conn):
            return "This database holds demo data; posting is refused."
    finally:
        conn.close()
    return None


def _record(db_path: DbTarget, source: str, cursor: str, action: str, what: str) -> None:
    conn = connect(db_path)
    try:
        SyncStateRepository(conn).record_success(source, cursor=cursor)
        AuditLogRepository(conn).record(
            "scheduler", what, "gcal", cursor, details={"action": action}
        )
    finally:
        conn.close()


def publish_brief(
    db_path: DbTarget,
    settings: Settings,
    day: date,
    *,
    program: Program | None = None,
    service: Any = None,
) -> Outcome:
    program = program or load_program(settings.program_file)
    if program is None:
        return Outcome(False, "No training program loaded.")
    problem = _writable(db_path, service)
    if problem:
        return Outcome(False, problem)
    brief = build_brief(db_path, settings, program, day)
    if brief is None:
        _record(db_path, BRIEF_SOURCE, day.isoformat(), "none", "morning_brief_post")
        return Outcome(True, f"No workout on {day:%a %d %b}: no brief needed.")
    start = datetime.combine(day, BRIEF_TIME)
    body = {
        "summary": brief.title,
        "description": brief.as_text()[:8000],
        "start": {"dateTime": start.isoformat(), "timeZone": settings.timezone},
        "end": {
            "dateTime": (start + timedelta(minutes=BRIEF_MINUTES)).isoformat(),
            "timeZone": settings.timezone,
        },
        "transparency": "transparent",
        "reminders": POPUP_NOW,
    }
    tz = settings.tz
    window = (
        datetime.combine(day, time(0), tzinfo=tz),
        datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz),
    )
    try:
        action = upsert_tagged_event(
            _service(service),
            settings.google_calendar_write_id,
            (BRIEF_TAG, day.isoformat()),
            body,
            window,
        )
    except Exception as exc:  # network/auth: report, never crash the caller
        return Outcome(False, redact(f"Posting the morning brief failed ({type(exc).__name__})."))
    _record(db_path, BRIEF_SOURCE, day.isoformat(), action, "morning_brief_post")
    return Outcome(True, f"Morning brief {action} in your calendar.")


def brief_due(now: datetime) -> bool:
    return BRIEF_WINDOW[0] <= now.time() < BRIEF_WINDOW[1]


def publish_brief_if_due(
    db_path: DbTarget, settings: Settings, now: datetime | None = None, *, service: Any = None
) -> Outcome | None:
    """Post today's brief once, between 04:30 and 09:00. None when there is nothing to do."""
    now = now or datetime.now(settings.tz)
    if not brief_due(now) or brief_posted_for(db_path) == now.date():
        return None
    if service is None and not _write_access():
        return None
    return publish_brief(db_path, settings, now.date(), service=service)


def ensure_checkin_reminder(
    db_path: DbTarget, settings: Settings, *, service: Any = None, force: bool = False
) -> Outcome | None:
    """Create the daily 17:30 reminder once. None if it already exists (unless `force`)."""
    if not force and _state(db_path, CHECKIN_SOURCE):
        return None
    if service is None and not _write_access():
        return None
    problem = _writable(db_path, service)
    if problem:
        return Outcome(False, problem)
    today = datetime.now(settings.tz).date()
    start = datetime.combine(today, CHECKIN_TIME)
    body = {
        "summary": "✅ Daily check-in (10 seconds)",
        "description": (
            f"Open the coach on your PC ({APP_URL}) → Today → Daily check-in.\n"
            "How hard did the workout feel, soreness, tiredness, any pain or illness.\n"
            "It makes tomorrow's brief, the workout analysis and the weekly review smarter."
        ),
        "start": {"dateTime": start.isoformat(), "timeZone": settings.timezone},
        "end": {
            "dateTime": (start + timedelta(minutes=CHECKIN_MINUTES)).isoformat(),
            "timeZone": settings.timezone,
        },
        "recurrence": ["RRULE:FREQ=DAILY"],
        "transparency": "transparent",
        "reminders": POPUP_NOW,
    }
    try:
        action = ensure_recurring_tagged_event(
            _service(service), settings.google_calendar_write_id, (CHECKIN_TAG, "daily"), body
        )
    except Exception as exc:
        return Outcome(
            False, redact(f"Creating the check-in reminder failed ({type(exc).__name__}).")
        )
    _record(db_path, CHECKIN_SOURCE, "daily", action, "checkin_reminder")
    return Outcome(True, f"Daily 17:30 check-in reminder {action}.")
