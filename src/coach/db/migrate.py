"""Forward-only SQL migrations.

Files live in `coach/db/migrations/` and are named `NNNN_description.sql`. Each pending
migration runs in its own transaction together with its `schema_migrations` row, so a
failing migration leaves the database unchanged. Never edit a migration once applied;
add a new one instead.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from coach.db.connection import Connection, DatabaseError, dialect
from coach.db.pg import translate_ddl
from coach.timeutil import to_db, utc_now

log = logging.getLogger(__name__)

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def _default_source() -> Traversable:
    return resources.files("coach.db").joinpath("migrations")


def discover(source: Traversable | Path | None = None) -> list[Migration]:
    folder = source if source is not None else _default_source()
    found: dict[int, Migration] = {}
    for entry in folder.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _NAME.match(entry.name)
        if match is None:
            raise MigrationError(f"badly named migration file: {entry.name}")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"duplicate migration version {version:04d}")
        found[version] = Migration(version, entry.name, entry.read_text(encoding="utf-8"))
    return [found[v] for v in sorted(found)]


def applied_versions(conn: Connection) -> set[int]:
    strict = "" if dialect(conn) == "postgres" else " STRICT"
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_utc TEXT NOT NULL)" + strict
    )
    conn.commit()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(conn: Connection, source: Traversable | Path | None = None) -> list[int]:
    """Apply pending migrations in order. Returns the versions applied by this call.

    On Postgres the SQLite DDL is translated first (`coach.db.pg.translate_ddl`); there,
    DDL is transactional too, so each migration still applies atomically.
    """
    postgres = dialect(conn) == "postgres"
    done = applied_versions(conn)
    applied: list[int] = []
    for migration in discover(source):
        if migration.version in done:
            continue
        try:
            if postgres:
                conn.executescript(translate_ddl(migration.sql))
            else:
                # executescript commits anything pending, then runs our explicit BEGIN; the
                # transaction stays open so the bookkeeping row commits atomically with it.
                conn.executescript("BEGIN;\n" + migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_utc) VALUES (?, ?, ?)",
                (migration.version, migration.name, to_db(utc_now())),
            )
            conn.commit()
        except DatabaseError as exc:
            if conn.in_transaction:
                conn.rollback()
            raise MigrationError(f"migration {migration.name} failed: {exc}") from exc
        log.info("applied migration %s", migration.name)
        applied.append(migration.version)
    return applied
