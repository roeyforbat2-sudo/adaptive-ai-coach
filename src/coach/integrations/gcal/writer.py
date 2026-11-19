"""The ONLY code that writes to Google Calendar. Called only by `services.calendar_write`,
after the athlete approved a specific list of changes in the app.

Verified against the official Calendar API v3 reference (developers.google.com/workspace/
calendar/api/v3/reference/events, 2026-11-18):
- events.insert: POST calendars/{calendarId}/events, body = Event resource;
- events.patch:  PATCH calendars/{calendarId}/events/{eventId}; fields not supplied stay
  unchanged (arrays would be replaced; we send none);
- events.get returns the event resource including its `etag`;
- start/end: {"dateTime": RFC3339, "timeZone": IANA}; the offset may be omitted when
  timeZone is given; `extendedProperties.private` is a string->string map;
- scope https://www.googleapis.com/auth/calendar.events ("View and edit events on all your
  calendars") is sufficient for all three.

Safety: an event is re-read right before it is patched, and skipped as "stale" if what the
athlete sees (title, description, start, end) changed since the preview, or it was cancelled.
The etag alone is not used: for instances of a REPEATING event, Google changes every
instance's etag as soon as one instance is edited (found 2026-11-18 on the athlete's weekly
gym series), which would wrongly mark the rest of the series as stale. Nothing is deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from coach.calendar_plan import Change, ExistingEvent, PlannedEvent
from coach.integrations.gcal.sync import fetch_events

EXISTING_FIELDS = (
    "items(id,etag,status,summary,description,start,end,eventType,extendedProperties/private),"
    "nextPageToken"
)
EVENT_FIELDS = "id,etag,status,summary,description,start,end,eventType,extendedProperties/private"
MAX_TEXT = 8000


@dataclass
class WriteResult:
    created: int = 0
    updated: int = 0
    stale: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def _when(raw: object) -> datetime | None:
    if isinstance(raw, dict) and isinstance(raw.get("dateTime"), str):
        try:
            return datetime.fromisoformat(raw["dateTime"])
        except ValueError:
            return None
    return None  # all-day or malformed: never matched or changed


def to_existing(raw: dict[str, Any], tz: ZoneInfo) -> ExistingEvent | None:
    start, end = _when(raw.get("start")), _when(raw.get("end"))
    event_id = raw.get("id")
    if (
        not isinstance(event_id, str)
        or start is None
        or end is None
        or raw.get("status") == "cancelled"
        or raw.get("eventType", "default") != "default"
    ):
        return None
    ext = raw.get("extendedProperties")
    private = ext.get("private") if isinstance(ext, dict) else None
    props = (
        {str(k)[:64]: str(v)[:1024] for k, v in private.items() if isinstance(v, str)}
        if isinstance(private, dict)
        else {}
    )
    summary, description = raw.get("summary"), raw.get("description")
    return ExistingEvent(
        event_id=event_id[:200],
        etag=raw.get("etag") if isinstance(raw.get("etag"), str) else None,
        day=start.astimezone(tz).date(),
        title=summary[:500] if isinstance(summary, str) else "",
        description=description[:MAX_TEXT] if isinstance(description, str) else "",
        start=start.astimezone(UTC),
        end=end.astimezone(UTC),
        private=props,
    )


def fetch_existing(
    service: Any, calendar_id: str, first: date, last: date, tz: ZoneInfo
) -> list[ExistingEvent]:
    start = datetime.combine(first, time(0), tzinfo=tz)
    end = datetime.combine(last + timedelta(days=1), time(0), tzinfo=tz)
    raw = fetch_events(service, calendar_id, start, end, fields=EXISTING_FIELDS)
    return [e for r in raw if (e := to_existing(r, tz)) is not None]


def _changed_since_preview(seen: ExistingEvent, raw: dict[str, Any], tz: ZoneInfo) -> bool:
    now = to_existing(raw, tz)
    return now is None or (now.title, now.description, now.start, now.end) != (
        seen.title,
        seen.description,
        seen.start,
        seen.end,
    )


def event_body(p: PlannedEvent, tz_name: str) -> dict[str, Any]:
    return {
        "summary": p.title,
        "description": p.description[:MAX_TEXT],
        "start": {"dateTime": p.start.replace(tzinfo=None).isoformat(), "timeZone": tz_name},
        "end": {"dateTime": p.end.replace(tzinfo=None).isoformat(), "timeZone": tz_name},
        "extendedProperties": {"private": p.private_props()},
    }


def apply_changes(
    service: Any, calendar_id: str, changes: list[Change], tz_name: str
) -> WriteResult:
    result = WriteResult()
    tz = ZoneInfo(tz_name)
    events = service.events()
    for change in changes:
        if change.action == "unchanged":
            continue
        body = event_body(change.planned, tz_name)
        try:
            if change.action == "create":
                events.insert(calendarId=calendar_id, body=body, fields="id").execute()
                result.created += 1
                continue
            existing = change.existing
            assert existing is not None
            current = events.get(
                calendarId=calendar_id, eventId=existing.event_id, fields=EVENT_FIELDS
            ).execute()
            if _changed_since_preview(existing, current, tz):
                result.stale += 1
                continue
            events.patch(
                calendarId=calendar_id, eventId=existing.event_id, body=body, fields="id"
            ).execute()
            result.updated += 1
        except Exception as exc:  # one failed event must not stop the rest
            result.failed += 1
            result.errors.append(f"{change.planned.day:%a %d %b}: {type(exc).__name__}")
    return result


def upsert_tagged_event(
    service: Any,
    calendar_id: str,
    tag: tuple[str, str],
    body: dict[str, Any],
    window: tuple[datetime, datetime],
) -> str:
    """Create or update the ONE event the app tagged with `tag` (private property) in `window`.

    Used for the automatic weekly-review event. It can only find, and so only change, an
    event carrying the app's own private tag: the athlete's events are never touched.
    Returns "created" or "updated".
    """
    key, value = tag
    events = service.events()
    found = (
        events.list(
            calendarId=calendar_id,
            privateExtendedProperty=f"{key}={value}",
            timeMin=window[0].isoformat(),
            timeMax=window[1].isoformat(),
            singleEvents=True,
            fields="items(id,status)",
        )
        .execute()
        .get("items", [])
    )
    ids = [i["id"] for i in found if isinstance(i, dict) and i.get("status") != "cancelled"]
    body = {**body, "extendedProperties": {"private": {key: value}}}
    if ids:
        events.patch(calendarId=calendar_id, eventId=ids[0], body=body, fields="id").execute()
        for extra in ids[1:]:  # duplicates of the app's OWN tagged event (e.g. a race)
            events.delete(calendarId=calendar_id, eventId=extra).execute()
        return "updated"
    events.insert(calendarId=calendar_id, body=body, fields="id").execute()
    return "created"


def ensure_recurring_tagged_event(
    service: Any, calendar_id: str, tag: tuple[str, str], body: dict[str, Any]
) -> str:
    """Create the app's ONE repeating event with this private tag, or update it if it exists.

    Used for the daily 17:30 check-in reminder (body carries an RRULE). Like
    `upsert_tagged_event`, it can only find and change the app's own tagged event.
    """
    key, value = tag
    events = service.events()
    found = (
        events.list(
            calendarId=calendar_id,
            privateExtendedProperty=f"{key}={value}",
            singleEvents=False,  # the series itself, not its daily instances
            fields="items(id,status)",
        )
        .execute()
        .get("items", [])
    )
    ids = [i["id"] for i in found if isinstance(i, dict) and i.get("status") != "cancelled"]
    body = {**body, "extendedProperties": {"private": {key: value}}}
    if ids:
        events.patch(calendarId=calendar_id, eventId=ids[0], body=body, fields="id").execute()
        for extra in ids[1:]:  # duplicates of the app's OWN tagged event (e.g. a race)
            events.delete(calendarId=calendar_id, eventId=extra).execute()
        return "updated"
    events.insert(calendarId=calendar_id, body=body, fields="id").execute()
    return "created"
