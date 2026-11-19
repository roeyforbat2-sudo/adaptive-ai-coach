"""The athlete's training program (translated plan document), loaded from JSON.

The program file is personal data and lives in the git-ignored `data/` folder
(`COACH_PROGRAM_FILE`, default `data/program.json`). Weeks run Sunday to Saturday.
The calendar remains the source of truth for *when* a session happens; the program adds
*what* the session is (distance, structure, target paces).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)

SessionKind = Literal["strength", "quality", "easy", "test", "long", "rest", "race", "none"]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Race(_M):
    name: str
    date: date
    distance_km: float
    goal_time: str | None = None
    goal_pace: str | None = None


class StructureDay(_M):
    day: str
    session: str
    kind: str


class PaceZone(_M):
    type: str
    pace: str
    rpe: str
    purpose: str


class Block(_M):
    number: int
    name: str
    weeks: tuple[int, int]
    goal: str


class ProgramWeek(_M):
    week: int
    start: date
    end: date
    phase: str
    monday: str
    monday_km: float | None
    wednesday: str
    wednesday_km: float | None
    long: str
    long_km: float | None
    total_km: float | None


class RaceDay(_M):
    date: date
    plan: str


class StrategyRow(_M):
    segment: str
    pace: str
    guidance: str


class GymExercise(_M):
    name: str
    sets: str
    focus: str = ""


class StrengthWorkout(_M):
    key: str
    name: str
    weekday: int  # Monday = 0 ... Sunday = 6
    exercises: list[GymExercise] = []


class StrengthVolume(_M):
    weeks: tuple[int, int]
    upper: str
    lower: str


class Program(_M):
    title: str
    race: Race
    start_date: date
    notes: list[str] = []
    weekly_structure: list[StructureDay] = []
    paces: list[PaceZone] = []
    blocks: list[Block] = []
    weeks: list[ProgramWeek]
    race_week: list[RaceDay] = []
    race_strategy: list[StrategyRow] = []
    race_note: str | None = None
    success_signs: list[str] = []
    strength_workouts: list[StrengthWorkout] = []
    strength_volume: list[StrengthVolume] = []
    strength_notes: list[str] = []

    def week_for(self, day: date) -> ProgramWeek | None:
        return next((w for w in self.weeks if w.start <= day <= w.end), None)

    def block_for(self, week: ProgramWeek) -> Block | None:
        return next((b for b in self.blocks if b.weeks[0] <= week.week <= b.weeks[1]), None)

    def pace_range(self, zone_prefix: str) -> tuple[float, float] | None:
        """(fast, slow) seconds per km for a pace zone such as 'Easy', or None."""
        zone = next((z for z in self.paces if z.type.lower().startswith(zone_prefix.lower())), None)
        if zone is None:
            return None
        parts = zone.pace.replace("/km", "").strip().split("-")
        try:
            secs = [int(p.split(":")[0]) * 60 + int(p.split(":")[1]) for p in parts if ":" in p]
        except ValueError:
            return None
        return (min(secs), max(secs)) if len(secs) == 2 else None

    def strength_workout(self, weekday: int) -> StrengthWorkout | None:
        return next((w for w in self.strength_workouts if w.weekday == weekday), None)

    def strength_volume_for(self, week: ProgramWeek) -> StrengthVolume | None:
        return next(
            (v for v in self.strength_volume if v.weeks[0] <= week.week <= v.weeks[1]), None
        )

    @property
    def peak_long_run(self) -> tuple[float, date] | None:
        """Longest planned long run before race week, and the end of its week."""
        candidates = [
            w for w in self.weeks if w.long_km and w.end < self.race.date - timedelta(days=6)
        ]
        if not candidates:
            return None
        peak = max(candidates, key=lambda w: (w.long_km or 0, -w.week))
        return float(peak.long_km or 0), peak.end


@dataclass(frozen=True)
class DaySession:
    date: date
    kind: SessionKind
    title: str
    detail: str
    km: float | None
    flexible: bool = False  # Friday/Saturday: long run on one day, rest on the other
    from_calendar: bool = False  # True when the athlete's calendar defined this day


_STRENGTH_DAYS = {
    6: "Strength: Upper body (strength)",
    1: "Strength: Lower body",
    3: "Strength: Upper hypertrophy + leg strengthening",
}


def _run_kind(text: str, default: SessionKind) -> SessionKind:
    low = text.lower()
    if "test" in low:
        return "test"
    if any(t in low for t in ("warm-up", "sprint", " at ", "x4", "x1")) and default == "quality":
        return "quality"
    return "easy" if "easy" in low and "warm-up" not in low else default


def session_for(program: Program, day: date) -> DaySession:
    race_day = next((r for r in program.race_week if r.date == day), None)
    if race_day is not None:
        kind: SessionKind = (
            "race"
            if day == program.race.date
            else "strength"
            if "strength" in race_day.plan.lower() or "activation" in race_day.plan.lower()
            else "easy"
            if "easy" in race_day.plan.lower() and " at " not in race_day.plan
            else "quality"
        )
        return DaySession(day, kind, "Race week", race_day.plan, None)
    week = program.week_for(day)
    if week is None:
        return DaySession(day, "none", "Outside the program", "", None)
    wd = day.weekday()  # Monday = 0 ... Sunday = 6
    if wd in _STRENGTH_DAYS:
        return DaySession(day, "strength", _STRENGTH_DAYS[wd], "Gym session from your plan.", None)
    if wd == 0:
        kind = _run_kind(week.monday, "quality")
        return DaySession(
            day, kind, "Quality run" if kind == "quality" else "Run", week.monday, week.monday_km
        )
    if wd == 2:
        kind = _run_kind(week.wednesday, "easy")
        title = "Test run" if kind == "test" else "Easy run"
        return DaySession(day, kind, title, week.wednesday, week.wednesday_km)
    return DaySession(day, "long", "Long run (or rest)", week.long, week.long_km, flexible=True)


def week_sessions(program: Program, week: ProgramWeek) -> list[DaySession]:
    days = (week.end - week.start).days + 1
    return [session_for(program, week.start + timedelta(days=i)) for i in range(days)]


def load_program(path: Path) -> Program | None:
    if not path.is_file():
        return None
    try:
        return Program.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        log.warning("could not load training program %s: %s", path, type(exc).__name__)
        return None
