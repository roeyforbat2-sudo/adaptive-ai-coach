"""Postgres support (cloud): a small connection wrapper that behaves like sqlite3's.

The app's SQL is written once, for SQLite (local runs, tests). On Postgres:
- placeholders are translated: `?` -> `%s`, `:name` -> `%(name)s`, literal `%` -> `%%`;
- Python bools become 0/1 (the schema stores flags as integers, as in SQLite);
- rows support both `row[0]` and `row["column"]`, like sqlite3.Row;
- `with conn:` commits or rolls back (psycopg's own context manager would close);
- migrations are translated from the SQLite DDL by `translate_ddl`.
TLS is required for every connection (`sslmode=require` unless the URL says stricter).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from types import TracebackType
from typing import Any

import psycopg

URL_PREFIXES = ("postgresql://", "postgres://")

# :name outside of '::' casts and string literals (our SQL is app-authored, no quotes inside).
_NAMED = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def is_url(target: object) -> bool:
    return isinstance(target, str) and target.startswith(URL_PREFIXES)


def translate_sql(sql: str) -> str:
    out, i, n = [], 0, len(sql)
    in_quote = False
    while i < n:
        ch = sql[i]
        if ch == "'":
            in_quote = not in_quote
            out.append(ch)
        elif ch == "%":
            out.append("%%")
        elif not in_quote and ch == "?":
            out.append("%s")
        elif not in_quote and ch == ":" and (m := _NAMED.match(sql, i)):
            out.append(f"%({m.group(1)})s")
            i = m.end()
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _param(value: Any) -> Any:
    return int(value) if isinstance(value, bool) else value


def _params(params: Sequence[Any] | Mapping[str, Any] | None) -> Any:
    if params is None:
        return None
    if isinstance(params, Mapping):
        return {k: _param(v) for k, v in params.items()}
    return [_param(v) for v in params]


class Row(Sequence[Any]):
    """Index and name access, like sqlite3.Row."""

    __slots__ = ("_index", "_values")

    def __init__(self, names: Sequence[str], values: Sequence[Any]) -> None:
        self._index = {name: i for i, name in enumerate(names)}
        self._values = tuple(values)

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._index)

    def __eq__(self, other: object) -> bool:
        return tuple(self) == (tuple(other) if isinstance(other, Sequence) else other)

    def __hash__(self) -> int:
        return hash(self._values)

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._index, self._values, strict=True))})"


class Cursor:
    def __init__(self, cur: psycopg.Cursor[Any]) -> None:
        self._cur = cur
        self._names = [d.name for d in cur.description] if cur.description else []

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self) -> Any:
        return self._cur.description

    def fetchone(self) -> Row | None:
        row = self._cur.fetchone()
        return None if row is None else Row(self._names, row)

    def fetchall(self) -> list[Row]:
        return [Row(self._names, r) for r in self._cur.fetchall()]

    def __iter__(self) -> Iterator[Row]:
        return iter(self.fetchall())


class PgConnection:
    dialect = "postgres"

    def __init__(self, url: str, *, read_only: bool = False, connect_timeout: int = 10) -> None:
        kwargs: dict[str, Any] = {"connect_timeout": connect_timeout}
        if "sslmode=" not in url:
            kwargs["sslmode"] = "require"
        self._conn = psycopg.connect(url, **kwargs)
        self.read_only = read_only
        if read_only:
            self._conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            self._conn.commit()

    # --- sqlite3-like API ---------------------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> Cursor:
        cur = self._conn.cursor()
        cur.execute(translate_sql(sql), _params(params))
        return Cursor(cur)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any] | Mapping[str, Any]]) -> None:
        with self._conn.cursor() as cur:
            cur.executemany(translate_sql(sql), [_params(p) for p in seq])

    def executescript(self, script: str) -> None:
        """Run several statements (no parameters), e.g. a translated migration."""
        self._conn.execute(script.encode())  # bytes: no placeholder parsing at all

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    @property
    def in_transaction(self) -> bool:
        return self._conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def __enter__(self) -> PgConnection:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()


# --- DDL translation (SQLite migrations -> Postgres) ----------------------------------------

_TRIGGER = re.compile(
    r"CREATE TRIGGER (\w+) BEFORE (UPDATE|DELETE) ON (\w+)\s*\n?\s*"
    r"BEGIN SELECT RAISE\(ABORT, '([^']*)'\); END;",
    re.IGNORECASE,
)


def _trigger(m: re.Match[str]) -> str:
    name, event, table, message = m.groups()
    return (
        f"CREATE OR REPLACE FUNCTION {name}_fn() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"BEGIN RAISE EXCEPTION '{message}'; END $$;\n"
        f"CREATE TRIGGER {name} BEFORE {event} ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {name}_fn();"
    )


def translate_ddl(sql: str) -> str:
    """The SQLite migrations as Postgres DDL (types, identity keys, checks, triggers)."""
    out = _TRIGGER.sub(_trigger, sql)
    out = re.sub(r"\)\s*STRICT\s*;", ");", out)
    # Surrogate keys: auto-numbered. (A key with its own CHECK, e.g. id = 1, stays plain.)
    out = re.sub(
        r"\bid(\s+)INTEGER PRIMARY KEY(\s*,)",
        r"id\1BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY\2",
        out,
    )
    out = re.sub(r"\bINTEGER\b", "BIGINT", out)
    out = re.sub(r"\bREAL\b", "DOUBLE PRECISION", out)
    out = re.sub(r"CHECK \(json_valid\((\w+)\)\)", r"CHECK ((\1::jsonb) IS NOT NULL)", out)
    # Case-insensitive unique names: SQLite COLLATE NOCASE -> a unique index on lower(name).
    nocase = re.findall(
        r"CREATE TABLE (\w+) \([^;]*?\b(\w+)\s+TEXT\s+NOT NULL UNIQUE COLLATE NOCASE", out
    )
    out = re.sub(r"(\w+\s+TEXT\s+NOT NULL) UNIQUE COLLATE NOCASE", r"\1", out)
    for table, column in nocase:
        out += f"\nCREATE UNIQUE INDEX uq_{table}_{column}_ci ON {table} (lower({column}));\n"
    return out
