from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from coach.db.repositories import (
    ActivityRepository,
    AuditLogRepository,
    ExerciseRepository,
    FeedbackRepository,
    PlannedWorkoutRepository,
    StrengthRepository,
    UnknownExerciseError,
    WellnessRepository,
    count_non_synthetic_rows,
)
from coach.domain import (
    DailyWellness,
    Exercise,
    Feedback,
    FeedbackSource,
    MuscleGroup,
    PlannedWorkout,
    PlanSource,
    Source,
    StrengthSession,
    StrengthSet,
)
from tests.conftest import DAY, make_activity


class TestActivities:
    def test_upsert_is_idempotent(self, conn: sqlite3.Connection) -> None:
        repo = ActivityRepository(conn)
        first = repo.upsert(make_activity())
        second = repo.upsert(make_activity())
        assert first == second
        assert repo.count() == 1

    def test_upsert_updates_fields_keeps_id(self, conn: sqlite3.Connection) -> None:
        repo = ActivityRepository(conn)
        activity_id = repo.upsert(make_activity())
        repo.upsert(make_activity(duration_s=3000, name="Renamed"))
        stored = repo.get(Source.SYNTHETIC, "syn-1")
        assert stored is not None
        assert stored.id == activity_id
        assert stored.duration_s == 3000
        assert stored.name == "Renamed"

    def test_round_trip_preserves_values(self, conn: sqlite3.Connection) -> None:
        repo = ActivityRepository(conn)
        original = make_activity()
        repo.upsert(original)
        stored = repo.get(Source.SYNTHETIC, "syn-1")
        assert stored is not None
        assert stored.model_dump(exclude={"id"}) == original.model_dump(exclude={"id"})

    def test_same_source_id_from_different_sources_are_distinct(
        self, conn: sqlite3.Connection
    ) -> None:
        repo = ActivityRepository(conn)
        repo.upsert(make_activity(source=Source.SYNTHETIC))
        repo.upsert(make_activity(source=Source.MANUAL))
        assert repo.count() == 2

    def test_list_between_is_half_open_and_ordered(self, conn: sqlite3.Connection) -> None:
        repo = ActivityRepository(conn)
        for i, hour in enumerate([9, 5, 7]):
            repo.upsert(
                make_activity(
                    source_id=f"syn-{i}", start_utc=datetime(2026, 11, 1, hour, tzinfo=UTC)
                )
            )
        found = repo.list_between(
            datetime(2026, 11, 1, 5, tzinfo=UTC), datetime(2026, 11, 1, 9, tzinfo=UTC)
        )
        assert [a.start_utc.hour for a in found] == [5, 7]

    def test_get_missing_returns_none(self, conn: sqlite3.Connection) -> None:
        assert ActivityRepository(conn).get(Source.GARMIN, "nope") is None


class TestWellness:
    def test_upsert_by_source_and_date(self, conn: sqlite3.Connection) -> None:
        repo = WellnessRepository(conn)
        repo.upsert(DailyWellness(source=Source.SYNTHETIC, date=DAY, hrv_rmssd_ms=60.0))
        repo.upsert(DailyWellness(source=Source.SYNTHETIC, date=DAY, hrv_rmssd_ms=55.5))
        rows = repo.list_between(DAY, DAY)
        assert len(rows) == 1
        assert rows[0].hrv_rmssd_ms == 55.5

    def test_missing_values_stay_missing(self, conn: sqlite3.Connection) -> None:
        """Never invent data: absent metrics are stored and returned as None."""
        repo = WellnessRepository(conn)
        repo.upsert(DailyWellness(source=Source.SYNTHETIC, date=DAY, resting_hr=50))
        (row,) = repo.list_between(DAY, DAY)
        assert row.resting_hr == 50
        assert row.hrv_rmssd_ms is None
        assert row.sleep_duration_s is None


class TestFeedback:
    def test_manual_entries_without_source_id_always_insert(self, conn: sqlite3.Connection) -> None:
        repo = FeedbackRepository(conn)
        repo.add(Feedback(date=DAY, rpe=5))
        repo.add(Feedback(date=DAY, rpe=6))
        assert len(repo.list_between(DAY, DAY)) == 2

    def test_entries_with_source_id_are_idempotent(self, conn: sqlite3.Connection) -> None:
        repo = FeedbackRepository(conn)
        fb = Feedback(date=DAY, source=FeedbackSource.SYNTHETIC, source_id="fb-1", rpe=5)
        first = repo.add(fb)
        second = repo.add(fb.model_copy(update={"rpe": 7}))
        assert first == second
        (stored,) = repo.list_between(DAY, DAY)
        assert stored.rpe == 7

    def test_links_to_activity(self, conn: sqlite3.Connection) -> None:
        activity_id = ActivityRepository(conn).upsert(make_activity())
        FeedbackRepository(conn).add(Feedback(date=DAY, activity_id=activity_id, rpe=4))
        (stored,) = FeedbackRepository(conn).list_between(DAY, DAY)
        assert stored.activity_id == activity_id

    def test_unknown_activity_rejected_by_foreign_key(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            FeedbackRepository(conn).add(Feedback(date=DAY, activity_id=999, rpe=4))


class TestStrength:
    @staticmethod
    def _session(*sets: StrengthSet, notes: str | None = None) -> StrengthSession:
        return StrengthSession(
            source=Source.MANUAL, source_id="m-1", date=DAY, sets=sets, notes=notes
        )

    def test_round_trip_with_sets(self, conn: sqlite3.Connection) -> None:
        repo = StrengthRepository(conn)
        session = self._session(
            StrengthSet(exercise="Back Squat", set_index=0, reps=5, load_kg=80, rir=2),
            StrengthSet(exercise="Pull-Up", set_index=1, reps=6),
        )
        repo.upsert_session(session)
        stored = repo.get_session(Source.MANUAL, "m-1")
        assert stored is not None
        assert stored.sets == session.sets

    def test_exercise_lookup_is_case_insensitive(self, conn: sqlite3.Connection) -> None:
        repo = StrengthRepository(conn)
        repo.upsert_session(self._session(StrengthSet(exercise="back squat", set_index=0, reps=5)))
        stored = repo.get_session(Source.MANUAL, "m-1")
        assert stored is not None
        assert stored.sets[0].exercise == "Back Squat"

    def test_upsert_replaces_sets(self, conn: sqlite3.Connection) -> None:
        repo = StrengthRepository(conn)
        squat = StrengthSet(exercise="Back Squat", set_index=0, reps=5, load_kg=80)
        first = repo.upsert_session(
            self._session(squat, StrengthSet(exercise="Plank", set_index=1, reps=1))
        )
        second = repo.upsert_session(self._session(squat, notes="dropped the plank"))
        assert first == second
        stored = repo.get_session(Source.MANUAL, "m-1")
        assert stored is not None
        assert len(stored.sets) == 1
        assert stored.notes == "dropped the plank"

    def test_unknown_exercise_rejected_without_partial_write(
        self, conn: sqlite3.Connection
    ) -> None:
        repo = StrengthRepository(conn)
        bad = self._session(StrengthSet(exercise="Made-Up Lift", set_index=0, reps=5))
        with pytest.raises(UnknownExerciseError):
            repo.upsert_session(bad)
        assert repo.get_session(Source.MANUAL, "m-1") is None

    def test_deleting_session_cascades_to_sets(self, conn: sqlite3.Connection) -> None:
        repo = StrengthRepository(conn)
        session_id = repo.upsert_session(
            self._session(StrengthSet(exercise="Back Squat", set_index=0, reps=5))
        )
        conn.execute("DELETE FROM strength_sessions WHERE id = ?", (session_id,))
        assert conn.execute("SELECT COUNT(*) FROM strength_sets").fetchone()[0] == 0

    def test_list_sessions_between(self, conn: sqlite3.Connection) -> None:
        repo = StrengthRepository(conn)
        repo.upsert_session(self._session(StrengthSet(exercise="Plank", set_index=0, reps=1)))
        assert len(repo.list_sessions_between(DAY, DAY)) == 1
        assert repo.list_sessions_between(date(2026, 12, 1), date(2026, 12, 31)) == []


class TestExercises:
    def test_seeded_and_addable(self, conn: sqlite3.Connection) -> None:
        repo = ExerciseRepository(conn)
        squat = repo.get_by_name("Back Squat")
        assert squat is not None and squat.is_lower_body
        repo.add(
            Exercise(
                name="Nordic Curl", primary_muscle_group=MuscleGroup.HAMSTRINGS, is_lower_body=True
            )
        )
        assert repo.get_by_name("nordic curl") is not None
        with pytest.raises(sqlite3.IntegrityError):
            repo.add(
                Exercise(
                    name="NORDIC CURL",
                    primary_muscle_group=MuscleGroup.HAMSTRINGS,
                    is_lower_body=True,
                )
            )


def test_audit_log_redacts_details(conn: sqlite3.Connection) -> None:
    AuditLogRepository(conn).record(
        "system", "token_refresh", "garmin", details={"access_token": "fake-token-value-123"}
    )
    (stored,) = conn.execute("SELECT details_json FROM audit_log").fetchall()
    assert "fake-token-value-123" not in stored[0]
    json.loads(stored[0])  # still valid JSON


def test_count_non_synthetic_rows(conn: sqlite3.Connection) -> None:
    ActivityRepository(conn).upsert(make_activity())
    assert count_non_synthetic_rows(conn) == 0
    FeedbackRepository(conn).add(Feedback(date=DAY, rpe=5))  # manual
    assert count_non_synthetic_rows(conn) == 1


class TestPlannedWorkouts:
    @staticmethod
    def _plan(**overrides: object) -> PlannedWorkout:
        data: dict[str, object] = {
            "source": PlanSource.SYNTHETIC,
            "source_id": "p-1",
            "date": DAY,
            "sport": "run",
            "workout_type": "easy",
            "target_duration_s": 2700,
        }
        data.update(overrides)
        return PlannedWorkout.model_validate(data)

    def test_upsert_idempotent_and_round_trip(self, conn: sqlite3.Connection) -> None:
        repo = PlannedWorkoutRepository(conn)
        first = repo.upsert(self._plan())
        second = repo.upsert(self._plan(target_duration_s=3000))
        assert first == second
        stored = repo.get(PlanSource.SYNTHETIC, "p-1")
        assert stored is not None and stored.target_duration_s == 3000
        assert repo.list_between(DAY, DAY) == [stored]

    def test_workout_type_must_match_sport(self) -> None:
        with pytest.raises(ValidationError, match="does not match"):
            self._plan(sport="strength", workout_type="easy")
        with pytest.raises(ValidationError, match="does not match"):
            self._plan(sport="run", workout_type="strength_full")

    def test_counted_as_non_synthetic_when_manual(self, conn: sqlite3.Connection) -> None:
        PlannedWorkoutRepository(conn).upsert(self._plan(source=PlanSource.MANUAL))
        assert count_non_synthetic_rows(conn) == 1
