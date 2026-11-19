"""The Saturday review: built from your data and posted to Google Calendar at 20:00.

Automatic by the athlete's choice (2026-11-18): once the calendar write grant exists, the
app posts (or refreshes) ONE event per week, "Weekly review · Week N of M", on Saturday
20:00-20:15, tagged privately so it can only ever update that same event. Training events
are never touched by this path. It runs from the dashboard (when open) and from
`python -m coach.review --publish --if-due` (for a scheduled task). Free; no AI.
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
from coach.integrations.gcal.writer import upsert_tagged_event
from coach.logging import redact
from coach.program import Program, load_program
from coach.services.dashboard import build_view, load_dashboard_data
from coach.timeutil import from_db
from coach.weekly_review import WeeklyReview, build_weekly_review

SOURCE = "weekly_review"
TAG_KEY = "adaptiveCoachReview"
POST_TIME = time(20, 0)
POST_MINUTES = 15
CATCH_UP = timedelta(hours=36)  # a review missed on Saturday is still posted until Mon 08:00


@dataclass(frozen=True)
class PublishOutcome:
    ok: bool
    message: str
    saturday: date | None = None


def _write_access() -> bool:
    from coach.integrations.gcal.auth import calendar_access

    return calendar_access(write=True)


def saturday_of(day: date) -> date:
    """The Saturday that ends the (Sunday-Saturday) week containing `day`."""
    return day + timedelta(days=(5 - day.weekday()) % 7)


def due_saturday(now: datetime) -> date | None:
    """The Saturday whose review should be posted by `now` (Sat 20:00 .. Mon 08:00)."""
    local = now.date()
    sat = local - timedelta(days=(local.weekday() - 5) % 7)
    post_at = datetime.combine(sat, POST_TIME, tzinfo=now.tzinfo)
    return sat if post_at <= now <= post_at + CATCH_UP else None


def _state(db_path: DbTarget) -> tuple[str | None, str | None]:
    if not db_exists(db_path):
        return None, None
    conn = connect(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT cursor, last_success_utc FROM sync_state WHERE source = ?", (SOURCE,)
        ).fetchone()
    except Exception:  # table missing on a brand-new database
        return None, None
    finally:
        conn.close()
    return (row[0], row[1]) if row else (None, None)


def last_posted(db_path: DbTarget) -> date | None:
    cursor, _ = _state(db_path)
    try:
        return date.fromisoformat(cursor) if cursor else None
    except ValueError:
        return None


def last_post_time(db_path: DbTarget) -> datetime | None:
    _, when = _state(db_path)
    return from_db(when) if when else None


def build(
    db_path: DbTarget,
    settings: Settings,
    program: Program,
    saturday: date,
    as_of: date | None = None,
) -> WeeklyReview | None:
    """The review of the week ending `saturday`, as of `as_of` (default: that Saturday)."""
    as_of = min(as_of or saturday, saturday)
    # Same inputs as CoachService.snapshot, without importing the (dormant) AI code: the
    # Anthropic SDK is large and made the window-less scheduled task hang while loading.
    data = load_dashboard_data(db_path, settings.tz, as_of)
    view = build_view(data, 8)
    safety = assess(view.recovery, data.activities, data.feedback, data.as_of)
    return build_weekly_review(
        program,
        data.activities,
        data.planned,
        view.recovery,
        safety,
        saturday,
        as_of=as_of,
        feedback=data.feedback,
    )


def event_body(review: WeeklyReview, saturday: date, tz_name: str) -> dict[str, Any]:
    start = datetime.combine(saturday, POST_TIME)
    end = start + timedelta(minutes=POST_MINUTES)
    return {
        "summary": f"📋 {review.title}",
        "description": review.as_text()[:8000],
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
        "transparency": "transparent",  # shows as free time
    }


def publish(
    db_path: DbTarget,
    settings: Settings,
    saturday: date,
    *,
    program: Program | None = None,
    service: Any = None,
    as_of: date | None = None,
) -> PublishOutcome:
    """Create/update the review event for `saturday`.

    With `as_of` before that Saturday it is a PREVIEW ("your week so far"): the event is
    written, but the week is not marked as posted, so the final review still replaces it
    on Saturday at 20:00.
    """
    final = as_of is None or as_of >= saturday
    program = program or load_program(settings.program_file)
    if program is None:
        return PublishOutcome(False, "No training program loaded.")
    if service is None and not _write_access():
        return PublishOutcome(False, "Calendar write access is not connected.")
    conn = connect(db_path)
    try:
        migrate(conn)
        if has_synthetic_rows(conn):
            return PublishOutcome(False, "This database holds demo data; posting is refused.")
    finally:
        conn.close()
    review = build(db_path, settings, program, saturday, as_of=as_of)
    if review is None:
        return PublishOutcome(False, f"{saturday:%d %b} is outside your training program.")
    tz = settings.tz
    window = (
        datetime.combine(saturday, time(0), tzinfo=tz),
        datetime.combine(saturday + timedelta(days=1), time(0), tzinfo=tz),
    )
    conn = connect(db_path)
    try:
        if service is None:
            from coach.integrations.gcal.auth import calendar_service

            service = calendar_service(write=True)
        action = upsert_tagged_event(
            service,
            settings.google_calendar_write_id,
            (TAG_KEY, saturday.isoformat()),
            event_body(review, saturday, settings.timezone),
            window,
        )
        if final:
            SyncStateRepository(conn).record_success(SOURCE, cursor=saturday.isoformat())
        AuditLogRepository(conn).record(
            "scheduler" if final else "user",
            "weekly_review_post",
            "gcal",
            saturday.isoformat(),
            details={"action": action, "final": final},
        )
        kind = "Weekly review" if final else "Weekly review preview (week so far)"
        return PublishOutcome(True, f"{kind} {action} in your calendar.", saturday)
    except Exception as exc:  # network/auth errors: record and report, never crash the page
        SyncStateRepository(conn).record_failure(SOURCE, f"{type(exc).__name__}: {exc}")
        return PublishOutcome(
            False, redact(f"Posting the weekly review failed ({type(exc).__name__}).")
        )
    finally:
        conn.close()


def publish_if_due(
    db_path: DbTarget, settings: Settings, now: datetime | None = None, *, service: Any = None
) -> PublishOutcome | None:
    """Post this Saturday's review once it is due and not yet posted. None: nothing to do."""
    now = now or datetime.now(settings.tz)
    sat = due_saturday(now)
    if sat is None or last_posted(db_path) == sat:
        return None
    if service is None and not _write_access():
        return None
    return publish(db_path, settings, sat, service=service)
