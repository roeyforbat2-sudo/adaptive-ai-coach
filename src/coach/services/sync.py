"""In-app syncing for the dashboard (read-only, free APIs; no AI).

Runs the same Garmin / Google Calendar syncs as the CLI commands, into the configured
database. Only runs for sources that are connected (credentials in the keyring); never
prompts, never writes to any external service. Errors are returned as short, redacted text.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from coach import secrets
from coach.config import Settings
from coach.db import connect, migrate
from coach.db.connection import Connection, DatabaseError, DbTarget, db_exists
from coach.db.repositories import SyncStateRepository, has_synthetic_rows
from coach.logging import redact
from coach.timeutil import from_db

GARMIN, GCAL = "garmin", "gcal"


@dataclass(frozen=True)
class SyncOutcome:
    source: str
    ok: bool
    message: str
    changed: bool = False


def connected(source: str) -> bool:
    if source == GCAL:
        from coach.integrations.gcal.auth import calendar_access

        return calendar_access()
    return secrets.has_secret(secrets.GARMIN_API_KEY)


def last_success(db_path: DbTarget, source: str) -> datetime | None:
    if not db_exists(db_path):
        return None
    conn = connect(db_path, read_only=True)
    try:
        row = conn.execute(
            "SELECT last_success_utc FROM sync_state WHERE source = ?", (source,)
        ).fetchone()
    except DatabaseError:  # table missing on a brand-new database
        return None
    finally:
        conn.close()
    return from_db(row[0]) if row and row[0] else None


def is_stale(
    db_path: DbTarget, source: str, max_age: timedelta, now: datetime | None = None
) -> bool:
    last = last_success(db_path, source)
    return last is None or (now or datetime.now(UTC)) - last > max_age


def _calendar_fingerprint(conn: Connection) -> str:
    rows = conn.execute(
        "SELECT date, workout_type, target_duration_s, target_distance_m, notes, detail "
        "FROM planned_workouts "
        "WHERE source = 'calendar' ORDER BY date, source_id"
    ).fetchall()
    return hashlib.sha256(repr([tuple(r) for r in rows]).encode()).hexdigest()


def _open(db_path: DbTarget) -> Connection:
    conn = connect(db_path)
    migrate(conn)
    if has_synthetic_rows(conn):
        conn.close()
        raise RuntimeError("this database holds demo data; syncing real data into it is refused")
    return conn


def sync_calendar(
    db_path: DbTarget,
    settings: Settings,
    *,
    past_days: int = 14,
    ahead_days: int = 60,
    service: Any = None,
) -> SyncOutcome:
    if not connected(GCAL):
        return SyncOutcome(GCAL, False, "Google Calendar is not connected.")
    from coach.integrations.gcal.auth import calendar_service
    from coach.integrations.gcal.sync import sync_calendar as run

    tz = settings.tz
    today = datetime.now(tz).date()
    start = datetime.combine(today - timedelta(days=past_days), time(0), tzinfo=tz)
    end = datetime.combine(today + timedelta(days=ahead_days + 1), time(0), tzinfo=tz)
    try:
        conn = _open(db_path)
    except RuntimeError as exc:
        return SyncOutcome(GCAL, False, str(exc))
    try:
        before = _calendar_fingerprint(conn)
        if service is None:
            service = calendar_service()
        result = run(conn, service, settings.calendar_ids, start, end, tz)
        changed = _calendar_fingerprint(conn) != before
        return SyncOutcome(
            GCAL, True, f"{result.training} training sessions in your calendar", changed
        )
    except Exception as exc:  # network / auth errors: record and report, never crash the page
        SyncStateRepository(conn).record_failure(GCAL, f"{type(exc).__name__}: {exc}")
        return SyncOutcome(GCAL, False, redact(f"Calendar sync failed ({type(exc).__name__})."))
    finally:
        conn.close()


def sync_garmin(
    db_path: DbTarget, settings: Settings, *, days: int = 7, client: Any = None
) -> SyncOutcome:
    if not connected(GARMIN):
        return SyncOutcome(GARMIN, False, "Garmin is not connected.")
    from coach.integrations.garmin.client import GarminClient, GarminError
    from coach.integrations.garmin.sync import sync_garmin as run

    today = datetime.now(settings.tz).date()
    try:
        conn = _open(db_path)
    except RuntimeError as exc:
        return SyncOutcome(GARMIN, False, str(exc))
    own_client = client is None
    try:
        if client is None:
            client = GarminClient(
                lambda: secrets.get_secret(secrets.GARMIN_API_KEY),
                base_url=settings.garmin_base_url,
            )
        before = conn.execute("SELECT COUNT(*), MAX(updated_utc) FROM activities").fetchone()
        result = run(conn, client, today - timedelta(days=days - 1), today, settings.tz)
        after = conn.execute("SELECT COUNT(*), MAX(updated_utc) FROM activities").fetchone()
        return SyncOutcome(
            GARMIN,
            True,
            f"{result.activities} activities, {result.wellness_days} days of wellness",
            tuple(before) != tuple(after),
        )
    except GarminError as exc:
        return SyncOutcome(GARMIN, False, redact(str(exc)))
    except Exception as exc:
        SyncStateRepository(conn).record_failure(GARMIN, f"{type(exc).__name__}: {exc}")
        return SyncOutcome(GARMIN, False, f"Garmin sync failed ({type(exc).__name__}).")
    finally:
        if own_client and client is not None:
            client.close()
        conn.close()
