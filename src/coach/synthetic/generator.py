"""Deterministic, seeded synthetic training data for tests and local development.

Models a base -> build -> taper block for a hybrid athlete: 5 runs/week (intervals, easy,
long run) plus 2 strength sessions, daily wellness that responds to recent load, random
poor-sleep nights, and one calf-pain episode around 60% of the block. All values are
fake and tagged `source="synthetic"`.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from coach.db.repositories import (
    ActivityRepository,
    FeedbackRepository,
    PlannedWorkoutRepository,
    StrengthRepository,
    WellnessRepository,
)
from coach.domain.models import (
    Activity,
    DailyWellness,
    Feedback,
    FeedbackSource,
    PlannedWorkout,
    PlanSource,
    Source,
    Sport,
    StrengthSession,
    StrengthSet,
    WorkoutType,
)

SYN = Source.SYNTHETIC

# run kind -> (pace s/km, avg HR, load per minute, aerobic TE, anaerobic TE, RPE range)
_RUNS: dict[str, tuple[float, int, float, float, float, tuple[int, int]]] = {
    "easy": (345.0, 140, 1.0, 2.4, 0.3, (3, 4)),
    "long": (355.0, 146, 1.15, 3.6, 0.5, (5, 6)),
    "intervals": (300.0, 159, 1.7, 3.3, 2.8, (7, 8)),
}

# weekday -> plan; weekday 0 = Monday
_WEEK: dict[int, tuple[str | None, str | None, int]] = {
    # (run kind, strength kind, base run minutes)
    0: (None, "full", 0),
    1: ("intervals", None, 55),
    2: ("easy", None, 45),
    3: ("easy", "upper_calf", 35),
    4: (None, None, 0),
    5: ("long", None, 100),
    6: ("easy", None, 40),
}

# strength kind -> [(exercise, sets, reps, base load kg or None, is lower body)]
_STRENGTH: dict[str, list[tuple[str, int, int, float | None, bool]]] = {
    "full": [
        ("Back Squat", 3, 5, 80.0, True),
        ("Romanian Deadlift", 3, 8, 70.0, True),
        ("Bench Press", 3, 6, 65.0, False),
        ("Pull-Up", 3, 6, None, False),
        ("Plank", 3, 1, None, False),
    ],
    "upper_calf": [
        ("Overhead Press", 3, 6, 40.0, False),
        ("Barbell Row", 3, 8, 60.0, False),
        ("Standing Calf Raise", 3, 12, 40.0, True),
        ("Bulgarian Split Squat", 2, 8, 20.0, True),
    ],
}


@dataclass(frozen=True)
class SyntheticDataset:
    activities: list[Activity] = field(default_factory=list)
    wellness: list[DailyWellness] = field(default_factory=list)
    # (activity source_id it belongs to, session) - ids are resolved when seeding
    strength_sessions: list[tuple[str | None, StrengthSession]] = field(default_factory=list)
    feedback: list[tuple[str | None, Feedback]] = field(default_factory=list)
    planned: list[PlannedWorkout] = field(default_factory=list)


@dataclass(frozen=True)
class SeedResult:
    activities: int
    wellness: int
    strength_sessions: int
    feedback: int
    planned: int


def _phase(index: int, days: int) -> tuple[str, float]:
    frac = index / max(days - 1, 1)
    if frac < 0.5:
        return "base", 0.85 + 0.3 * frac  # 0.85 -> 1.0
    if frac < 0.85:
        return "build", 1.0 + 0.4 * (frac - 0.5)  # 1.0 -> 1.14
    return "taper", 0.7


def generate(
    days: int = 120,
    *,
    end_date: date,
    seed: int = 42,
    tz: ZoneInfo | None = None,
    plan_days_ahead: int = 14,
) -> SyntheticDataset:
    if days < 1:
        raise ValueError("days must be >= 1")
    zone = tz or ZoneInfo("UTC")
    rng = random.Random(seed)
    start_date = end_date - timedelta(days=days - 1)
    pain_days = {int(days * 0.6) + k for k in range(3)} if days >= 14 else set()

    data = SyntheticDataset()
    fatigue = 0.0

    for i in range(days):
        day = start_date + timedelta(days=i)
        phase, volume = _phase(i, days)
        run_kind, strength_kind, base_minutes = _WEEK[day.weekday()]
        in_pain = i in pain_days
        poor_sleep = rng.random() < 0.1
        day_load = 0.0

        if in_pain:
            run_kind = None  # athlete skips running while the calf hurts
            data.feedback.append(
                (
                    None,
                    Feedback(
                        date=day,
                        source=FeedbackSource.SYNTHETIC,
                        source_id=f"syn-fb-{day}-pain",
                        soreness=6,
                        fatigue=5,
                        mood=2,
                        pain_flag=True,
                        pain_location="left calf",
                        notes="Calf tight on stairs.",
                    ),
                )
            )

        if run_kind is not None:
            pace, hr, load_pm, aer, ana, (rpe_lo, rpe_hi) = _RUNS[run_kind]
            if phase == "build" and run_kind == "easy" and day.weekday() == 3:
                run_kind, pace, hr, load_pm = "tempo", 315.0, 154, 1.45  # Thursday tempo
            minutes = base_minutes * volume * rng.uniform(0.93, 1.07)
            duration_s = int(minutes * 60)
            run_pace = pace * rng.uniform(0.97, 1.03)
            avg_hr = int(hr + rng.randint(-3, 3) + (3 if fatigue > 1.2 else 0))
            load = round(minutes * load_pm, 1)
            day_load += load
            hour = time(7, 0) if run_kind == "long" else time(6, 30)
            source_id = f"syn-act-{day}-run"
            data.activities.append(
                Activity(
                    source=SYN,
                    source_id=source_id,
                    sport=Sport.RUN,
                    start_utc=datetime.combine(day, hour, tzinfo=zone),
                    duration_s=duration_s,
                    distance_m=round(duration_s / run_pace * 1000, 1),
                    elevation_gain_m=round(rng.uniform(20, 120) * minutes / 45, 1),
                    avg_hr=avg_hr,
                    max_hr=min(avg_hr + rng.randint(12, 28), 198),
                    training_load=load,
                    aerobic_te=round(min(aer * rng.uniform(0.9, 1.1), 5.0), 1),
                    anaerobic_te=round(min(ana * rng.uniform(0.8, 1.2), 5.0), 1),
                    name=f"Synthetic {run_kind} run",
                )
            )
            data.feedback.append(
                (
                    source_id,
                    Feedback(
                        date=day,
                        source=FeedbackSource.SYNTHETIC,
                        source_id=f"syn-fb-{day}-run",
                        rpe=rng.randint(rpe_lo, rpe_hi) + (1 if poor_sleep else 0),
                        soreness=rng.randint(1, 4),
                        fatigue=min(10, rng.randint(2, 4) + int(fatigue)),
                        mood=rng.randint(3, 5),
                    ),
                )
            )

        if strength_kind is not None:
            week_no = i // 7
            sets: list[StrengthSet] = []
            for exercise, n_sets, reps, base_load, lower in _STRENGTH[strength_kind]:
                if in_pain and lower:
                    continue  # no lower-body lifting during the pain episode
                if phase == "taper":
                    n_sets = min(n_sets, 2)
                load_kg = None
                if base_load is not None:
                    progression = 2.5 * (week_no // 2) if phase != "taper" else 0.0
                    load_kg = base_load + min(progression, base_load * 0.15)
                for _ in range(n_sets):
                    sets.append(
                        StrengthSet(
                            exercise=exercise,
                            set_index=len(sets),
                            reps=reps,
                            load_kg=load_kg,
                            rir=rng.randint(1, 3),
                        )
                    )
            minutes = 25 + 2.2 * len(sets)
            load = round(minutes * 0.9, 1)
            day_load += load
            source_id = f"syn-act-{day}-strength"
            data.activities.append(
                Activity(
                    source=SYN,
                    source_id=source_id,
                    sport=Sport.STRENGTH,
                    start_utc=datetime.combine(day, time(18, 0), tzinfo=zone),
                    duration_s=int(minutes * 60),
                    avg_hr=rng.randint(105, 120),
                    max_hr=rng.randint(140, 160),
                    training_load=load,
                    name=f"Synthetic strength ({strength_kind})",
                )
            )
            data.strength_sessions.append(
                (
                    source_id,
                    StrengthSession(
                        source=SYN, source_id=f"syn-str-{day}", date=day, sets=tuple(sets)
                    ),
                )
            )
            data.feedback.append(
                (
                    source_id,
                    Feedback(
                        date=day,
                        source=FeedbackSource.SYNTHETIC,
                        source_id=f"syn-fb-{day}-strength",
                        rpe=rng.randint(6, 7),
                        soreness=rng.randint(2, 5),
                    ),
                )
            )

        # Wellness reflects the previous days' load (fatigue) and tonight's sleep.
        sleep_s = int((5.3 if poor_sleep else 7.4) * 3600 + rng.gauss(0, 1500))
        hrv = 62 - 4.0 * fatigue - (9 if poor_sleep else 0) - (5 if in_pain else 0)
        data.wellness.append(
            DailyWellness(
                source=SYN,
                date=day,
                sleep_duration_s=max(3 * 3600, min(sleep_s, 10 * 3600)),
                sleep_score=max(20, min(95, int(80 - (25 if poor_sleep else 0) + rng.gauss(0, 5)))),
                hrv_rmssd_ms=round(max(20.0, hrv + rng.gauss(0, 3)), 1),
                resting_hr=round(49 + 1.6 * fatigue + (3 if poor_sleep else 0) + rng.gauss(0, 1)),
                body_battery_high=rng.randint(45, 70) if poor_sleep else rng.randint(70, 95),
                body_battery_low=rng.randint(5, 30),
                stress_avg=rng.randint(20, 45),
                steps=rng.randint(6_000, 11_000) + int(day_load * 60),
            )
        )
        fatigue = 0.7 * fatigue + day_load / 120

    data.planned.extend(_plan(start_date, days, plan_days_ahead))
    return data


_STRENGTH_TYPES = {"full": WorkoutType.STRENGTH_FULL, "upper_calf": WorkoutType.STRENGTH_UPPER}


def _plan(start_date: date, days: int, days_ahead: int) -> list[PlannedWorkout]:
    """The athlete's intended week template for the block plus `days_ahead` future days.

    Deterministic (no RNG), so adding the plan never changes the generated actuals.
    Pain-episode days stay planned; they show up as missed sessions on the dashboard.
    """
    planned: list[PlannedWorkout] = []
    for i in range(days + max(days_ahead, 0)):
        day = start_date + timedelta(days=i)
        phase, volume = _phase(min(i, days - 1), days) if i < days else ("taper", 0.7)
        run_kind, strength_kind, base_minutes = _WEEK[day.weekday()]
        if run_kind is not None:
            kind = "tempo" if phase == "build" and day.weekday() == 3 else run_kind
            pace = 315.0 if kind == "tempo" else _RUNS[run_kind][0]
            duration_s = int(base_minutes * volume * 60)
            planned.append(
                PlannedWorkout(
                    source=PlanSource.SYNTHETIC,
                    source_id=f"syn-plan-{day}-run",
                    date=day,
                    sport=Sport.RUN,
                    workout_type=WorkoutType(kind),
                    target_duration_s=duration_s,
                    target_distance_m=round(duration_s / pace * 1000, -2),
                )
            )
        if strength_kind is not None:
            planned.append(
                PlannedWorkout(
                    source=PlanSource.SYNTHETIC,
                    source_id=f"syn-plan-{day}-strength",
                    date=day,
                    sport=Sport.STRENGTH,
                    workout_type=_STRENGTH_TYPES[strength_kind],
                    target_duration_s=45 * 60,
                )
            )
    return planned


def seed_database(conn: sqlite3.Connection, dataset: SyntheticDataset) -> SeedResult:
    """Upsert a dataset. Idempotent: seeding the same dataset twice changes nothing."""
    activities = ActivityRepository(conn)
    ids = {a.source_id: activities.upsert(a) for a in dataset.activities}

    wellness = WellnessRepository(conn)
    for w in dataset.wellness:
        wellness.upsert(w)

    strength = StrengthRepository(conn)
    for activity_ref, session in dataset.strength_sessions:
        linked = session.model_copy(update={"activity_id": ids.get(activity_ref or "")})
        strength.upsert_session(linked)

    feedback = FeedbackRepository(conn)
    plans = PlannedWorkoutRepository(conn)
    for workout in dataset.planned:
        plans.upsert(workout)

    for activity_ref, fb in dataset.feedback:
        feedback.add(fb.model_copy(update={"activity_id": ids.get(activity_ref or "")}))

    return SeedResult(
        activities=len(dataset.activities),
        wellness=len(dataset.wellness),
        strength_sessions=len(dataset.strength_sessions),
        feedback=len(dataset.feedback),
        planned=len(dataset.planned),
    )
