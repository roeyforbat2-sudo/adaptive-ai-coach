"""Repositories: the only place that reads/writes domain tables.

Writes are idempotent upserts keyed on (source, source_id) or (source, date), so re-running
a sync or the synthetic seeder never duplicates rows. Each write method commits its own
transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from coach.db.connection import Connection
from coach.domain.models import (
    Activity,
    DailyWellness,
    Exercise,
    Feedback,
    Goal,
    PlannedWorkout,
    PlanSource,
    Source,
    StrengthSession,
    StrengthSet,
)
from coach.logging import redact
from coach.timeutil import from_db, to_db, utc_now


class UnknownExerciseError(LookupError):
    pass


def _returning_id(conn: Connection, sql: str, params: dict[str, Any]) -> int:
    rows = conn.execute(sql, params).fetchall()
    return int(rows[0][0])


def _row_dict(row: Any, *drop: str) -> dict[str, Any]:
    return {k: row[k] for k in row.keys() if k not in drop}  # noqa: SIM118 - Row has no __iter__ of keys


class ActivityRepository:
    _COLUMNS = (
        "source",
        "source_id",
        "sport",
        "start_utc",
        "duration_s",
        "distance_m",
        "elevation_gain_m",
        "avg_hr",
        "max_hr",
        "training_load",
        "aerobic_te",
        "anaerobic_te",
        "name",
    )

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, activity: Activity) -> int:
        params = activity.model_dump(mode="json", exclude={"id"})
        params["start_utc"] = to_db(activity.start_utc)
        params["now"] = to_db(utc_now())
        cols = ", ".join(self._COLUMNS)
        values = ", ".join(f":{c}" for c in self._COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in self._COLUMNS[2:])
        sql = (
            f"INSERT INTO activities ({cols}, created_utc, updated_utc) "  # noqa: S608 - fixed column names
            f"VALUES ({values}, :now, :now) "
            f"ON CONFLICT (source, source_id) DO UPDATE SET {updates}, updated_utc = :now "
            "RETURNING id"
        )
        with self._conn:
            return _returning_id(self._conn, sql, params)

    def get(self, source: Source, source_id: str) -> Activity | None:
        row = self._conn.execute(
            "SELECT * FROM activities WHERE source = ? AND source_id = ?", (source, source_id)
        ).fetchone()
        return self._to_model(row) if row else None

    def list_between(self, start: datetime, end: datetime) -> list[Activity]:
        """Activities with start_utc in [start, end)."""
        rows = self._conn.execute(
            "SELECT * FROM activities WHERE start_utc >= ? AND start_utc < ? ORDER BY start_utc",
            (to_db(start), to_db(end)),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0])

    @staticmethod
    def _to_model(row: Any) -> Activity:
        data = _row_dict(row, "created_utc", "updated_utc")
        data["start_utc"] = from_db(data["start_utc"])
        return Activity.model_validate(data)


class WellnessRepository:
    _COLUMNS = (
        "date",
        "source",
        "sleep_duration_s",
        "sleep_score",
        "hrv_rmssd_ms",
        "resting_hr",
        "body_battery_high",
        "body_battery_low",
        "stress_avg",
        "steps",
    )

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, wellness: DailyWellness) -> int:
        params = wellness.model_dump(mode="json", exclude={"id"})
        params["now"] = to_db(utc_now())
        cols = ", ".join(self._COLUMNS)
        values = ", ".join(f":{c}" for c in self._COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in self._COLUMNS[2:])
        sql = (
            f"INSERT INTO daily_wellness ({cols}, updated_utc) VALUES ({values}, :now) "  # noqa: S608
            f"ON CONFLICT (source, date) DO UPDATE SET {updates}, updated_utc = :now "
            "RETURNING id"
        )
        with self._conn:
            return _returning_id(self._conn, sql, params)

    def list_between(self, start: date, end: date) -> list[DailyWellness]:
        """Inclusive date range."""
        rows = self._conn.execute(
            "SELECT * FROM daily_wellness WHERE date BETWEEN ? AND ? ORDER BY date, source",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [DailyWellness.model_validate(_row_dict(r, "updated_utc")) for r in rows]


class FeedbackRepository:
    _COLUMNS = (
        "date",
        "activity_id",
        "source",
        "source_id",
        "rpe",
        "soreness",
        "fatigue",
        "mood",
        "sleep_quality",
        "pain_flag",
        "pain_location",
        "illness_flag",
        "notes",
    )

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(self, feedback: Feedback) -> int:
        """Insert, or update on (source, source_id) conflict. NULL source_id never conflicts."""
        params = feedback.model_dump(mode="json", exclude={"id"})
        params["now"] = to_db(utc_now())
        cols = ", ".join(self._COLUMNS)
        values = ", ".join(f":{c}" for c in self._COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in self._COLUMNS if c != "source_id")
        sql = (
            f"INSERT INTO feedback ({cols}, created_utc) VALUES ({values}, :now) "  # noqa: S608
            f"ON CONFLICT (source, source_id) DO UPDATE SET {updates} "
            "RETURNING id"
        )
        with self._conn:
            return _returning_id(self._conn, sql, params)

    def list_between(self, start: date, end: date) -> list[Feedback]:
        rows = self._conn.execute(
            "SELECT * FROM feedback WHERE date BETWEEN ? AND ? ORDER BY date, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [Feedback.model_validate(_row_dict(r, "created_utc")) for r in rows]


class PlannedWorkoutRepository:
    _COLUMNS = (
        "source",
        "source_id",
        "date",
        "sport",
        "workout_type",
        "target_duration_s",
        "target_distance_m",
        "notes",
        "detail",
    )

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert(self, workout: PlannedWorkout) -> int:
        params = workout.model_dump(mode="json", exclude={"id"})
        params["now"] = to_db(utc_now())
        cols = ", ".join(self._COLUMNS)
        values = ", ".join(f":{c}" for c in self._COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in self._COLUMNS[2:])
        sql = (
            f"INSERT INTO planned_workouts ({cols}, created_utc, updated_utc) "  # noqa: S608
            f"VALUES ({values}, :now, :now) "
            f"ON CONFLICT (source, source_id) DO UPDATE SET {updates}, updated_utc = :now "
            "RETURNING id"
        )
        with self._conn:
            return _returning_id(self._conn, sql, params)

    def get(self, source: PlanSource, source_id: str) -> PlannedWorkout | None:
        row = self._conn.execute(
            "SELECT * FROM planned_workouts WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        return self._to_model(row) if row else None

    def list_between(self, start: date, end: date) -> list[PlannedWorkout]:
        rows = self._conn.execute(
            "SELECT * FROM planned_workouts WHERE date BETWEEN ? AND ? ORDER BY date, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def delete_missing(
        self, source: PlanSource, start: date, end: date, keep_source_ids: set[str]
    ) -> int:
        """Remove plans from `source` dated in [start, end] that are no longer in `keep`.

        Used when re-syncing a derived source (e.g., the calendar window) so events that were
        deleted or changed in the calendar do not linger here.
        """
        rows = self._conn.execute(
            "SELECT id, source_id FROM planned_workouts WHERE source = ? AND date BETWEEN ? AND ?",
            (source.value, start.isoformat(), end.isoformat()),
        ).fetchall()
        stale = [(r["id"],) for r in rows if r["source_id"] not in keep_source_ids]
        with self._conn:
            self._conn.executemany("DELETE FROM planned_workouts WHERE id = ?", stale)
        return len(stale)

    @staticmethod
    def _to_model(row: Any) -> PlannedWorkout:
        return PlannedWorkout.model_validate(_row_dict(row, "created_utc", "updated_utc"))


class GoalRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(self, goal: Goal) -> int:
        with self._conn:
            new_id = int(
                self._conn.execute(
                    "INSERT INTO goals (kind, description, target_date, target_value, unit, "
                    "priority, active, created_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                    (
                        goal.kind.value,
                        goal.description,
                        goal.target_date.isoformat() if goal.target_date else None,
                        goal.target_value,
                        goal.unit,
                        goal.priority,
                        goal.active,
                        to_db(utc_now()),
                    ),
                ).fetchone()[0]
            )
        return new_id

    def set_active(self, goal_id: int, active: bool) -> bool:
        with self._conn:
            cur = self._conn.execute("UPDATE goals SET active = ? WHERE id = ?", (active, goal_id))
        return bool(cur.rowcount == 1)

    def list(self, *, active_only: bool = True) -> list[Goal]:
        where = "WHERE active = 1 " if active_only else ""
        rows = self._conn.execute(
            f"SELECT * FROM goals {where}ORDER BY target_date IS NULL, target_date, priority"  # noqa: S608
        ).fetchall()
        return [Goal.model_validate(_row_dict(r, "created_utc")) for r in rows]


class CoachOutputRepository:
    """Cached coach outputs keyed by (kind, ref)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def save(
        self,
        kind: str,
        ref: str,
        *,
        model: str,
        prompt_version: str,
        output: dict[str, Any],
        guardrails: list[dict[str, Any]],
        fallback_used: bool,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> int:
        with self._conn:
            rows = self._conn.execute(
                "INSERT INTO coach_outputs (kind, ref, model, prompt_version, output_json, "
                "guardrails_json, fallback_used, input_tokens, output_tokens, created_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (kind, ref) DO UPDATE SET "
                "model = excluded.model, prompt_version = excluded.prompt_version, "
                "output_json = excluded.output_json, guardrails_json = excluded.guardrails_json, "
                "fallback_used = excluded.fallback_used, input_tokens = excluded.input_tokens, "
                "output_tokens = excluded.output_tokens, created_utc = excluded.created_utc "
                "RETURNING id",
                (
                    kind,
                    ref,
                    model,
                    prompt_version,
                    json.dumps(output, ensure_ascii=False),
                    json.dumps(guardrails, ensure_ascii=False),
                    fallback_used,
                    input_tokens,
                    output_tokens,
                    to_db(utc_now()),
                ),
            ).fetchall()
        return int(rows[0][0])

    def get(self, kind: str, ref: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM coach_outputs WHERE kind = ? AND ref = ?", (kind, ref)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["output"] = json.loads(data.pop("output_json"))
        data["guardrails"] = json.loads(data.pop("guardrails_json"))
        return data


class ChatRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        model: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> int:
        with self._conn:
            new_id = int(
                self._conn.execute(
                    "INSERT INTO chat_messages (conversation_id, role, content, model, "
                    "input_tokens, output_tokens, created_utc) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                    (
                        conversation_id,
                        role,
                        content,
                        model,
                        input_tokens,
                        output_tokens,
                        to_db(utc_now()),
                    ),
                ).fetchone()[0]
            )
        return new_id

    def history(self, conversation_id: str, limit: int = 40) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT role, content, created_utc FROM chat_messages WHERE conversation_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


class ExerciseRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(self, exercise: Exercise) -> int:
        with self._conn:
            new_id = int(
                self._conn.execute(
                    "INSERT INTO exercises (name, primary_muscle_group, is_lower_body, unilateral) "
                    "VALUES (?, ?, ?, ?) RETURNING id",
                    (
                        exercise.name,
                        exercise.primary_muscle_group.value,
                        exercise.is_lower_body,
                        exercise.unilateral,
                    ),
                ).fetchone()[0]
            )
        return new_id

    def get_by_name(self, name: str) -> Exercise | None:
        row = self._conn.execute(
            "SELECT * FROM exercises WHERE lower(name) = lower(?)", (name,)
        ).fetchone()
        return Exercise.model_validate(dict(row)) if row else None

    def list_all(self) -> list[Exercise]:
        rows = self._conn.execute("SELECT * FROM exercises ORDER BY name").fetchall()
        return [Exercise.model_validate(dict(r)) for r in rows]


class StrengthRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def upsert_session(self, session: StrengthSession) -> int:
        """Upsert the session and replace its sets atomically."""
        exercise_ids = self._resolve_exercises(session.sets)
        now = to_db(utc_now())
        with self._conn:
            session_id = _returning_id(
                self._conn,
                "INSERT INTO strength_sessions "
                "(activity_id, date, source, source_id, notes, created_utc, updated_utc) "
                "VALUES (:activity_id, :date, :source, :source_id, :notes, :now, :now) "
                "ON CONFLICT (source, source_id) DO UPDATE SET activity_id = excluded.activity_id, "
                "date = excluded.date, notes = excluded.notes, updated_utc = :now RETURNING id",
                {
                    "activity_id": session.activity_id,
                    "date": session.date.isoformat(),
                    "source": session.source.value,
                    "source_id": session.source_id,
                    "notes": session.notes,
                    "now": now,
                },
            )
            self._conn.execute("DELETE FROM strength_sets WHERE session_id = ?", (session_id,))
            self._conn.executemany(
                "INSERT INTO strength_sets "
                "(session_id, exercise_id, set_index, reps, load_kg, rir, is_warmup) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        session_id,
                        exercise_ids[s.exercise.casefold()],
                        s.set_index,
                        s.reps,
                        s.load_kg,
                        s.rir,
                        s.is_warmup,
                    )
                    for s in session.sets
                ],
            )
        return session_id

    def get_session(self, source: Source, source_id: str) -> StrengthSession | None:
        row = self._conn.execute(
            "SELECT * FROM strength_sessions WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        return self._to_model(row) if row else None

    def list_sessions_between(self, start: date, end: date) -> list[StrengthSession]:
        rows = self._conn.execute(
            "SELECT * FROM strength_sessions WHERE date BETWEEN ? AND ? ORDER BY date, id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def _resolve_exercises(self, sets: tuple[StrengthSet, ...]) -> dict[str, int]:
        ids: dict[str, int] = {}
        for name in {s.exercise for s in sets}:
            row = self._conn.execute(
                "SELECT id FROM exercises WHERE lower(name) = lower(?)", (name,)
            ).fetchone()
            if row is None:
                raise UnknownExerciseError(f"unknown exercise: {name!r}")
            ids[name.casefold()] = int(row[0])
        return ids

    def _to_model(self, row: Any) -> StrengthSession:
        set_rows = self._conn.execute(
            "SELECT e.name AS exercise, s.set_index, s.reps, s.load_kg, s.rir, s.is_warmup "
            "FROM strength_sets s JOIN exercises e ON e.id = s.exercise_id "
            "WHERE s.session_id = ? ORDER BY s.set_index",
            (row["id"],),
        ).fetchall()
        data = _row_dict(row, "created_utc", "updated_utc")
        data["sets"] = tuple(StrengthSet.model_validate(dict(r)) for r in set_rows)
        return StrengthSession.model_validate(data)


@dataclass(frozen=True)
class CalendarEventRow:
    calendar_id: str
    event_id: str
    etag: str | None
    start_utc: datetime
    end_utc: datetime
    all_day: bool
    busy: bool
    is_training: bool
    title: str | None  # must be None unless is_training


class CalendarEventRepository:
    """Read-only cache of calendar events (privacy: titles only for training events)."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def replace_window(
        self, calendar_id: str, start: datetime, end: datetime, rows: list[CalendarEventRow]
    ) -> int:
        """Upsert `rows` and delete cached events of this calendar starting in [start, end)
        that are no longer returned. Returns the number of deleted cache rows."""
        now = to_db(utc_now())
        keep = {r.event_id for r in rows}
        with self._conn:
            for r in rows:
                self._conn.execute(
                    "INSERT INTO calendar_events (calendar_id, event_id, etag, start_utc, end_utc, "
                    "all_day, busy, is_training, title, last_synced_utc) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (calendar_id, event_id) DO UPDATE SET etag = excluded.etag, "
                    "start_utc = excluded.start_utc, end_utc = excluded.end_utc, "
                    "all_day = excluded.all_day, busy = excluded.busy, "
                    "is_training = excluded.is_training, title = excluded.title, "
                    "last_synced_utc = excluded.last_synced_utc",
                    (
                        r.calendar_id,
                        r.event_id,
                        r.etag,
                        to_db(r.start_utc),
                        to_db(r.end_utc),
                        r.all_day,
                        r.busy,
                        r.is_training,
                        r.title if r.is_training else None,
                        now,
                    ),
                )
            existing = self._conn.execute(
                "SELECT id, event_id FROM calendar_events "
                "WHERE calendar_id = ? AND start_utc >= ? AND start_utc < ?",
                (calendar_id, to_db(start), to_db(end)),
            ).fetchall()
            stale = [(row["id"],) for row in existing if row["event_id"] not in keep]
            self._conn.executemany("DELETE FROM calendar_events WHERE id = ?", stale)
        return len(stale)

    def count(self, *, training_only: bool = False) -> int:
        sql = (
            "SELECT COUNT(*) FROM calendar_events WHERE is_training = 1"
            if training_only
            else "SELECT COUNT(*) FROM calendar_events"
        )
        return int(self._conn.execute(sql).fetchone()[0])


class SyncStateRepository:
    """Per-source sync bookkeeping. Error text is redacted before storage."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record_success(self, source: str, cursor: str | None) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO sync_state (source, cursor, last_success_utc, last_error, "
                "consecutive_failures) VALUES (?, ?, ?, NULL, 0) "
                "ON CONFLICT (source) DO UPDATE SET cursor = excluded.cursor, "
                "last_success_utc = excluded.last_success_utc, last_error = NULL, "
                "consecutive_failures = 0",
                (source, cursor, to_db(utc_now())),
            )

    def record_failure(self, source: str, error: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO sync_state (source, last_error, consecutive_failures) "
                "VALUES (?, ?, 1) ON CONFLICT (source) DO UPDATE SET "
                "last_error = excluded.last_error, "
                "consecutive_failures = sync_state.consecutive_failures + 1",
                (source, redact(error)[:500]),
            )

    def get(self, source: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM sync_state WHERE source = ?", (source,)).fetchone()
        return dict(row) if row else None


class AuditLogRepository:
    """Append-only (enforced by DB triggers). Details are redacted before storage."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def record(
        self,
        actor: str,
        action: str,
        entity: str,
        entity_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        details_json = redact(json.dumps(details or {}, sort_keys=True, default=str))
        with self._conn:
            new_id = int(
                self._conn.execute(
                    "INSERT INTO audit_log (ts_utc, actor, action, entity, entity_id, "
                    "details_json) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                    (to_db(utc_now()), actor, action, entity, entity_id, details_json),
                ).fetchone()[0]
            )
        return new_id


def has_synthetic_rows(conn: Connection) -> bool:
    """True if any source-tracked table holds synthetic demo data (never mix with real data)."""
    tables = ("activities", "daily_wellness", "strength_sessions", "feedback", "planned_workouts")
    return any(
        conn.execute(f"SELECT 1 FROM {t} WHERE source = 'synthetic' LIMIT 1").fetchone()  # noqa: S608
        for t in tables
    )


def count_non_synthetic_rows(conn: Connection) -> int:
    """Rows in source-tracked tables that did not come from the synthetic generator.

    Used to refuse seeding fake data into a database that holds real data.
    """
    total = 0
    for table in (
        "activities",
        "daily_wellness",
        "strength_sessions",
        "feedback",
        "planned_workouts",
    ):
        total += int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE source != 'synthetic'"  # noqa: S608
            ).fetchone()[0]
        )
    return total
