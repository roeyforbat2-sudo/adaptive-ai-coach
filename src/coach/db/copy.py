"""Copy all data into a fresh database (SQLite -> Postgres, or a backup export -> any).

    python -m coach.db.copy --from data/coach.sqlite --to-keyring  # target: set_db_url's URL
    python -m coach.db.copy --from data/coach.sqlite --to-env      # target: COACH_DATABASE_URL
    python -m coach.db.copy --from-export coach-export.json --to-env

The target gets the schema (migrations), then every table's rows with their original ids,
and its id counters are moved past the copied ids. It REFUSES a target that already holds
training data, so it can never overwrite anything. Row counts are compared at the end.
The database URL is read from the environment (Secret Manager in the cloud), never from
the command line, so it does not end up in shell history.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from coach.db.connection import Connection, DbTarget, connect, dialect
from coach.db.migrate import migrate

# Parents before children (foreign keys).
ORDER = (
    "athlete_profile",
    "goals",
    "activities",
    "exercises",
    "strength_sessions",
    "strength_sets",
    "daily_wellness",
    "feedback",
    "planned_workouts",
    "calendar_events",
    "coach_outputs",
    "chat_messages",
    "sync_state",
    "audit_log",
)
USER_DATA = ("activities", "daily_wellness", "feedback", "planned_workouts", "audit_log")
SEEDED = ("exercises",)  # filled by migration 0001; replaced by the source's rows


class CopyError(RuntimeError):
    pass


Tables = dict[str, tuple[list[str], list[list[Any]]]]


def read_sqlite(conn: Connection) -> Tables:
    out: Tables = {}
    for table in ORDER:
        cur = conn.execute(f"SELECT * FROM {table} ORDER BY 1")  # noqa: S608 - fixed names
        out[table] = ([d[0] for d in cur.description], [list(r) for r in cur.fetchall()])
    return out


def read_export(path: Path) -> Tables:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != 1:
        raise CopyError("unknown export format")
    return {t: (v["columns"], v["rows"]) for t, v in data["tables"].items() if t in ORDER}


def _count(conn: Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def write(target: Connection, tables: Tables) -> dict[str, int]:
    migrate(target)
    busy = [t for t in USER_DATA if _count(target, t)]
    if busy:
        raise CopyError(f"target already has data in {', '.join(busy)}; nothing was copied")
    with target:
        for table in SEEDED:
            if tables.get(table, ([], []))[1]:
                target.execute(f"DELETE FROM {table}")  # noqa: S608
        for table in ORDER:
            columns, rows = tables.get(table, ([], []))
            if not rows:
                continue
            cols = ", ".join(columns)
            marks = ", ".join("?" for _ in columns)
            target.executemany(
                f"INSERT INTO {table} ({cols}) VALUES ({marks})",  # noqa: S608 - fixed names
                rows,
            )
        if dialect(target) == "postgres":
            for table in ORDER:
                if "id" in tables.get(table, ([], []))[0]:
                    target.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "  # noqa: S608
                        f"(SELECT COALESCE(MAX(id), 0) + 1 FROM {table}), false)"
                    )
    counts = {t: _count(target, t) for t in ORDER}
    for table, (_, rows) in tables.items():
        if counts.get(table) != len(rows):
            raise CopyError(f"{table}: copied {counts.get(table)} of {len(rows)} rows")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.db.copy",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--from", dest="source", type=Path, help="a SQLite database file")
    src.add_argument("--from-export", type=Path, help="a coach-export.json backup")
    dst = parser.add_mutually_exclusive_group(required=True)
    dst.add_argument("--to-env", action="store_true", help="COACH_DATABASE_URL (Postgres)")
    dst.add_argument(
        "--to-keyring", action="store_true", help="the URL saved by python -m coach.set_db_url"
    )
    dst.add_argument("--to", type=Path, help="a NEW SQLite file (for testing a restore)")
    args = parser.parse_args(argv)

    if args.source is not None:
        if not args.source.is_file():
            print(f"No database at {args.source}.")
            return 2
        source = connect(args.source, read_only=True)
        try:
            tables = read_sqlite(source)
        finally:
            source.close()
    else:
        tables = read_export(args.from_export)

    target_url: DbTarget
    if args.to_keyring:
        from coach import secrets
        from coach.set_db_url import MAIN

        if not secrets.has_secret(MAIN):
            print("No database URL stored. Run: python -m coach.set_db_url")
            return 2
        target_url = secrets.get_secret(MAIN)
    elif args.to_env:
        url = os.environ.get("COACH_DATABASE_URL", "")
        if not url.startswith(("postgresql://", "postgres://")):
            print("Set COACH_DATABASE_URL to the Postgres URL first (it is never printed).")
            return 2
        target_url = url
    else:
        target_url = args.to
    target = connect(target_url)
    try:
        counts = write(target, tables)
    except CopyError as exc:
        print(f"Copy refused: {exc}")
        return 1
    finally:
        target.close()
    print("Copied: " + ", ".join(f"{t} {n}" for t, n in counts.items() if n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
