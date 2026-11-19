"""Dashboard service: load data read-only from SQLite and build the view model.

The dashboard never writes. The database is opened with SQLite `mode=ro`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from coach.analytics import dashboard as an
from coach.db import connect
from coach.db.connection import DbTarget, db_exists
from coach.db.read_models import (
    activities_frame,
    data_sources,
    feedback_frame,
    planned_frame,
    strength_sessions_frame,
    strength_sets_frame,
    wellness_frame,
)

DEMO_DB_PATH = Path("data/dev.sqlite")
SYNTHETIC = "synthetic"


class DatabaseNotFoundError(FileNotFoundError):
    pass


def resolve_db_path(configured: DbTarget, demo: Path = DEMO_DB_PATH) -> DbTarget:
    """Use the configured database (a Postgres URL, or an existing file), else the demo one."""
    candidates: tuple[DbTarget, ...] = (configured, demo)
    for candidate in candidates:
        if db_exists(candidate):
            return candidate
    raise DatabaseNotFoundError(
        f"No database at {configured} or {demo}. Create demo data with: "
        "python -m coach.synthetic --days 120 --db data/dev.sqlite"
    )


@dataclass(frozen=True)
class DashboardData:
    db_path: DbTarget
    tz: ZoneInfo
    sources: frozenset[str]
    activities: pd.DataFrame
    wellness: pd.DataFrame
    sessions: pd.DataFrame
    sets: pd.DataFrame
    planned: pd.DataFrame
    feedback: pd.DataFrame
    data_start: date | None
    data_end: date | None
    as_of: date

    @property
    def is_demo(self) -> bool:
        """True if ANY shown data is synthetic (every page then carries the DEMO label)."""
        return SYNTHETIC in self.sources

    @property
    def demo_only(self) -> bool:
        return self.sources == frozenset({SYNTHETIC})


def load_dashboard_data(db_path: DbTarget, tz: ZoneInfo, today: date) -> DashboardData:
    conn = connect(db_path, read_only=True)
    try:
        sources = frozenset(data_sources(conn))
        activities = an.add_local_dates(activities_frame(conn), tz)
        wellness = wellness_frame(conn)
        sessions = strength_sessions_frame(conn)
        sets = strength_sets_frame(conn)
        planned = planned_frame(conn)
        feedback = feedback_frame(conn)
    finally:
        conn.close()

    observed = pd.concat([activities["date"], wellness["date"], sessions["date"]]).dropna()
    data_start = observed.min().date() if not observed.empty else None
    data_end = observed.max().date() if not observed.empty else None

    as_of = today
    if SYNTHETIC in sources and data_end is not None and data_end < today - timedelta(days=1):
        # Stale demo data: show it as of the day after it ends, so "last 7 days" is meaningful.
        as_of = data_end + timedelta(days=1)

    return DashboardData(
        db_path=db_path,
        tz=tz,
        sources=sources,
        activities=activities,
        wellness=wellness,
        sessions=sessions,
        sets=sets,
        planned=planned,
        feedback=feedback,
        data_start=data_start,
        data_end=data_end,
        as_of=as_of,
    )


@dataclass(frozen=True)
class DashboardView:
    start: date
    end: date  # last day of history shown (as_of - 1)
    plan_end: date
    overview: an.Overview
    upcoming: pd.DataFrame
    weekly_running: pd.DataFrame
    run_history: pd.DataFrame
    pace_trend: pd.DataFrame
    training_time: pd.DataFrame
    strength_weekly: pd.DataFrame
    muscle_weekly: pd.DataFrame
    muscle_average: pd.DataFrame
    recovery: pd.DataFrame
    plan_status: pd.DataFrame
    adherence: pd.DataFrame


def build_view(data: DashboardData, weeks: int | None, plan_days_ahead: int = 14) -> DashboardView:
    """Compute everything the pages show for the last `weeks` weeks (None = all history)."""
    end = data.as_of - timedelta(days=1)
    if weeks is None:
        start = data.data_start or end
    else:
        start = end - timedelta(weeks=weeks) + timedelta(days=1)
        if data.data_start is not None:
            start = max(start, data.data_start)
    start = min(start, end)
    plan_end = data.as_of + timedelta(days=plan_days_ahead - 1)

    recovery_all = an.recovery_daily(data.wellness, data.data_start or start, end)
    status_all = an.plan_status(
        data.planned, data.activities, data.data_start or start, plan_end, data.as_of
    )
    status_range = status_all[status_all["date"] >= pd.Timestamp(start)].reset_index(drop=True)

    return DashboardView(
        start=start,
        end=end,
        plan_end=plan_end,
        overview=an.overview(
            data.activities, data.sessions, recovery_all, status_all, data.as_of, data.data_start
        ),
        upcoming=an.upcoming(status_all, data.as_of, days=7),
        weekly_running=an.weekly_running(data.activities, start, end),
        run_history=an.run_history(data.activities, start, end),
        pace_trend=an.pace_trend(data.activities, start, end),
        training_time=an.weekly_training_time(data.activities, start, end),
        strength_weekly=an.strength_weekly(data.sessions, data.sets, start, end),
        muscle_weekly=an.muscle_group_weekly(data.sets, start, end),
        muscle_average=an.muscle_group_average(data.sets, end, weeks=4),
        recovery=recovery_all[recovery_all["date"] >= pd.Timestamp(start)].reset_index(drop=True),
        plan_status=status_range,
        adherence=an.weekly_adherence(status_range, start, plan_end),
    )
