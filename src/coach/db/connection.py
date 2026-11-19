from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg

from coach.db.pg import PgConnection, is_url

MEMORY = ":memory:"

# Where the data lives: a SQLite file (local) or a postgresql:// URL (cloud).
DbTarget = Path | str
# Either connection type: they share the sqlite3-style API the app uses.
Connection = Any
# Catch database errors from either backend.
DatabaseError: tuple[type[Exception], ...] = (sqlite3.Error, psycopg.Error)


def connect(path: DbTarget, *, read_only: bool = False) -> Connection:
    """Open the coach database: SQLite for a file path, Postgres for a postgresql:// URL.

    SQLite: foreign keys on, rows accessible by column name; file databases use WAL so the
    dashboard can read while a sync/review job writes. `read_only=True` opens an existing
    file with SQLite's `mode=ro` (raises if the file does not exist).
    Postgres: TLS required; `read_only=True` makes the whole session read-only.
    """
    if is_url(path):
        return PgConnection(str(path), read_only=read_only)
    target = str(path)
    if read_only:
        if target == MEMORY:
            raise ValueError("read_only requires a database file")
        uri = Path(target).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    if target != MEMORY:
        Path(target).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if target != MEMORY:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def dialect(conn: Connection) -> str:
    return "postgres" if isinstance(conn, PgConnection) else "sqlite"


def db_exists(target: DbTarget) -> bool:
    """A SQLite file that exists, or any Postgres URL (the server is assumed to exist)."""
    return is_url(target) or Path(target).is_file()


def as_target(value: str | Path) -> DbTarget:
    """A command-line / cache-key string back to a target (URL stays a string)."""
    return str(value) if is_url(value) else Path(value)


def changed_marker(target: DbTarget) -> float:
    """The SQLite file's modification time (a cache key); 0 for Postgres."""
    return 0.0 if is_url(target) else Path(target).stat().st_mtime


def describe(target: DbTarget) -> str:
    """Safe to show: never includes a Postgres password."""
    if is_url(target):
        host = str(target).split("@")[-1].split("/")[0].split("?")[0]
        return f"Postgres ({host})"
    return Path(target).as_posix()


def query_frame(conn: Connection, sql: str, params: Any = None) -> pd.DataFrame:
    """Run a query into a DataFrame on either backend (no pandas DBAPI warnings)."""
    cur = conn.execute(sql, params) if params is not None else conn.execute(sql)
    rows = cur.fetchall()
    names = [d[0] for d in cur.description] if cur.description else []
    return pd.DataFrame.from_records([tuple(r) for r in rows], columns=names)


def has_column(conn: Connection, table: str, column: str) -> bool:
    if dialect(conn) == "postgres":
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = ? AND column_name = ?",
            (table, column),
        ).fetchone()
        return row is not None
    return column in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
