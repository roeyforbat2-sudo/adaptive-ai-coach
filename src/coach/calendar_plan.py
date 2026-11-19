"""Turn the training program into Google Calendar events, and diff them with the calendar.

Pure functions, no I/O. The athlete's choices (2026-11-18):
- one event per training day, 06:00-07:00 local time by default;
- the title is just the session ("Easy run", "Long run", "Gym – Upper", ...), in English;
- the description holds the full session from the program (structure, distance, paces,
  or the gym exercises with sets/reps);
- the long run goes on Friday (Saturday is rest);
- existing training events on the same day are UPDATED IN PLACE (no duplicates).

Nothing here talks to Google: `integrations/gcal/writer.py` applies a reviewed list of
changes, and only after the athlete clicks the approve button in the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from coach.domain.models import Sport
from coach.integrations.gcal.classify import classify
from coach.program import DaySession, Program, session_for

Category = Literal["run", "strength"]
Action = Literal["create", "update", "unchanged"]

TAG_KEY = "adaptiveCoach"  # extendedProperties.private marker on events the app wrote
DATE_KEY = "coachDate"
KM_KEY = "coachKm"
DETAIL_KEY = "coachDetail"
DETAIL_MAX = 300

GYM_TITLES = {6: "Gym – Upper", 1: "Gym – Legs", 3: "Gym – Full body"}
RUN_TITLES = {"quality": "Quality run", "test": "Test run", "easy": "Easy run", "long": "Long run"}
FOOTER = (
    "Planned by Adaptive AI Coach from your training program. You can move or edit this "
    "event; the app follows your calendar."
)


@dataclass(frozen=True)
class PlannedEvent:
    day: date
    category: Category
    title: str
    description: str
    detail: str  # short session text, also kept in the event's private properties
    km: float | None
    start: datetime
    end: datetime

    def private_props(self) -> dict[str, str]:
        props = {TAG_KEY: "1", DATE_KEY: self.day.isoformat(), DETAIL_KEY: self.detail[:DETAIL_MAX]}
        if self.km is not None:
            props[KM_KEY] = f"{self.km:g}"
        return props


@dataclass(frozen=True)
class ExistingEvent:
    """A timed event already in the calendar (fields as read from Google, untrusted)."""

    event_id: str
    etag: str | None
    day: date
    title: str
    description: str
    start: datetime
    end: datetime
    private: dict[str, str] = field(default_factory=dict)

    @property
    def category(self) -> Category | None:
        kind = classify(self.title)
        if kind is None:
            return None
        return "strength" if kind[0] is Sport.STRENGTH else "run"


@dataclass(frozen=True)
class Change:
    action: Action
    planned: PlannedEvent
    existing: ExistingEvent | None = None


# --- planning ------------------------------------------------------------------------


def _header(program: Program, day: date) -> str:
    week = program.week_for(day)
    if week is None:
        return program.title
    total = max((w.week for w in program.weeks), default=week.week)
    block = program.block_for(week)
    parts = [f"Week {week.week} of {total}"]
    if block:
        parts.append(f"Block {block.number}: {block.name}")
    if week.phase:
        parts.append(week.phase if week.phase.lower().endswith("week") else f"{week.phase} week")
    return " · ".join(parts)


def _run_description(program: Program, s: DaySession, kind: str) -> str:
    lines = [_header(program, s.date), "", f"Session: {s.detail}"]
    if s.km:
        lines.append(f"Distance: {s.km:g} km")
    week = program.week_for(s.date)
    if kind == "race":
        race = program.race
        if race.goal_time:
            lines.append(
                f"Goal: {race.goal_time}" + (f" ({race.goal_pace})" if race.goal_pace else "")
            )
        if program.race_strategy:
            lines += ["", "Race plan:"]
            lines += [f"• {r.segment}: {r.pace} — {r.guidance}" for r in program.race_strategy]
        if program.race_note:
            lines += ["", program.race_note]
        return "\n".join([*lines, "", FOOTER])
    if kind in ("easy", "long"):
        wanted = ["easy"] + (["hmp"] if "hmp" in s.detail.lower() else [])
        for prefix in wanted:
            zone = next((z for z in program.paces if z.type.lower().startswith(prefix)), None)
            if zone:
                lines.append(f"Pace: {zone.type} {zone.pace} (RPE {zone.rpe})")
    elif program.paces:
        lines += ["", "Pace guide:"]
        lines += [f"• {z.type}: {z.pace} (RPE {z.rpe})" for z in program.paces]
        lines += [f"• {n}" for n in program.notes if "quality" in n.lower()]
    if week is not None and week.total_km:
        lines += ["", f"Week total: {week.total_km:g} km running"]
    return "\n".join([*lines, "", FOOTER])


def _gym_description(program: Program, s: DaySession) -> str:
    lines = [_header(program, s.date), ""]
    race_week = any(r.date == s.date for r in program.race_week)
    if race_week:
        lines += [s.detail, ""]
    workout = program.strength_workout(s.date.weekday())
    if workout and (not race_week or "strength a" in s.detail.lower()):
        lines.append(workout.name)
        lines += [
            f"• {e.name}: {e.sets}" + (f" ({e.focus})" if e.focus else "")
            for e in workout.exercises
        ]
    week = program.week_for(s.date)
    volume = program.strength_volume_for(week) if week else None
    if volume:
        lines += ["", f"Volume now: upper body: {volume.upper}; lower body: {volume.lower}"]
    if program.strength_notes and not race_week:
        lines += ["", "Progression:"] + [f"• {n}" for n in program.strength_notes]
    return "\n".join([*lines, "", FOOTER]).replace("\n\n\n", "\n\n")


def plan_for_day(
    program: Program,
    day: date,
    tz: ZoneInfo,
    *,
    start_time: time = time(6, 0),
    minutes: int = 60,
    long_run_weekday: int = 4,  # Friday
) -> PlannedEvent | None:
    s = session_for(program, day)
    if s.kind in ("rest", "none"):
        return None
    if s.flexible and day.weekday() != long_run_weekday:
        return None  # the other flexible day is the rest day
    start = datetime.combine(day, start_time, tzinfo=tz)
    end = start + timedelta(minutes=minutes)
    if s.kind == "strength":
        title = GYM_TITLES.get(day.weekday(), "Gym")
        workout = program.strength_workout(day.weekday())
        detail = s.detail if s.title == "Race week" else (workout.name if workout else title)
        return PlannedEvent(
            day, "strength", title, _gym_description(program, s), detail, None, start, end
        )
    kind = "long" if s.flexible else s.kind
    km: float | None
    if kind == "race":
        title, km = f"Race – {program.race.name}", program.race.distance_km
    else:
        title, km = RUN_TITLES.get(kind, "Run"), s.km
    return PlannedEvent(
        day, "run", title, _run_description(program, s, kind), s.detail, km, start, end
    )


def planned_events(
    program: Program,
    first: date,
    last: date,
    tz: ZoneInfo,
    *,
    start_time: time = time(6, 0),
    minutes: int = 60,
) -> list[PlannedEvent]:
    """Events for every training day from `first` to `last` (inclusive), within the program."""
    last = min(last, program.race.date)
    out: list[PlannedEvent] = []
    day = first
    while day <= last:
        event = plan_for_day(program, day, tz, start_time=start_time, minutes=minutes)
        if event is not None:
            out.append(event)
        day += timedelta(days=1)
    return out


# --- diff ----------------------------------------------------------------------------


def _same(p: PlannedEvent, e: ExistingEvent) -> bool:
    return (
        e.title == p.title
        and e.description == p.description
        and e.start == p.start
        and e.end == p.end
        and all(e.private.get(k) == v for k, v in p.private_props().items())
    )


def diff(planned: list[PlannedEvent], existing: list[ExistingEvent]) -> list[Change]:
    """Match each planned event with an existing training event on the same day.

    Preference: the event the app wrote for that date, then any training event of the same
    kind (run or gym). A match is updated in place; no match means a new event. Events of
    the other kind, and non-training events, are never touched.
    """
    by_day: dict[date, list[ExistingEvent]] = {}
    for e in existing:
        by_day.setdefault(e.day, []).append(e)
    used: set[str] = set()
    changes: list[Change] = []
    for p in planned:
        candidates = [
            e for e in by_day.get(p.day, []) if e.event_id not in used and e.category == p.category
        ]
        candidates.sort(
            key=lambda e: (e.private.get(DATE_KEY) != p.day.isoformat(), e.start, e.event_id)
        )
        if not candidates:
            changes.append(Change("create", p))
            continue
        match = candidates[0]
        used.add(match.event_id)
        changes.append(Change("unchanged" if _same(p, match) else "update", p, match))
    return changes
