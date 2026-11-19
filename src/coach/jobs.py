"""Scheduled jobs for the cloud (Cloud Run jobs started by Cloud Scheduler).

    python -m coach.jobs morning-brief   # daily 05:25: sync, post today's brief if due
    python -m coach.jobs weekly-review   # Saturday 20:05: sync, post the weekly review if due
    python -m coach.jobs nightly         # nightly: sync, check-in reminder, backup

Exactly three jobs: Cloud Scheduler's free tier is three jobs per billing account. Output is
plain lines on stdout (Cloud Logging), already redacted. Never imports the AI code.
Exit code 0 = done or nothing to do; 1 = a step failed (Cloud Run marks the run failed).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from coach.logging import redact


def morning_brief() -> int:
    from coach import brief

    return brief.main(["--publish", "--if-due"])


def weekly_review() -> int:
    from coach import review

    return review.main(["--publish", "--if-due"])


def nightly() -> int:
    from coach import backup
    from coach.config import get_settings
    from coach.services import daily_messages
    from coach.services import sync as live_sync

    settings = get_settings()
    target = settings.db_target
    ok = True
    for outcome in (
        live_sync.sync_garmin(target, settings, days=3),
        live_sync.sync_calendar(target, settings),
    ):
        print(f"{outcome.source}: {outcome.message}")
        ok &= outcome.ok or "not connected" in outcome.message
    reminder = daily_messages.ensure_checkin_reminder(target, settings)
    if reminder is not None:
        print(reminder.message)
        ok &= reminder.ok
    made = backup.backup_if_due(settings)
    print(f"backup: {made.path.name if made else 'not due'}")
    return 0 if ok else 1


JOBS: dict[str, Callable[[], int]] = {
    "morning-brief": morning_brief,
    "weekly-review": weekly_review,
    "nightly": nightly,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in JOBS:
        print(__doc__)
        return 2
    try:
        return JOBS[args[0]]()
    except Exception as exc:
        print(redact(f"{args[0]} failed: {type(exc).__name__}: {exc}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
