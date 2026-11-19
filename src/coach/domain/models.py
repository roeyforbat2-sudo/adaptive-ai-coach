"""Domain models. These are the validation boundary for all external (untrusted) data.

Integrations map raw payloads into these models; anything that does not validate is
rejected before it reaches the database or the LLM. Units are SI.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from coach.timeutil import ensure_utc


class Source(StrEnum):
    GARMIN = "garmin"
    FILE_IMPORT = "file_import"
    MANUAL = "manual"
    SYNTHETIC = "synthetic"


class FeedbackSource(StrEnum):
    MANUAL = "manual"
    CHAT = "chat"  # extracted from chat, saved only after the user confirms
    SYNTHETIC = "synthetic"


class Sport(StrEnum):
    RUN = "run"
    TRAIL_RUN = "trail_run"
    TREADMILL = "treadmill"
    STRENGTH = "strength"
    BIKE = "bike"
    WALK = "walk"
    OTHER = "other"


class PlanSource(StrEnum):
    MANUAL = "manual"
    SYNTHETIC = "synthetic"
    ENGINE = "engine"  # recommendation engine (Phase 4)
    CALENDAR = "calendar"  # imported from Google Calendar (Phase 6)


class WorkoutType(StrEnum):
    EASY = "easy"
    LONG = "long"
    TEMPO = "tempo"
    INTERVALS = "intervals"
    RECOVERY = "recovery"
    STRENGTH_FULL = "strength_full"
    STRENGTH_UPPER = "strength_upper"
    STRENGTH_LOWER = "strength_lower"

    @property
    def is_strength(self) -> bool:
        return self.value.startswith("strength")


class GoalKind(StrEnum):
    RACE = "race"
    VOLUME = "volume"
    STRENGTH = "strength"
    BODY = "body"


class MuscleGroup(StrEnum):
    QUADS = "quads"
    HAMSTRINGS = "hamstrings"
    GLUTES = "glutes"
    CALVES = "calves"
    CORE = "core"
    BACK = "back"
    CHEST = "chest"
    SHOULDERS = "shoulders"
    ARMS = "arms"


# --- reusable field types -------------------------------------------------------------

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean_text(max_len: int) -> BeforeValidator:
    """Untrusted free text: strip control chars and whitespace, truncate (never reject)."""

    def clean(value: object) -> object:
        if not isinstance(value, str):
            return value
        return _CONTROL_CHARS.sub("", value).strip()[:max_len] or None

    return BeforeValidator(clean)


ActivityName = Annotated[str | None, _clean_text(200)]
ShortText = Annotated[str | None, _clean_text(100)]
LongText = Annotated[str | None, _clean_text(2000)]
SourceId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[\w.:\-]+$")]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(ensure_utc)]
HeartRate = Annotated[int, Field(ge=20, le=250)]
Score0to10 = Annotated[int, Field(ge=0, le=10)]
Score1to5 = Annotated[int, Field(ge=1, le=5)]
Percent = Annotated[int, Field(ge=0, le=100)]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


# --- training -------------------------------------------------------------------------


class Activity(DomainModel):
    id: int | None = None  # set when read from the database
    source: Source
    source_id: SourceId
    sport: Sport
    start_utc: UtcDatetime
    duration_s: int = Field(gt=0, le=7 * 86_400)
    distance_m: float | None = Field(default=None, ge=0, le=1_000_000)
    elevation_gain_m: float | None = Field(default=None, ge=0, le=20_000)
    avg_hr: HeartRate | None = None
    max_hr: HeartRate | None = None
    training_load: float | None = Field(default=None, ge=0, le=2_000)
    aerobic_te: float | None = Field(default=None, ge=0, le=5)
    anaerobic_te: float | None = Field(default=None, ge=0, le=5)
    name: ActivityName = None

    @model_validator(mode="after")
    def _hr_order(self) -> Self:
        if self.avg_hr is not None and self.max_hr is not None and self.avg_hr > self.max_hr:
            raise ValueError("avg_hr cannot exceed max_hr")
        return self


class Exercise(DomainModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    primary_muscle_group: MuscleGroup
    is_lower_body: bool
    unilateral: bool = False


class StrengthSet(DomainModel):
    exercise: str = Field(min_length=1, max_length=100)  # Exercise.name
    set_index: int = Field(ge=0)
    reps: int = Field(ge=0, le=200)
    load_kg: float | None = Field(default=None, ge=0, le=1_000)
    rir: int | None = Field(default=None, ge=0, le=10)  # reps in reserve
    is_warmup: bool = False


class StrengthSession(DomainModel):
    id: int | None = None
    source: Source
    source_id: SourceId
    date: date
    activity_id: int | None = Field(default=None, gt=0)
    notes: LongText = None
    sets: tuple[StrengthSet, ...] = ()

    @model_validator(mode="after")
    def _unique_set_index(self) -> Self:
        indexes = [s.set_index for s in self.sets]
        if len(indexes) != len(set(indexes)):
            raise ValueError("set_index values must be unique within a session")
        return self


class PlannedWorkout(DomainModel):
    id: int | None = None
    source: PlanSource
    source_id: SourceId
    date: date
    sport: Sport
    workout_type: WorkoutType
    target_duration_s: int | None = Field(default=None, gt=0, le=86_400)
    target_distance_m: float | None = Field(default=None, ge=0, le=1_000_000)
    notes: LongText = None
    detail: LongText = None  # short session text (e.g. "12 km easy")

    @model_validator(mode="after")
    def _type_matches_sport(self) -> Self:
        if self.workout_type.is_strength != (self.sport is Sport.STRENGTH):
            raise ValueError("workout_type does not match sport")
        return self


class Goal(DomainModel):
    """A dated goal, e.g. a race. Several can be active; the coach plans toward the nearest."""

    id: int | None = None
    kind: GoalKind
    description: Annotated[str, Field(min_length=1, max_length=200)]
    target_date: date | None = None
    target_value: float | None = Field(default=None, gt=0, le=1_000)  # e.g. 21.1 (km)
    unit: Annotated[str, Field(max_length=20)] | None = None
    priority: int = Field(default=2, ge=1, le=3)  # 1 = A-race / main goal
    active: bool = True


# --- recovery & feedback --------------------------------------------------------------


class DailyWellness(DomainModel):
    id: int | None = None
    source: Source
    date: date
    sleep_duration_s: int | None = Field(default=None, ge=0, le=86_400)
    sleep_score: Percent | None = None
    hrv_rmssd_ms: float | None = Field(default=None, ge=1, le=300)
    resting_hr: int | None = Field(default=None, ge=25, le=150)
    body_battery_high: Percent | None = None
    body_battery_low: Percent | None = None
    stress_avg: Percent | None = None
    steps: int | None = Field(default=None, ge=0, le=200_000)

    @model_validator(mode="after")
    def _battery_order(self) -> Self:
        high, low = self.body_battery_high, self.body_battery_low
        if high is not None and low is not None and low > high:
            raise ValueError("body_battery_low cannot exceed body_battery_high")
        return self


class Feedback(DomainModel):
    id: int | None = None
    date: date
    source: FeedbackSource = FeedbackSource.MANUAL
    source_id: SourceId | None = None
    activity_id: int | None = Field(default=None, gt=0)
    rpe: int | None = Field(default=None, ge=1, le=10)
    soreness: Score0to10 | None = None
    fatigue: Score0to10 | None = None
    mood: Score1to5 | None = None
    sleep_quality: Score1to5 | None = None
    pain_flag: bool = False
    pain_location: ShortText = None
    illness_flag: bool = False
    notes: LongText = None

    @model_validator(mode="after")
    def _pain_location_needs_flag(self) -> Self:
        if self.pain_location and not self.pain_flag:
            raise ValueError("pain_location given without pain_flag")
        return self
