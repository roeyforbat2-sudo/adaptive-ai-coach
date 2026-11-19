"""Print or post the morning brief; set up the daily check-in reminder.

python -m coach.brief                      # today's brief (or --date 2026-11-23)
python -m coach.brief --publish            # sync, then post today's brief now
python -m coach.brief --publish --if-due   # only between 04:30 and 09:00, once a day
python -m coach.brief --reminder           # create/refresh the daily 17:30 check-in event
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from coach.config import get_settings
from coach.logging import setup_logging
from coach.program import load_program
from coach.services import daily_messages as dm
from coach.services import sync as live_sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.brief",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--date", type=date.fromisoformat, help="the day (default today)")
    parser.add_argument("--publish", action="store_true", help="post it to Google Calendar")
    parser.add_argument("--if-due", action="store_true", help="with --publish: only when due")
    parser.add_argument("--reminder", action="store_true", help="the 17:30 check-in event")
    args = parser.parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level)
    program = load_program(settings.program_file)
    if program is None:
        print(f"No training program at {settings.program_file}.")
        return 2
    db_path, now = settings.db_target, datetime.now(settings.tz)
    if args.reminder:
        out = dm.ensure_checkin_reminder(db_path, settings, force=True)
        print(out.message if out else "Calendar write access is not connected.")
        return 0 if out and out.ok else 1
    if args.publish:
        if args.if_due and (not dm.brief_due(now) or dm.brief_posted_for(db_path) == now.date()):
            print("Nothing to post right now.")
            return 0
        for outcome in (
            live_sync.sync_garmin(db_path, settings),
            live_sync.sync_calendar(db_path, settings),
        ):
            print(f"{outcome.source}: {outcome.message}")
        result = dm.publish_brief(db_path, settings, args.date or now.date(), program=program)
        print(result.message)
        reminder = dm.ensure_checkin_reminder(db_path, settings)
        if reminder is not None:
            print(reminder.message)
        return 0 if result.ok else 1
    brief = dm.build_brief(db_path, settings, program, args.date or now.date())
    if brief is None:
        print("No workout planned that day: no brief.")
        return 0
    print(brief.title)
    print(brief.as_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
