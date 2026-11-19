"""Daily backups: consistent copy, manifest, once-a-day trigger, pruning, CLI (temp dirs only)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from coach import backup
from coach.config import Settings, get_settings
from coach.db import connect, migrate
from coach.db.repositories import AuditLogRepository

TZ = ZoneInfo("Asia/Jerusalem")
NOW = datetime(2026, 11, 18, 10, 30, tzinfo=TZ)


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Settings, Path]:
    db = tmp_path / "data" / "coach.sqlite"
    db.parent.mkdir()
    conn = connect(db)
    migrate(conn)
    AuditLogRepository(conn).record("user", "test", "Half marathon")
    conn.close()
    program = tmp_path / "data" / "program.json"
    program.write_text('{"title": "x"}', encoding="utf-8")
    dest = tmp_path / "backups"
    settings = Settings(
        _env_file=None, db_path=db, program_file=program, backup_dir=dest, backup_keep=3
    )
    return settings, dest


def test_backup_is_a_complete_checked_copy(setup: tuple[Settings, Path]) -> None:
    settings, dest = setup
    made = backup.make_backup(settings.db_path, settings.program_file, dest, NOW)
    assert made.path.name == "coach-2026-11-18_1030"
    copy = sqlite3.connect(made.path / "coach.sqlite")
    assert copy.execute("SELECT entity FROM audit_log").fetchone() == ("Half marathon",)
    copy.close()
    assert (made.path / "program.json").read_text(encoding="utf-8") == '{"title": "x"}'
    manifest = json.loads((made.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["rows"]["audit_log"] == 1 and "coach.sqlite" in manifest["files"]
    assert not list(dest.glob(".*partial"))  # no leftovers
    with pytest.raises(backup.BackupError, match="already exists"):
        backup.make_backup(settings.db_path, settings.program_file, dest, NOW)


def test_once_a_day_and_pruning_only_touches_app_backups(setup: tuple[Settings, Path]) -> None:
    settings, dest = setup
    assert backup.backup_if_due(settings, NOW) is not None
    assert backup.backup_if_due(settings, NOW + timedelta(hours=5)) is None  # same day
    dest.joinpath("my notes.txt").write_text("keep me", encoding="utf-8")
    dest.joinpath("coach-not-a-backup").mkdir()
    for day in range(1, 5):
        assert backup.backup_if_due(settings, NOW + timedelta(days=day)) is not None
    kept = backup.list_backups(dest)
    assert [b.when.day for b in kept] == [22, 21, 20]  # keep=3, newest first
    assert dest.joinpath("my notes.txt").is_file()
    assert dest.joinpath("coach-not-a-backup").is_dir()


def test_missing_database(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, db_path=tmp_path / "none.sqlite", backup_dir=tmp_path)
    assert backup.backup_if_due(settings, NOW) is None
    with pytest.raises(backup.BackupError, match="No database"):
        backup.make_backup(settings.db_path, None, tmp_path, NOW)


def test_default_dir_prefers_setting_then_onedrive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COACH_BACKUP_DIR", raising=False)
    assert backup.default_dir(Settings(_env_file=None)) == Path("backups")
    monkeypatch.setenv("ONEDRIVE", str(tmp_path))
    assert backup.default_dir(Settings(_env_file=None)) == tmp_path / "Adaptive Coach backups"
    assert backup.default_dir(Settings(_env_file=None, backup_dir=tmp_path / "x")) == tmp_path / "x"


def test_cli(
    setup: tuple[Settings, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings, dest = setup
    monkeypatch.setenv("COACH_DB_PATH", str(settings.db_path))
    monkeypatch.setenv("COACH_PROGRAM_FILE", str(settings.program_file))
    monkeypatch.setenv("COACH_BACKUP_DIR", str(dest))
    get_settings.cache_clear()
    try:
        assert backup.main([]) == 0
        assert "Backup saved" in capsys.readouterr().out
        assert backup.main(["--list"]) == 0
        assert "1 backup(s)" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
