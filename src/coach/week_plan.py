"""This program week: what was planned, what was done, and rule-based suggestions.

Suggestions are advice only: nothing here changes the calendar. Rules (documented in
docs/coaching-methodology.md -> "Weekly suggestions"):
- Safety rules win: low recovery -> keep the remaining runs easy / swap the quality session.
- Long-run progression follows the plan only if last week's long run was (nearly) done;
  otherwise cap this week's long run at last week's + 1.5 km.
- Volume: don't add missed kilometres; don't exceed the plan after an over-target week.
- Easy-run discipline: easy runs faster than the plan's easy range are flagged.
- Quality session done / missed, gym sessions done so far, what is left this week.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

import pandas as pd

from coach.analytics.workout import pace_str, run_kind_hint
from coach.domain.labels import workout_label
from coach.engine.safety import SafetyAssessment
from coach.insights import Insight, Tone
from coach.integrations.gcal.classify import classify
from coach.integrations.gcal.translate import distance_km, translate_title
from coach.program import DaySession, Program, ProgramWeek, SessionKind, session_for, week_sessions

DayStatus = Literal["done", "today", "upcoming", "missed", "rest"]
RUN_KINDS = {"quality", "easy", "test", "long", "race"}
LONG_RUN_STEP_KM = 1.5


@dataclass(frozen=True)
class DayPlan:
    session: DaySession
    status: DayStatus
    actual: str  # short description of what was recorded that day ("" if nothing)


@dataclass(frozen=True)
class WeekPlanView:
    week: ProgramWeek
    days: list[DayPlan]
    done_km: float
    target_km: float | None
    suggestions: list[Insight] = field(default_factory=list)
    from_calendar: bool = False


def _day_acts(acts: pd.DataFrame, day: date) -> pd.DataFrame:
    return acts[acts["date"] == pd.Timestamp(day)]


def _describe(day_acts: pd.DataFrame) -> str:
    parts = []
    for r in day_acts.itertuples():
        if r.family == "run":
            km = (r.distance_m or 0) / 1000
            parts.append(f"Run {km:.1f} km")
        elif r.family == "strength":
            parts.append(f"Gym {r.duration_s / 60:.0f} min")
        else:
            parts.append(f"Workout {r.duration_s / 60:.0f} min")
    return " + ".join(parts)


def _event_km(title: object, distance_m: object) -> float | None:
    """Distance from the title ("ריצה- 11 קל"), else the one stored on the event."""
    km = distance_km(title if isinstance(title, str) else None)
    if (
        km is None
        and isinstance(distance_m, int | float)
        and pd.notna(distance_m)
        and distance_m > 0
    ):
        km = round(float(distance_m) / 1000, 1)
    return km


def calendar_sessions(
    program: Program, week: ProgramWeek, planned: pd.DataFrame | None
) -> list[DaySession] | None:
    """The week's sessions as they are in the athlete's calendar (None if it has none).

    Each calendar training event becomes that day's session, described by its own title
    (translated). Moving, editing, adding or deleting an event in Google Calendar therefore
    changes the plan shown here after the next sync. Race week keeps the program's day plan.
    """
    if (
        planned is None
        or planned.empty
        or any(week.start <= r.date <= week.end for r in program.race_week)
    ):
        return None
    cal = planned[
        (planned["source"] == "calendar")
        & (planned["date"] >= pd.Timestamp(week.start))
        & (planned["date"] <= pd.Timestamp(week.end))
        & planned["workout_type"].notna()
    ]
    if cal.empty:
        return None
    cal = cal.assign(
        workout_type=[
            (c[1].value if (c := classify(n if isinstance(n, str) else None)) else t)
            for n, t in zip(cal["notes"], cal["workout_type"], strict=True)
        ]
    )
    runs = cal[~cal["workout_type"].str.startswith("strength")]
    easy_runs = runs[~runs["workout_type"].isin(["intervals", "tempo"])]
    km_by_id = {int(r.id): _event_km(r.notes, r.target_distance_m) for r in easy_runs.itertuples()}
    long_ids = {int(r.id) for r in easy_runs.itertuples() if r.workout_type == "long"}
    long_id = None
    if long_ids:
        long_id = min(long_ids)
    elif km_by_id:
        best_id, best_km = max(km_by_id.items(), key=lambda kv: kv[1] or 0)
        if best_km and best_km >= 8:
            long_id = best_id
    sessions: list[DaySession] = []
    for i in range((week.end - week.start).days + 1):
        day = week.start + timedelta(days=i)
        rows = cal[cal["date"] == pd.Timestamp(day)].sort_values("id")
        if rows.empty:
            sessions.append(DaySession(day, "rest", "Rest day", "", None, from_calendar=True))
            continue
        r = rows.iloc[0]
        notes = r["notes"] if isinstance(r["notes"], str) else None
        classified = classify(notes)  # re-read the title so rule updates apply without a re-sync
        wtype = classified[1].value if classified else str(r["workout_type"])
        stored = r.get("detail")
        detail = (
            stored
            if isinstance(stored, str) and stored
            else translate_title(notes) or workout_label(wtype)
        )
        kind: SessionKind
        if wtype.startswith("strength"):
            # App-written titles ("Gym – Upper") keep their name; the detail is the workout.
            own = (
                notes.replace(" – ", ": ", 1) if notes and notes.lower().startswith("gym") else None
            )
            kind, title, km = "strength", own or f"Gym: {detail}", None
        else:
            if wtype in ("intervals", "tempo"):
                kind = "test" if "test" in f"{detail} {notes or ''}".lower() else "quality"
                km = week.monday_km if kind == "quality" else week.wednesday_km
            else:
                km = km_by_id.get(int(r["id"]))
                kind = "long" if int(r["id"]) == long_id else "easy"
            title = {
                "quality": "Quality run",
                "test": "Test run",
                "long": "Long run",
                "easy": "Easy run",
            }[kind]
        if len(rows) > 1:
            title += f" + {len(rows) - 1} more"
        sessions.append(DaySession(day, kind, title, detail, km, from_calendar=True))
    return sessions


def scheduled_session(program: Program, planned: pd.DataFrame | None, day: date) -> DaySession:
    """The session for one day: from the calendar when it has that week, else the program."""
    week = program.week_for(day)
    if week is not None:
        sessions = calendar_sessions(program, week, planned)
        if sessions is not None:
            return next(s for s in sessions if s.date == day)
    return session_for(program, day)


def _statuses(sessions: list[DaySession], acts: pd.DataFrame, today: date) -> list[DayPlan]:
    out: list[DayPlan] = []
    long_done_on = None
    for s in sessions:
        if s.flexible:
            runs = _day_acts(acts, s.date)
            if not runs[runs["family"] == "run"].empty:
                long_done_on = s.date
    for s in sessions:
        day_acts = _day_acts(acts, s.date)
        actual = _describe(day_acts)
        family_needed = "strength" if s.kind == "strength" else "run"
        did = not day_acts[day_acts["family"] == family_needed].empty
        status: DayStatus
        if s.kind == "rest":
            status = "done" if not day_acts.empty else "rest"  # trained on a rest day: extra
        elif s.flexible and long_done_on is not None and long_done_on != s.date:
            status = "rest"
        elif did:
            status = "done"
        elif s.date == today:
            status = "today"
        elif s.date > today:
            status = "upcoming"
        elif s.flexible and s.date.weekday() == 4:  # Friday passed: Saturday may still be the day
            status = "rest" if today <= s.date + timedelta(days=1) else "missed"
        else:
            status = "missed"
        out.append(DayPlan(s, status, actual))
    return out


def _runs_between(acts: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    return acts[
        (acts["family"] == "run")
        & (acts["date"] >= pd.Timestamp(start))
        & (acts["date"] <= pd.Timestamp(end))
    ]


def build_week_plan(
    program: Program,
    activities: pd.DataFrame,
    safety: SafetyAssessment,
    today: date,
    planned: pd.DataFrame | None = None,
) -> WeekPlanView | None:
    week = program.week_for(today)
    if week is None:
        return None
    from_calendar = calendar_sessions(program, week, planned)
    sessions = from_calendar if from_calendar is not None else week_sessions(program, week)
    days = _statuses(sessions, activities, today)
    runs = _runs_between(activities, week.start, min(today, week.end))
    done_km = round(float(runs["distance_m"].fillna(0).sum()) / 1000, 1)
    tips: list[Insight] = []

    # 1. Safety first.
    remaining_quality = [
        d
        for d in days
        if d.session.kind in ("quality", "test") and d.status in ("today", "upcoming")
    ]
    if not safety.intensity_allowed:
        reasons = "; ".join(g.reason for g in safety.guardrails if g.severity != "info")
        msg = f"Recovery markers are low ({reasons}). Keep this week's remaining runs easy"
        msg += ", and swap the quality session for an easy run." if remaining_quality else "."
        tips.append(Insight("warn", msg))
    if safety.readiness == "red":
        tips.append(
            Insight(
                "warn",
                "Take an extra rest day before the long run, and shorten it if you "
                "still feel flat.",
            )
        )
    if any(g.code == "sleep_caution" for g in safety.guardrails):
        tips.append(
            Insight(
                "note",
                "Short sleep lately: if you can, do the long run on Saturday to get "
                "an extra night.",
            )
        )

    # 2. Quality session.
    quality_days = [d for d in days if d.session.kind in ("quality", "test")]
    for d in quality_days:
        if d.status == "done":
            day_runs = _day_acts(activities, d.session.date)
            hinted = (
                day_runs["name"].map(run_kind_hint).eq("quality").any()
                if "name" in day_runs
                else False
            )
            tips.append(
                Insight(
                    "good",
                    f"{d.session.title} done ({d.session.detail})."
                    + (
                        ""
                        if hinted
                        else " Tip: name quality sessions in Garmin (e.g. '6x400') "
                        "so the app compares them correctly."
                    ),
                )
            )
        elif d.status == "missed":
            tips.append(
                Insight(
                    "note",
                    f"{d.session.title} ({d.session.detail}) isn't recorded. Don't squeeze it "
                    "in later: keep the easy run easy and do the long run as planned.",
                )
            )

    # 3. Long-run progression (last week's actual vs plan).
    prev = program.week_for(week.start - timedelta(days=1))
    this_long = week.long_km
    if prev is not None and prev.long_km and this_long:
        prev_runs = _runs_between(activities, prev.start, prev.end)
        prev_long = float(prev_runs["distance_m"].max() / 1000) if not prev_runs.empty else 0.0
        if prev_long >= prev.long_km - 0.5:
            tips.append(
                Insight(
                    "good",
                    f"Last week's long run ({prev_long:.1f} km) hit the plan "
                    f"({prev.long_km:g} km): go ahead with {this_long:g} km this week.",
                )
            )
        elif prev_long > 0:
            capped = min(this_long, round(prev_long + LONG_RUN_STEP_KM, 1))
            if capped < this_long:
                tips.append(
                    Insight(
                        "warn",
                        f"Last week's long run was {prev_long:.1f} km vs {prev.long_km:g} km "
                        f"planned. Make this week's long run ~{capped:g} km instead of "
                        f"{this_long:g} km, and build from there.",
                    )
                )

        # 4. Volume vs plan last week.
        prev_km = float(prev_runs["distance_m"].fillna(0).sum()) / 1000
        if prev.total_km:
            ratio = prev_km / prev.total_km
            if ratio < 0.8:
                tips.append(
                    Insight(
                        "note",
                        f"Last week you ran {prev_km:.1f} of {prev.total_km:g} km planned. "
                        "Don't add the missing kilometres this week; just follow the plan.",
                    )
                )
            elif ratio > 1.15:
                tips.append(
                    Insight(
                        "warn",
                        f"Last week you ran {prev_km:.1f} km, more than the {prev.total_km:g} km "
                        "planned. Stick to the plan's volume this week.",
                    )
                )

    # 5. Easy-run discipline (last 14 days, non-quality runs).
    easy_range = program.pace_range("Easy")
    recent = _runs_between(activities, today - timedelta(days=14), today)
    if easy_range and not recent.empty:
        easy = (
            recent[~recent["name"].map(run_kind_hint).eq("quality")] if "name" in recent else recent
        )
        easy = easy[easy["distance_m"] > 0]
        if len(easy) >= 2:
            pace = float((easy["duration_s"] / (easy["distance_m"] / 1000)).median())
            fast, slow = easy_range
            if pace < fast - 10:
                tips.append(
                    Insight(
                        "warn",
                        f"Your easy runs average {pace_str(pace)}, faster than the plan's easy "
                        f"range ({pace_str(fast)} to {pace_str(slow)}). Slow them down so the "
                        "quality sessions and long run get your best energy.",
                    )
                )
            elif pace <= slow + 10:
                tips.append(
                    Insight(
                        "good",
                        f"Easy runs are in the plan's easy range (typical {pace_str(pace)}).",
                    )
                )

    # 6. Gym so far.
    gym_due = [d for d in days if d.session.kind == "strength" and d.session.date <= today]
    if gym_due:
        gym_done = sum(1 for d in gym_due if d.status == "done")
        tone: Tone = "good" if gym_done == len(gym_due) else "note"
        tips.append(
            Insight(tone, f"Gym: {gym_done} of {len(gym_due)} sessions done so far this week.")
        )

    # 7. What's left.
    left = [d for d in days if d.status in ("today", "upcoming") and d.session.kind in RUN_KINDS]
    if left:
        seen_long = False
        parts = []
        for d in left:
            if d.session.flexible:
                if seen_long:
                    continue
                seen_long = True
                parts.append(f"Fri/Sat {d.session.detail}")
            else:
                parts.append(f"{d.session.date:%a} {d.session.detail}")
        remaining = (week.total_km or 0) - done_km
        suffix = (
            f" (about {remaining:.0f} km to reach {week.total_km:g} km)"
            if week.total_km and remaining > 0
            else ""
        )
        tips.append(Insight("note", "Still to run this week: " + " · ".join(parts) + suffix + "."))

    target = week.total_km
    if from_calendar is not None:
        run_km = [s.km for s in from_calendar if s.kind in RUN_KINDS]
        if run_km and all(k is not None for k in run_km):
            target = round(sum(k for k in run_km if k is not None), 1)
    return WeekPlanView(week, days, done_km, target, tips, from_calendar is not None)
