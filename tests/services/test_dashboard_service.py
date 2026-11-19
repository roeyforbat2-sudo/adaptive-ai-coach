from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from coach.db import connect, migrate
from coach.db.repositories import ActivityRepository
from coach.services.dashboard import (
    DatabaseNotFoundError,
    build_view,
    load_dashboard_data,
    resolve_db_path,
)
from coach.synthetic import generate, seed_database
from tests.conftest import make_activity

TZ = ZoneInfo("Asia/Jerusalem")
END = date(2026, 11, 16)


@pytest.fixture
def demo_db(tmp_path: Path) -> Path:
    db = tmp_path / "dev.sqlite"
    conn = connect(db)
    migrate(conn)
    seed_database(conn, generate(70, end_date=END, seed=42, tz=TZ))
    conn.close()
    return db


def test_resolve_prefers_configured_then_demo(tmp_path: Path) -> None:
    configured, demo = tmp_path / "coach.sqlite", tmp_path / "dev.sqlite"
    with pytest.raises(DatabaseNotFoundError, match=r"coach\.synthetic"):
        resolve_db_path(configured, demo)
    demo.write_bytes(b"")
    assert resolve_db_path(configured, demo) == demo
    configured.write_bytes(b"")
    assert resolve_db_path(configured, demo) == configured


def test_dashboard_connection_is_read_only(demo_db: Path) -> None:
    conn = connect(demo_db, read_only=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM activities")
    conn.close()


def test_read_only_requires_existing_file(tmp_path: Path) -> None:
    with pytest.raises(sqlite3.OperationalError):
        connect(tmp_path / "missing.sqlite", read_only=True)


def test_load_marks_demo_and_coverage(demo_db: Path) -> None:
    data = load_dashboard_data(demo_db, TZ, today=date(2026, 11, 17))
    assert data.is_demo and data.demo_only
    assert data.data_end == END
    assert data.data_start == date(2026, 9, 8)
    assert data.as_of == date(2026, 11, 17)
    assert len(data.planned) > 0


def test_stale_demo_data_is_shown_as_of_its_end(demo_db: Path) -> None:
    data = load_dashboard_data(demo_db, TZ, today=date(2027, 1, 10))
    assert data.as_of == date(2026, 11, 17)


def test_mixed_sources_still_flagged_demo(demo_db: Path) -> None:
    conn = connect(demo_db)
    ActivityRepository(conn).upsert(make_activity(source="manual", source_id="m-1"))
    conn.close()
    data = load_dashboard_data(demo_db, TZ, today=date(2026, 11, 17))
    assert data.is_demo and not data.demo_only


@pytest.mark.parametrize("weeks", [4, 8, 12, None])
def test_build_view_for_every_range(demo_db: Path, weeks: int | None) -> None:
    data = load_dashboard_data(demo_db, TZ, today=date(2026, 11, 17))
    view = build_view(data, weeks)
    assert view.end == END
    assert view.start >= data.data_start  # type: ignore[operator]
    if weeks and weeks * 7 <= 70:  # fixture holds 70 days; longer ranges clip to data start
        assert len(view.weekly_running) in (weeks, weeks + 1)  # partial week at either end
    else:
        assert view.start == data.data_start
    assert view.weekly_running["distance_km"].sum() > 0
    assert set(view.upcoming["status"]) <= {"today", "upcoming"}
    assert not view.upcoming.empty
    assert (view.recovery["date"] >= pd.Timestamp(view.start)).all()
    assert view.plan_status["date"].max() <= pd.Timestamp(view.plan_end)


def test_overview_values_are_plausible(demo_db: Path) -> None:
    view = build_view(load_dashboard_data(demo_db, TZ, today=date(2026, 11, 17)), 8)
    ov = view.overview
    assert ov.runs.last_7d == 5 and ov.strength_sessions.last_7d == 2
    assert ov.run_km.prior_weekly_avg is not None
    assert ov.adherence_pct_7d == 100
    assert ov.hrv_7d is not None and ov.sleep_h_7d is not None
