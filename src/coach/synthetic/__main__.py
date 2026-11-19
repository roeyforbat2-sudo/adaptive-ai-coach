"""Seed a development database with synthetic data.

    python -m coach.synthetic --days 120 --db data/dev.sqlite

Refuses to touch a database that already contains non-synthetic (real) data.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from coach.config import get_settings
from coach.db import connect, migrate
from coach.db.repositories import count_non_synthetic_rows
from coach.logging import setup_logging
from coach.synthetic.generator import generate, seed_database

log = logging.getLogger("coach.synthetic")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="python -m coach.synthetic", description=__doc__)
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--db", type=Path, default=Path("data/dev.sqlite"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--end-date", type=date.fromisoformat, default=None, help="YYYY-MM-DD (default: today)"
    )
    args = parser.parse_args(argv)

    setup_logging(settings.log_level)
    end_date = args.end_date or datetime.now(settings.tz).date()

    conn = connect(args.db)
    try:
        migrate(conn)
        if count_non_synthetic_rows(conn) > 0:
            log.error("refusing to seed: %s contains non-synthetic data", args.db)
            return 2
        dataset = generate(args.days, end_date=end_date, seed=args.seed, tz=settings.tz)
        result = seed_database(conn, dataset)
    finally:
        conn.close()

    log.info(
        "seeded %s: %d activities, %d wellness days, %d strength sessions, %d feedback, "
        "%d planned workouts",
        args.db,
        result.activities,
        result.wellness,
        result.strength_sessions,
        result.feedback,
        result.planned,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
