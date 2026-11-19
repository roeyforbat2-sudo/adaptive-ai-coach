from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from coach.db import connect, migrate
from coach.db.repositories import ActivityRepository, FeedbackRepository
from coach.domain import Feedback, Source, Sport
from coach.synthetic import generate, seed_database
from coach.synthetic.__main__ import main

END = date(2026, 11, 15)
TZ = ZoneInfo("Asia/Jerusalem")


def test_same_seed_is_deterministic() -> None:
    assert generate(60, end_date=END, seed=7, tz=TZ) == generate(60, end_date=END, seed=7, tz=TZ)


def test_different_seed_differs() -> None:
    a = generate(60, end_date=END, seed=1, tz=TZ)
    b = generate(60, end_date=END, seed=2, tz=TZ)
    assert a.wellness != b.wellness


def test_dataset_shape() -> None:
    data = generate(84, end_date=END, seed=42, tz=TZ)  # 12 weeks
    assert len(data.wellness) == 84
    assert {w.date for w in data.wellness} == {END - timedelta(days=i) for i in range(84)}

    runs = [a for a in data.activities if a.sport is Sport.RUN]
    strength = [a for a in data.activities if a.sport is Sport.STRENGTH]
    assert 50 <= len(runs) <= 60  # ~5/week minus the pain episode
    assert len(strength) == len(data.strength_sessions) == 24  # 2/week

    pain = [fb for _, fb in data.feedback if fb.pain_flag]
    assert len(pain) == 3
    pain_days = {fb.date for fb in pain}
    assert not any(a.start_utc.astimezone(TZ).date() in pain_days for a in runs)

    assert all(a.source is Source.SYNTHETIC for a in data.activities)
    assert all(a.name and a.name.startswith("Synthetic") for a in data.activities)


def test_local_start_times_respect_timezone() -> None:
    data = generate(14, end_date=END, seed=42, tz=TZ)
    long_runs = [a for a in data.activities if a.name == "Synthetic long run"]
    assert long_runs
    assert all(a.start_utc.astimezone(TZ).hour == 7 for a in long_runs)


def test_taper_reduces_strength_volume() -> None:
    data = generate(100, end_date=END, seed=42, tz=TZ)
    first = len(data.strength_sessions[0][1].sets)
    last = len(data.strength_sessions[-1][1].sets)
    assert last < first


def test_rejects_non_positive_days() -> None:
    with pytest.raises(ValueError, match="days"):
        generate(0, end_date=END)


def test_seeding_is_idempotent_and_links_ids(conn: sqlite3.Connection) -> None:
    data = generate(28, end_date=END, seed=42, tz=TZ)
    seed_database(conn, data)
    seed_database(conn, data)
    assert ActivityRepository(conn).count() == len(data.activities)
    assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == len(data.feedback)
    unlinked_sessions = conn.execute(
        "SELECT COUNT(*) FROM strength_sessions WHERE activity_id IS NULL"
    ).fetchone()[0]
    assert unlinked_sessions == 0


def test_cli_seeds_file_db(tmp_path: Path) -> None:
    db = tmp_path / "dev.sqlite"
    assert main(["--days", "14", "--db", str(db), "--end-date", "2026-11-15"]) == 0
    conn = connect(db)
    assert ActivityRepository(conn).count() > 0
    conn.close()


def test_cli_refuses_db_with_real_data(tmp_path: Path) -> None:
    db = tmp_path / "real.sqlite"
    conn = connect(db)
    migrate(conn)
    FeedbackRepository(conn).add(Feedback(date=END, rpe=5))  # a "real" manual entry
    conn.close()
    assert main(["--days", "14", "--db", str(db), "--end-date", "2026-11-15"]) == 2


def test_plan_covers_block_and_future() -> None:
    data = generate(28, end_date=END, seed=42, tz=TZ, plan_days_ahead=14)
    dates = {p.date for p in data.planned}
    assert min(dates) == END - timedelta(days=27)
    assert max(dates) == END + timedelta(days=14)
    assert all(p.workout_type.is_strength == (p.sport is Sport.STRENGTH) for p in data.planned)


def test_plan_does_not_change_generated_actuals() -> None:
    with_plan = generate(28, end_date=END, seed=42, tz=TZ, plan_days_ahead=14)
    without = generate(28, end_date=END, seed=42, tz=TZ, plan_days_ahead=0)
    assert with_plan.activities == without.activities
    assert with_plan.wellness == without.wellness


def test_pain_days_leave_planned_runs_unfulfilled() -> None:
    data = generate(84, end_date=END, seed=42, tz=TZ)
    pain_days = {fb.date for _, fb in data.feedback if fb.pain_flag}
    planned_runs_on_pain_days = [
        p for p in data.planned if p.date in pain_days and p.sport is Sport.RUN
    ]
    assert planned_runs_on_pain_days  # planned ...
    run_days = {a.start_utc.astimezone(TZ).date() for a in data.activities if a.sport is Sport.RUN}
    assert not pain_days & run_days  # ... but not done
