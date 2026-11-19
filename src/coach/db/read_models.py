"""Read models: tabular views of the database for analytics and the dashboard.

Returns pandas DataFrames with typed columns. Missing values stay missing (NaN/None).
"""

from __future__ import annotations

import pandas as pd

from coach.db.connection import Connection, has_column, query_frame

_SOURCE_TABLES = (
    "activities", "daily_wellness", "strength_sessions", "feedback", "planned_workouts",
)  # fmt: skip


def activities_frame(conn: Connection) -> pd.DataFrame:
    df = query_frame(
        conn,
        "SELECT id, source, sport, start_utc, duration_s, distance_m, elevation_gain_m, "
        "avg_hr, max_hr, training_load, aerobic_te, anaerobic_te, name FROM activities "
        "ORDER BY start_utc",
    )
    df["start_utc"] = pd.to_datetime(df["start_utc"], utc=True)
    return df


def wellness_frame(conn: Connection) -> pd.DataFrame:
    df = query_frame(
        conn,
        "SELECT date, source, sleep_duration_s, sleep_score, hrv_rmssd_ms, resting_hr, "
        "body_battery_high, body_battery_low, stress_avg, steps FROM daily_wellness "
        "ORDER BY date",
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def strength_sessions_frame(conn: Connection) -> pd.DataFrame:
    df = query_frame(
        conn,
        "SELECT id AS session_id, date, source, activity_id FROM strength_sessions ORDER BY date",
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def strength_sets_frame(conn: Connection) -> pd.DataFrame:
    df = query_frame(
        conn,
        "SELECT s.session_id, ss.date, e.name AS exercise, e.primary_muscle_group AS muscle_group, "
        "e.is_lower_body, s.set_index, s.reps, s.load_kg, s.rir, s.is_warmup "
        "FROM strength_sets s "
        "JOIN strength_sessions ss ON ss.id = s.session_id "
        "JOIN exercises e ON e.id = s.exercise_id "
        "ORDER BY ss.date, s.session_id, s.set_index",
    )
    df["date"] = pd.to_datetime(df["date"])
    df["is_warmup"] = df["is_warmup"].astype(bool)
    df["is_lower_body"] = df["is_lower_body"].astype(bool)
    return df


def planned_frame(conn: Connection) -> pd.DataFrame:
    # `detail` arrives with migration 0005; a read-only connection may predate it.
    detail = "detail" if has_column(conn, "planned_workouts", "detail") else "NULL AS detail"
    df = query_frame(
        conn,
        "SELECT id, source, date, sport, workout_type, target_duration_s, target_distance_m, "  # noqa: S608
        f"notes, {detail} FROM planned_workouts ORDER BY date, id",
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def feedback_frame(conn: Connection) -> pd.DataFrame:
    df = query_frame(
        conn,
        "SELECT date, activity_id, source, source_id, rpe, soreness, fatigue, pain_flag, "
        "pain_location, illness_flag FROM feedback ORDER BY date, id",
    )
    df["date"] = pd.to_datetime(df["date"])
    df["pain_flag"] = df["pain_flag"].astype(bool)
    df["illness_flag"] = df["illness_flag"].astype(bool)
    return df


def data_sources(conn: Connection) -> set[str]:
    """Distinct `source` values across all source-tracked tables."""
    union = " UNION ".join(f"SELECT DISTINCT source FROM {t}" for t in _SOURCE_TABLES)  # noqa: S608
    return {row[0] for row in conn.execute(union)}
