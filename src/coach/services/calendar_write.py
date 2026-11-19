"""Plan -> Google Calendar, with a preview and an explicit approval step.

1. `preview()` reads the calendar (read-only grant) and diffs it with the program:
   which events would be created, updated in place, or are already up to date.
2. The athlete reviews the list in the app and clicks the approve button.
3. `apply()` writes exactly that list with the separate write grant. Each update is
   skipped if the event changed since the preview (etag check). Nothing is deleted.
   The run is recorded in the audit log and the calendar is re-synced afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any

from coach.calendar_plan import Change, diff, planned_events
from coach.config import Settings
from coach.db import connect, migrate
from coach.db.connection import DbTarget
from coach.db.repositories import AuditLogRepository, has_synthetic_rows
from coach.integrations.gcal.writer import WriteResult, apply_changes, fetch_existing
from coach.program import Program

__all__ = ["CalendarWriteError", "Change", "Preview", "WriteResult", "apply", "preview"]


class CalendarWriteError(RuntimeError):
    pass


@dataclass(frozen=True)
class Preview:
    calendar_id: str
    first: date
    last: date
    changes: list[Change]

    @property
    def pending(self) -> list[Change]:
        return [c for c in self.changes if c.action != "unchanged"]

    def count(self, action: str) -> int:
        return sum(c.action == action for c in self.changes)


def read_connected() -> bool:
    from coach.integrations.gcal.auth import calendar_access

    return calendar_access()


def write_connected() -> bool:
    from coach.integrations.gcal.auth import calendar_access

    return calendar_access(write=True)


def _service(*, write: bool) -> Any:
    from coach.integrations.gcal.auth import calendar_service

    return calendar_service(write=write)


def preview(
    settings: Settings,
    program: Program,
    first: date,
    last: date,
    *,
    start_time: time = time(6, 0),
    minutes: int = 60,
    service: Any = None,
) -> Preview:
    if first > last:
        raise CalendarWriteError("The start date is after the end date.")
    if service is None:
        if not read_connected():
            raise CalendarWriteError("Google Calendar is not connected.")
        service = _service(write=False)
    planned = planned_events(
        program, first, last, settings.tz, start_time=start_time, minutes=minutes
    )
    existing = fetch_existing(service, settings.google_calendar_write_id, first, last, settings.tz)
    return Preview(settings.google_calendar_write_id, first, last, diff(planned, existing))


def apply(
    db_path: DbTarget, settings: Settings, approved: Preview, *, service: Any = None
) -> WriteResult:
    """Write the approved preview. The caller must only call this after the user's click."""
    conn = connect(db_path)
    try:
        migrate(conn)
        if has_synthetic_rows(conn):
            raise CalendarWriteError("This database holds demo data; writing is refused.")
        if service is None:
            if not write_connected():
                raise CalendarWriteError("Calendar write access is not connected.")
            service = _service(write=True)
        result = apply_changes(service, approved.calendar_id, approved.pending, settings.timezone)
        AuditLogRepository(conn).record(
            "user",
            "calendar_write",
            "gcal",
            approved.calendar_id,
            details={
                "from": approved.first.isoformat(),
                "to": approved.last.isoformat(),
                "created": result.created,
                "updated": result.updated,
                "stale": result.stale,
                "failed": result.failed,
            },
        )
        return result
    finally:
        conn.close()
