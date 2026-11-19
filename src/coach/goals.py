"""Manage training goals (races and other targets).

    python -m coach.goals list
    python -m coach.goals add --desc "Half marathon" --date 2027-02-26 --distance 21.1 --priority 1
    python -m coach.goals add --desc "Marathon (October 2027)" --distance 42.2
    python -m coach.goals done 2          # mark goal #2 inactive (kept for history)

Goals are data, not code: the coach plans toward the nearest active goal and keeps the
others in view. A goal without a date is shown as "date to be set".
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from coach.config import get_settings
from coach.db import connect, migrate
from coach.db.connection import as_target
from coach.db.repositories import GoalRepository
from coach.domain import Goal, GoalKind


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m coach.goals",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", type=as_target, default=settings.db_target)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    add = sub.add_parser("add")
    add.add_argument("--desc", required=True)
    add.add_argument("--date", type=date.fromisoformat, default=None)
    add.add_argument("--distance", type=float, default=None, help="race distance in km")
    add.add_argument("--priority", type=int, default=2, choices=(1, 2, 3))
    add.add_argument("--kind", default="race", choices=[k.value for k in GoalKind])
    done = sub.add_parser("done")
    done.add_argument("goal_id", type=int)
    args = parser.parse_args(argv)

    conn = connect(args.db)
    try:
        migrate(conn)
        repo = GoalRepository(conn)
        if args.cmd == "add":
            goal = Goal(
                kind=GoalKind(args.kind),
                description=args.desc,
                target_date=args.date,
                target_value=args.distance,
                unit="km" if args.distance else None,
                priority=args.priority,
            )
            print(f"Added goal #{repo.add(goal)}: {goal.description}")
        elif args.cmd == "done":
            ok = repo.set_active(args.goal_id, False)
            print("Marked done." if ok else f"No goal #{args.goal_id}.")
            return 0 if ok else 1
        today = datetime_today(settings.tz)
        for g in repo.list():
            when = (
                f"{g.target_date} ({(g.target_date - today).days} days)"
                if g.target_date
                else "date to be set"
            )
            dist = f", {g.target_value:g} {g.unit}" if g.target_value else ""
            print(f"#{g.id} [P{g.priority}] {g.description}{dist} - {when}")
    finally:
        conn.close()
    return 0


def datetime_today(tz: object) -> date:
    from datetime import datetime

    return datetime.now(tz).date()  # type: ignore[arg-type]


if __name__ == "__main__":
    sys.exit(main())
