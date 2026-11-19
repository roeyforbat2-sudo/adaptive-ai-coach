"""Print or post the weekly review (free, rule-based).

    python -m coach.review                       # this week's review so far
    python -m coach.review --date 2026-11-21     # the week containing that date
    python -m coach.review --publish             # sync, then post this week's review now
    python -m coach.review --publish --if-due    # post only when due (Sat 20:00..Mon 08:00)

`--publish` first syncs Garmin and Google Calendar (read-only), then creates or updates the
one "Weekly review" event for that Saturday. It never touches your training events.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from coach.config import get_settings
from coach.logging import setup_logging
from coach.program import load_program
from coach.services import sync as live_sync
from coach.services import weekly_review as wr


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.review",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", type=date.fromisoformat, help="a day in the week to review")
    parser.add_argument("--publish", action="store_true", help="post it to Google Calendar")
    parser.add_argument("--if-due", action="store_true", help="with --publish: only when due")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    program = load_program(settings.program_file)
    if program is None:
        print(f"No training program at {settings.program_file}.")
        return 2
    now = datetime.now(settings.tz)
    db_path = settings.db_target

    if args.publish:
        if args.if_due and wr.due_saturday(now) in (None, wr.last_posted(db_path)):
            print("Nothing to post right now.")
            return 0
        for outcome in (
            live_sync.sync_garmin(db_path, settings),
            live_sync.sync_calendar(db_path, settings),
        ):
            print(f"{outcome.source}: {outcome.message}")
        saturday = wr.due_saturday(now) if args.if_due else wr.saturday_of(args.date or now.date())
        assert saturday is not None
        # Before Saturday this posts a preview of the week so far; Saturday's run replaces it.
        result = wr.publish(db_path, settings, saturday, program=program, as_of=now.date())
        print(result.message)
        return 0 if result.ok else 1

    day = args.date or now.date()
    review = wr.build(db_path, settings, program, wr.saturday_of(day), as_of=now.date())
    if review is None:
        print(f"{day} is outside your training program.")
        return 2
    print(review.as_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
