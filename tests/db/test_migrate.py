from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from coach.db import connect, migrate
from coach.db.migrate import MigrationError, applied_versions, discover

EXPECTED_TABLES = {
    "athlete_profile", "goals", "activities", "exercises", "strength_sessions",
    "strength_sets", "daily_wellness", "feedback", "sync_state", "audit_log",
    "schema_migrations", "planned_workouts", "calendar_events", "coach_outputs", "chat_messages",
}  # fmt: skip


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r[0] for r in rows}


def test_fresh_database_gets_full_schema() -> None:
    conn = connect(":memory:")
    assert migrate(conn) == [1, 2, 3, 4, 5]
    assert _tables(conn) >= EXPECTED_TABLES
    assert conn.execute("SELECT COUNT(*) FROM exercises").fetchone()[0] > 0
    conn.close()


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    assert migrate(conn) == []
    assert applied_versions(conn) == {1, 2, 3, 4, 5}


def test_file_database_uses_wal_and_foreign_keys(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "coach.sqlite"
    conn = connect(db)
    migrate(conn)
    assert db.exists()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_failed_migration_rolls_back(tmp_path: Path) -> None:
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE a (x INTEGER) STRICT;", encoding="utf-8")
    (tmp_path / "0002_broken.sql").write_text(
        "CREATE TABLE b (x INTEGER) STRICT;\nTHIS IS NOT SQL;", encoding="utf-8"
    )
    conn = connect(":memory:")
    with pytest.raises(MigrationError, match="0002_broken"):
        migrate(conn, tmp_path)
    assert "a" in _tables(conn)
    assert "b" not in _tables(conn)  # partial work from the failed file was rolled back
    assert applied_versions(conn) == {1}
    conn.close()


@pytest.mark.parametrize("bad_name", ["1_short.sql", "0001-Dash.sql", "0001_UPPER.sql"])
def test_badly_named_migration_rejected(tmp_path: Path, bad_name: str) -> None:
    (tmp_path / bad_name).write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="badly named"):
        discover(tmp_path)


def test_duplicate_versions_rejected(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="duplicate"):
        discover(tmp_path)


def test_audit_log_is_append_only(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO audit_log (ts_utc, actor, action, entity) VALUES ('t', 'system', 'x', 'y')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit_log SET action = 'z'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit_log")


def test_check_constraints_reject_bad_rows(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO activities (source, source_id, sport, start_utc, duration_s, avg_hr, "
            "created_utc, updated_utc) VALUES ('synthetic', 'x', 'run', 't', 60, 400, 't', 't')"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO activities (source, source_id, sport, start_utc, duration_s, "
            "created_utc, updated_utc) VALUES ('scraper', 'x', 'run', 't', 60, 't', 't')"
        )
