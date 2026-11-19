"""Daily backup of your training data: the database and the training program.

    python -m coach.backup            # make a backup now
    python -m coach.backup --list     # list backups (newest first)

Where: `COACH_BACKUP_DIR`, default `<OneDrive>\\Adaptive Coach backups` (so OneDrive keeps an
off-PC copy), else `backups/` in the project (git-ignored). Each backup is a folder
`coach-YYYY-MM-DD_HHMM` holding a consistent copy of the database (SQLite online-backup API,
safe while the app runs), `program.json`, and `manifest.json` (time, sizes, row counts). The
copy is integrity-checked before it counts. Only the newest `COACH_BACKUP_KEEP` (default 30)
backup folders the app made are kept; older ones of THAT pattern are removed. Nothing else
in the folder is ever touched. Credentials are never backed up: they stay in Credential Manager.

Restore (manual, on purpose): stop the dashboard, copy `coach.sqlite` from a backup folder
over `data/coach.sqlite` (and `program.json` over `data/program.json`), start it again.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from coach.config import Settings, get_settings
from coach.db.connection import DbTarget, db_exists
from coach.db.pg import PgConnection, is_url

PREFIX = "coach-"
_NAME = re.compile(r"^coach-(\d{4}-\d{2}-\d{2}_\d{4})$")
_FORMAT = "%Y-%m-%d_%H%M"
DAILY = timedelta(hours=24)
_TABLES = ("activities", "daily_wellness", "planned_workouts", "goals", "audit_log")


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Backup:
    path: Path
    when: datetime  # local time, minute precision (from the folder name)


def default_dir(settings: Settings) -> Path:
    if settings.backup_dir is not None:
        return settings.backup_dir
    onedrive = os.environ.get("ONEDRIVECONSUMER") or os.environ.get("ONEDRIVE")
    if onedrive and Path(onedrive).is_dir():
        return Path(onedrive) / "Adaptive Coach backups"
    return Path("backups")


def list_backups(dest: Path) -> list[Backup]:
    """App-made backup folders in `dest`, newest first (others are ignored)."""
    if not dest.is_dir():
        return []
    out = []
    for p in dest.iterdir():
        m = _NAME.match(p.name)
        if p.is_dir() and m and (p / "manifest.json").is_file():
            out.append(Backup(p, datetime.strptime(m.group(1), _FORMAT)))  # noqa: DTZ007 - local time
    return sorted(out, key=lambda b: b.when, reverse=True)


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in _TABLES:
        try:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
        except sqlite3.Error:
            continue
    return counts


def _export_postgres(url: str, target: Path) -> dict[str, int]:
    """Every table, read in ONE read-only repeatable-read transaction (a consistent
    snapshot), written as JSON: {"format": 1, "tables": {name: {"columns", "rows"}}}."""
    conn = PgConnection(url, read_only=True)
    try:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            ).fetchall()
        ]
        out: dict[str, dict[str, list[object]]] = {}
        for table in tables:
            cur = conn.execute(f'SELECT * FROM "{table}" ORDER BY 1')  # noqa: S608 - catalog names
            out[table] = {
                "columns": [d[0] for d in cur.description],
                "rows": [list(r) for r in cur.fetchall()],
            }
        conn.rollback()
    finally:
        conn.close()
    target.write_text(json.dumps({"format": 1, "tables": out}, default=str), encoding="utf-8")
    return {t: len(v["rows"]) for t, v in out.items() if t in _TABLES}


def make_backup(db_path: DbTarget, program_file: Path | None, dest: Path, now: datetime) -> Backup:
    if not db_exists(db_path):
        raise BackupError(f"No database at {db_path}.")
    if is_url(db_path):
        return _make_cloud_backup(str(db_path), program_file, dest, now)
    db_path = Path(db_path)
    folder = dest / f"{PREFIX}{now:{_FORMAT}}"
    if folder.exists():
        raise BackupError(f"A backup for {now:%Y-%m-%d %H:%M} already exists.")
    tmp = dest / f".{folder.name}.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp / "coach.sqlite")
        try:
            src.backup(dst)  # consistent snapshot, safe while the app is running
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
            counts = _row_counts(dst)
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            raise BackupError(f"The copied database failed its integrity check ({ok}).")
        files = ["coach.sqlite"]
        if program_file is not None and program_file.is_file():
            shutil.copy2(program_file, tmp / "program.json")
            files.append("program.json")
        manifest = {
            "created_local": now.isoformat(timespec="seconds"),
            "files": {f: (tmp / f).stat().st_size for f in files},
            "rows": counts,
            "restore": "Stop the dashboard, copy coach.sqlite over data/coach.sqlite "
            "(and program.json over data/program.json), start it again.",
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tmp.rename(folder)  # appears only when complete
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return Backup(folder, now.replace(second=0, microsecond=0, tzinfo=None))


def _make_cloud_backup(url: str, program_file: Path | None, dest: Path, now: datetime) -> Backup:
    """Built in a local temp folder, then COPIED into `dest` with manifest.json last.

    `dest` is a Cloud Storage bucket mounted with Cloud Storage FUSE, where folders cannot
    be renamed atomically, so there is no rename step. A folder counts as a backup only
    once its manifest exists (list_backups), so a half-copied one is never mistaken for one.
    """
    folder = dest / f"{PREFIX}{now:{_FORMAT}}"
    if (folder / "manifest.json").exists():
        raise BackupError(f"A backup for {now:%Y-%m-%d %H:%M} already exists.")
    with tempfile.TemporaryDirectory() as work:
        tmp = Path(work)
        counts = _export_postgres(url, tmp / "coach-export.json")
        files = ["coach-export.json"]
        if program_file is not None and program_file.is_file():
            shutil.copyfile(program_file, tmp / "program.json")
            files.append("program.json")
        manifest = {
            "created_local": now.isoformat(timespec="seconds"),
            "files": {f: (tmp / f).stat().st_size for f in files},
            "rows": counts,
            "restore": "python -m coach.db.copy --from-export <folder>/coach-export.json --to <db>",
        }
        folder.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copyfile(tmp / f, folder / f)
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return Backup(folder, now.replace(second=0, microsecond=0, tzinfo=None))


def prune(dest: Path, keep: int) -> list[Path]:
    """Remove app-made backup folders beyond the newest `keep`. Returns what was removed."""
    removed = []
    for old in list_backups(dest)[max(1, keep) :]:
        shutil.rmtree(old.path)
        removed.append(old.path)
    return removed


def backup_if_due(settings: Settings, now: datetime | None = None) -> Backup | None:
    """Back up once a day (called by the dashboard). None if not due or nothing to back up."""
    db_path = settings.db_target
    if not db_exists(db_path):
        return None
    now = now or datetime.now(settings.tz)
    dest = default_dir(settings)
    latest = next(iter(list_backups(dest)), None)
    if latest is not None and now.replace(tzinfo=None) - latest.when < DAILY:
        return None
    made = make_backup(db_path, settings.program_file, dest, now)
    prune(dest, settings.backup_keep)
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.backup",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--list", action="store_true", help="list backups, newest first")
    args = parser.parse_args(argv)
    settings = get_settings()
    dest = default_dir(settings)
    if args.list:
        backups = list_backups(dest)
        print(f"{len(backups)} backup(s) in {dest}")
        for b in backups:
            print(f"  {b.when:%a %d %b %Y %H:%M}  {b.path.name}")
        return 0
    try:
        made = make_backup(
            settings.db_target, settings.program_file, dest, datetime.now(settings.tz)
        )
    except BackupError as exc:
        print(f"Backup failed: {exc}")
        return 1
    removed = prune(dest, settings.backup_keep)
    print(f"Backup saved: {made.path}")
    if removed:
        print(f"Removed {len(removed)} old backup(s) (keeping {settings.backup_keep}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
