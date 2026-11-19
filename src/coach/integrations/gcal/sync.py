"""Read calendar events (read-only) and turn training events into planned workouts.

    python -m coach.integrations.gcal.sync [--past 7] [--ahead 21]

Verified against the official Events: list reference (developers.google.com/workspace/
calendar/api/v3/reference/events/list, 2026-11-17): GET calendars/{calendarId}/events with
timeMin (exclusive bound on end time), timeMax (exclusive bound on start time), RFC 3339 with
offset; singleEvents=true expands recurrences and enables orderBy=startTime; paging via
nextPageToken. Partial response (`fields`) limits what Google returns: we never request
descriptions, attendees, locations, or conference data.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from coach import secrets
from coach.config import get_settings
from coach.db import connect, migrate
from coach.db.connection import Connection, as_target
from coach.db.repositories import (
    AuditLogRepository,
    CalendarEventRepository,
    CalendarEventRow,
    PlannedWorkoutRepository,
    SyncStateRepository,
    has_synthetic_rows,
)
from coach.domain.models import PlannedWorkout, PlanSource
from coach.integrations.gcal.auth import GoogleSetupError, calendar_access, calendar_service
from coach.integrations.gcal.classify import classify
from coach.logging import redact, setup_logging

log = logging.getLogger("coach.gcal.sync")
SOURCE = "gcal"
EVENT_FIELDS = (
    "items(id,etag,status,summary,start,end,eventType,transparency,extendedProperties/private),"
    "nextPageToken"
)
NON_TRAINING_TYPES = {"outOfOffice", "focusTime", "workingLocation", "birthday", "fromGmail"}


@dataclass
class CalendarSyncResult:
    events: int = 0
    training: int = 0
    removed: int = 0
    skipped: int = 0
    calendars: list[str] = field(default_factory=list)


def fetch_events(
    service: Any,
    calendar_id: str,
    start: datetime,
    end: datetime,
    *,
    fields: str | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        response = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
                fields=fields or EVENT_FIELDS,
                pageToken=page_token,
            )
            .execute()
        )
        items.extend(i for i in response.get("items", []) if isinstance(i, dict))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def _parse_when(when: object, tz: ZoneInfo) -> tuple[datetime, bool] | None:
    """Event start/end -> (aware UTC datetime, all_day)."""
    if not isinstance(when, dict):
        return None
    try:
        if isinstance(when.get("dateTime"), str):
            return datetime.fromisoformat(when["dateTime"]).astimezone(UTC), False
        if isinstance(when.get("date"), str):
            day = date.fromisoformat(when["date"])
            return datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC), True
    except ValueError:
        return None
    return None


# The app's own message events (morning brief, weekly review, check-in reminder) are NOT
# training sessions, even though their titles mention "Gym" or "Long run".
APP_MESSAGE_TAGS = ("adaptiveCoachBrief", "adaptiveCoachReview", "adaptiveCoachCheckin")


def _is_app_message(raw: dict[str, Any]) -> bool:
    ext = raw.get("extendedProperties")
    private = ext.get("private") if isinstance(ext, dict) else None
    return isinstance(private, dict) and any(tag in private for tag in APP_MESSAGE_TAGS)


def plan_source_id(calendar_id: str, event_id: str) -> str:
    digest = hashlib.sha256(f"{calendar_id}|{event_id}".encode()).hexdigest()[:24]
    return f"gcal-{digest}"


def map_event(
    raw: dict[str, Any], calendar_id: str, tz: ZoneInfo
) -> tuple[CalendarEventRow, PlannedWorkout | None] | None:
    event_id = raw.get("id")
    start, end = _parse_when(raw.get("start"), tz), _parse_when(raw.get("end"), tz)
    if (
        not isinstance(event_id, str)
        or start is None
        or end is None
        or raw.get("status") == "cancelled"
    ):
        return None
    (start_utc, all_day), (end_utc, _) = start, end
    title = raw.get("summary") if isinstance(raw.get("summary"), str) else None
    kind = (
        None
        if raw.get("eventType") in NON_TRAINING_TYPES or _is_app_message(raw)
        else classify(title)
    )
    row = CalendarEventRow(
        calendar_id=calendar_id,
        event_id=event_id[:200],
        etag=raw.get("etag") if isinstance(raw.get("etag"), str) else None,
        start_utc=start_utc,
        end_utc=end_utc,
        all_day=all_day,
        busy=raw.get("transparency") != "transparent",
        is_training=kind is not None,
        title=title[:200] if kind is not None and title else None,
    )
    if kind is None:
        return row, None
    sport, workout_type = kind
    seconds = int((end_utc - start_utc).total_seconds())
    km, detail = _coach_props(raw)
    plan = PlannedWorkout(
        source=PlanSource.CALENDAR,
        source_id=plan_source_id(calendar_id, event_id),
        date=start_utc.astimezone(tz).date(),
        sport=sport,
        workout_type=workout_type,
        target_duration_s=seconds if not all_day and 0 < seconds <= 6 * 3600 else None,
        target_distance_m=km * 1000 if km is not None else None,
        notes=row.title,
        detail=detail,
    )
    return row, plan


def _coach_props(raw: dict[str, Any]) -> tuple[float | None, str | None]:
    """Distance and session text the app stored on events it wrote (untrusted, validated)."""
    ext = raw.get("extendedProperties")
    private = ext.get("private") if isinstance(ext, dict) else None
    if not isinstance(private, dict) or private.get("adaptiveCoach") != "1":
        return None, None
    km: float | None = None
    try:
        km = float(private.get("coachKm", ""))
    except ValueError:
        km = None
    if km is not None and not 0 < km <= 100:
        km = None
    detail = private.get("coachDetail")
    return km, detail[:300] if isinstance(detail, str) and detail.strip() else None


def sync_calendar(
    conn: Connection,
    service: Any,
    calendar_ids: list[str],
    start: datetime,
    end: datetime,
    tz: ZoneInfo,
) -> CalendarSyncResult:
    """Fetch all calendars first (a failure writes nothing), then replace the cached window."""
    fetched = {cid: fetch_events(service, cid, start, end) for cid in calendar_ids}
    result = CalendarSyncResult(calendars=list(calendar_ids))
    events_repo, plans_repo = CalendarEventRepository(conn), PlannedWorkoutRepository(conn)
    keep_plans: set[str] = set()
    for calendar_id, raw_events in fetched.items():
        rows: list[CalendarEventRow] = []
        for raw in raw_events:
            mapped = map_event(raw, calendar_id, tz)
            if mapped is None:
                result.skipped += 1
                continue
            row, plan = mapped
            rows.append(row)
            if plan is not None:
                plans_repo.upsert(plan)
                keep_plans.add(plan.source_id)
                result.training += 1
        result.removed += events_repo.replace_window(calendar_id, start, end, rows)
        result.events += len(rows)
    result.removed += plans_repo.delete_missing(
        PlanSource.CALENDAR,
        start.astimezone(tz).date(),
        (end - timedelta(seconds=1)).astimezone(tz).date(),  # end is exclusive
        keep_plans,
    )
    SyncStateRepository(conn).record_success(SOURCE, cursor=end.isoformat())
    AuditLogRepository(conn).record(
        "user",
        "gcal_sync",
        SOURCE,
        details={
            "calendars": len(calendar_ids),
            "events": result.events,
            "training": result.training,
            "removed": result.removed,
        },
    )
    return result


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m coach.integrations.gcal.sync",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--past", type=int, default=7, help="days back (0-60)")
    parser.add_argument("--ahead", type=int, default=21, help="days ahead (1-60)")
    parser.add_argument("--db", type=as_target, default=settings.db_target)
    args = parser.parse_args(argv)
    setup_logging(settings.log_level)
    if not (0 <= args.past <= 60 and 1 <= args.ahead <= 60):
        log.error("--past must be 0-60 and --ahead 1-60")
        return 2
    if not calendar_access():
        log.error("Google Calendar is not connected. Run: python -m coach.integrations.gcal.auth")
        return 2

    today = datetime.now(settings.tz).date()
    start = datetime.combine(today - timedelta(days=args.past), time(0), tzinfo=settings.tz)
    end = datetime.combine(today + timedelta(days=args.ahead + 1), time(0), tzinfo=settings.tz)
    conn = connect(args.db)
    try:
        migrate(conn)
        if has_synthetic_rows(conn):
            log.error("refusing to sync: %s contains synthetic demo data", args.db)
            return 2
        service = calendar_service()
        result = sync_calendar(conn, service, settings.calendar_ids, start, end, settings.tz)
    except (GoogleSetupError, secrets.SecretNotFoundError) as exc:
        log.error("Google Calendar setup problem: %s", exc)
        return 2
    except Exception as exc:  # googleapiclient/auth errors: record sanitized and stop
        SyncStateRepository(conn).record_failure(SOURCE, f"{type(exc).__name__}: {exc}")
        log.error("Google Calendar sync failed: %s", redact(f"{type(exc).__name__}: {exc}"))
        return 1
    finally:
        conn.close()
    log.info(
        "Calendar sync %s..%s: %d events (%d training -> planned workouts), %d removed, "
        "%d skipped, calendars=%d",
        start.date(),
        end.date(),
        result.events,
        result.training,
        result.removed,
        result.skipped,
        len(result.calendars),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
