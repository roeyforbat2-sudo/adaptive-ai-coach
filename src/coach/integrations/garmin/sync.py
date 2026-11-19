"""Pull Garmin data for a date range and upsert it (idempotent).

    python -m coach.integrations.garmin.sync --days 30

This makes LIVE, authenticated, read-only requests to the Garmin Personal API using the key
stored in Windows Credential Manager. It writes to the configured database
(COACH_DB_PATH, default data/coach.sqlite) and refuses to mix real data into a database that
contains synthetic demo data.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from coach import secrets
from coach.config import get_settings
from coach.db import connect, migrate
from coach.db.connection import Connection, as_target
from coach.db.repositories import (
    ActivityRepository,
    AuditLogRepository,
    StrengthRepository,
    SyncStateRepository,
    WellnessRepository,
    has_synthetic_rows,
)
from coach.integrations.garmin.client import GarminClient, GarminError
from coach.integrations.garmin.mapping import (
    MappingReport,
    map_activity,
    map_wellness,
    strength_session_for,
)
from coach.logging import redact, setup_logging

log = logging.getLogger("coach.garmin.sync")
SOURCE = "garmin"
MAX_DAYS = 365


@dataclass
class SyncResult:
    start: date
    end: date
    activities: int = 0
    strength_sessions: int = 0
    wellness_days: int = 0
    skipped: int = 0
    unknown_type_keys: dict[str, int] = field(default_factory=dict)
    skip_reasons: dict[str, int] = field(default_factory=dict)
    requests: int = 0


def sync_garmin(
    conn: Connection, client: GarminClient, start: date, end: date, tz: ZoneInfo
) -> SyncResult:
    """Fetch everything first, then write; a failed fetch writes nothing."""
    report = MappingReport()
    try:
        activities = [
            a for raw in client.iter_activities(start, end) if (a := map_activity(raw, report))
        ]
        wellness = map_wellness(
            client.sleep_summaries(start, end),
            client.hrv_summaries(start, end),
            client.daily_summaries(start, end),
            report,
        )
    except GarminError as exc:
        SyncStateRepository(conn).record_failure(SOURCE, str(exc))
        raise

    result = SyncResult(
        start=start,
        end=end,
        skipped=report.skipped,
        unknown_type_keys=report.unknown_type_keys,
        skip_reasons=report.skip_reasons,
        requests=client.request_count,
    )
    activity_repo, strength_repo = ActivityRepository(conn), StrengthRepository(conn)
    for activity in activities:
        activity_id = activity_repo.upsert(activity)
        result.activities += 1
        session = strength_session_for(activity, activity.start_utc.astimezone(tz).date())
        if session is not None:
            strength_repo.upsert_session(session.model_copy(update={"activity_id": activity_id}))
            result.strength_sessions += 1
    wellness_repo = WellnessRepository(conn)
    for day in wellness:
        wellness_repo.upsert(day)
        result.wellness_days += 1

    SyncStateRepository(conn).record_success(SOURCE, cursor=end.isoformat())
    AuditLogRepository(conn).record(
        "user",
        "garmin_sync",
        SOURCE,
        details={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "activities": result.activities,
            "wellness_days": result.wellness_days,
            "skipped": result.skipped,
            "requests": result.requests,
        },
    )
    return result


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m coach.integrations.garmin.sync",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days", type=int, default=30, help=f"days back from today (1-{MAX_DAYS})")
    parser.add_argument("--db", type=as_target, default=settings.db_target)
    args = parser.parse_args(argv)
    setup_logging(settings.log_level)

    if not 1 <= args.days <= MAX_DAYS:
        log.error("--days must be between 1 and %d", MAX_DAYS)
        return 2
    if not secrets.has_secret(secrets.GARMIN_API_KEY):
        log.error("No Garmin key stored. Run: python -m coach.integrations.garmin.set_key")
        return 2

    end = datetime.now(settings.tz).date()
    start = end - timedelta(days=args.days - 1)
    conn = connect(args.db)
    try:
        migrate(conn)
        if has_synthetic_rows(conn):
            log.error(
                "refusing to sync: %s contains synthetic demo data. Use a separate DB "
                "(default data/coach.sqlite).",
                args.db,
            )
            return 2
        with GarminClient(
            lambda: secrets.get_secret(secrets.GARMIN_API_KEY), base_url=settings.garmin_base_url
        ) as client:
            result = sync_garmin(conn, client, start, end, settings.tz)
    except GarminError as exc:
        log.error("Garmin sync failed: %s", redact(str(exc)))
        return 1
    finally:
        conn.close()

    log.info(
        "Garmin sync %s to %s: %d activities (%d strength), %d wellness days, %d skipped, "
        "%d requests -> %s",
        result.start,
        result.end,
        result.activities,
        result.strength_sessions,
        result.wellness_days,
        result.skipped,
        result.requests,
        args.db,
    )
    if result.skip_reasons:
        log.info("skipped records by reason: %s", result.skip_reasons)
    if result.unknown_type_keys:
        log.info("activity types mapped to 'other': %s", result.unknown_type_keys)
    return 0


if __name__ == "__main__":
    sys.exit(main())
