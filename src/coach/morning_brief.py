"""The 05:30 "Today's brief": what to do this morning and how to approach it.

Free and rule-based (see docs/coaching-methodology.md -> "Morning brief"). Built from today's
session in your calendar (or the program), the program's pace zones and gym workouts, last
night's recovery data, the safety rules and yesterday's check-in. Posted to Google Calendar
at 05:30 (one tagged event per day) and shown on the Today tab.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from coach.analytics.workout import pace_str, run_kind_hint
from coach.checkins import HARD_FATIGUE, HARD_SORENESS, for_day
from coach.engine.safety import SafetyAssessment
from coach.insights import Insight
from coach.program import DaySession, Program
from coach.weekly_review import pace_range_str, session_text

RUN_KINDS = {"quality", "easy", "test", "long", "race"}
LONG_FUEL_KM = 14  # from here: bring water / a gel


@dataclass(frozen=True)
class MorningBrief:
    day: date
    session: DaySession
    title: str  # event title, e.g. "☀️ Today: Long run · 12 km"
    headline: str
    plan: list[str] = field(default_factory=list)  # how to run / lift it
    readiness: list[str] = field(default_factory=list)  # last night's data
    adjustments: list[Insight] = field(default_factory=list)
    focus: Insight | None = None

    def as_text(self) -> str:
        lines = [self.headline, ""]
        if self.plan:
            lines += ["THE PLAN"] + [f"• {p}" for p in self.plan] + [""]
        if self.readiness:
            lines += ["HOW YOU'RE RECOVERING"] + [f"• {r}" for r in self.readiness] + [""]
        if self.adjustments:
            lines += ["TODAY'S ADJUSTMENTS"] + [f"! {a.text}" for a in self.adjustments] + [""]
        if self.focus:
            lines += ["FOCUS", f"→ {self.focus.text}", ""]
        lines.append(
            "After your workout: do the 10-second check-in in the app (Today tab). "
            "Rule-based brief by Adaptive AI Coach (free, no AI)."
        )
        return "\n".join(lines)


def _zone(program: Program, prefix: str) -> str | None:
    z = next((z for z in program.paces if z.type.lower().startswith(prefix.lower())), None)
    return f"{z.type.split(' (')[0]} {z.pace} (RPE {z.rpe})" if z else None


def _plan_lines(program: Program, s: DaySession) -> list[str]:
    out: list[str] = []
    if s.kind == "strength":
        workout = program.strength_workout(s.date.weekday())
        race_week = any(r.date == s.date for r in program.race_week)
        if race_week:
            out.append(s.detail)
        if workout and (not race_week or "strength a" in s.detail.lower()):
            out.append(workout.name)
            out += [
                f"{e.name}: {e.sets}" + (f" ({e.focus})" if e.focus else "")
                for e in workout.exercises
            ]
        week = program.week_for(s.date)
        vol = program.strength_volume_for(week) if week else None
        if vol:
            out.append(
                f"Volume now: upper body {vol.upper.lower()}; lower body {vol.lower.lower()}."
            )
        return out
    out.append(f"Session: {s.detail}" if s.detail else f"Session: {s.title}")
    if s.km:
        out.append(f"Distance: {s.km:g} km")
    if s.kind in ("easy", "long"):
        easy = _zone(program, "Easy")
        if easy:
            out.append(f"Pace: {easy}. Conversation pace; let heart rate, not the watch, decide.")
        if "hmp" in s.detail.lower():
            hmp = _zone(program, "HMP")
            if hmp:
                out.append(f"Faster segment: {hmp}.")
        out.append("Start the first 1-2 km slower than easy and build into it.")
        if s.kind == "long" and (s.km or 0) >= LONG_FUEL_KM:
            out.append("Long run: bring water and a gel; practise race-day fuelling.")
    elif s.kind in ("quality", "test"):
        out.append("Warm-up: 2 km easy + a few relaxed strides. Cool-down: 1-1.5 km easy.")
        low = s.detail.lower()
        target = re.search(r"\((\d:\d{2})\)|\bat (\d:\d{2})", s.detail)
        if any(k in low for k in ("hill", "uphill", "sprint")):
            out.append(
                "Effort, not pace: strong and relaxed uphill (RPE 8), then walk or jog down "
                "until fully recovered."
            )
        elif target:
            out.append(f"Target pace for the reps: {target.group(1) or target.group(2)} /km.")
        else:
            zone = _zone(program, "HMP" if "hmp" in low else "Intervals")
            if zone:
                out.append(f"Pace: {zone}.")
        if s.kind == "test":
            out.append("Test: start controlled, finish strong; the result sets your next paces.")
        else:
            out.append("Hit the target on every rep; even reps beat one fast one.")
    elif s.kind == "race":
        out.append(f"Goal: {program.race.goal_time} ({program.race.goal_pace}).")
        out += [f"{r.segment}: {r.pace}, {r.guidance}" for r in program.race_strategy]
    return out


def _readiness(recovery: pd.DataFrame | None, today: date) -> tuple[list[str], bool]:
    """Lines about last night, and whether today's numbers are in yet."""
    if recovery is None or recovery.empty:
        return ["No recovery data yet."], False
    row = recovery[recovery["date"] == pd.Timestamp(today)]
    synced = not row.empty and pd.notna(row.iloc[0].get("sleep_h"))
    if not synced:
        row = recovery[recovery["date"] == pd.Timestamp(today - timedelta(days=1))]
    if row.empty:
        return ["Last night's sleep and HRV are not synced yet."], False
    r = row.iloc[0]
    out = []
    when = "Last night" if synced else "Night before (today's not synced yet)"
    if pd.notna(r.get("sleep_h")):
        out.append(f"{when}: {r['sleep_h']:.1f} h of sleep.")
    if pd.notna(r.get("hrv")):
        out.append(f"HRV {r['hrv']:.0f} ms ({r.get('hrv_status') or 'no baseline yet'}).")
    if pd.notna(r.get("resting_hr")) and pd.notna(r.get("rhr_base")):
        out.append(f"Resting HR {r['resting_hr']:.0f} (your baseline {r['rhr_base']:.0f}).")
    return out, synced


def build_morning_brief(
    program: Program,
    session: DaySession,
    recovery: pd.DataFrame | None,
    safety: SafetyAssessment,
    feedback: pd.DataFrame | None,
    activities: pd.DataFrame | None,
) -> MorningBrief | None:
    """None on rest days and outside the program (no brief is posted then)."""
    s, today = session, session.date
    if s.kind in ("rest", "none"):
        return None
    what = session_text(s)
    title = f"☀️ Today: {s.title.replace('Gym: ', 'Gym – ')}" + (f" · {s.km:g} km" if s.km else "")
    readiness, _ = _readiness(recovery, today)
    adjustments: list[Insight] = []
    running = s.kind in RUN_KINDS
    if not safety.intensity_allowed and s.kind in ("quality", "test"):
        reasons = "; ".join(g.reason for g in safety.guardrails if g.severity != "info")
        adjustments.append(
            Insight(
                "warn",
                f"Swap the quality session for an easy run of the same time ({reasons}). "
                "Do the quality work once recovery is back in your range.",
            )
        )
    elif not safety.intensity_allowed and running:
        adjustments.append(Insight("warn", "Keep it fully easy and consider shortening it."))
    if safety.readiness == "red":
        adjustments.append(Insight("warn", "Recovery is in the red: today is a good day to rest."))
    yesterday = for_day(feedback, today - timedelta(days=1))
    if yesterday:
        if yesterday["pain"]:
            area = f" ({yesterday['pain_location']})" if yesterday["pain_location"] else ""
            adjustments.append(
                Insight(
                    "warn",
                    f"You reported pain yesterday{area}. If it's still there, skip the run or "
                    "the exercises that load it; if it persists, see a professional.",
                )
            )
        if (yesterday["soreness"] or 0) >= HARD_SORENESS:
            tip = (
                "cut one set from the leg exercises and keep 2+ reps in reserve"
                if s.kind == "strength"
                else "start very easy and shorten the run if the legs don't loosen up"
            )
            adjustments.append(
                Insight("note", f"Soreness was {yesterday['soreness']}/10 yesterday: {tip}.")
            )
        if (yesterday["fatigue"] or 0) >= HARD_FATIGUE:
            adjustments.append(
                Insight(
                    "note",
                    f"You felt tired yesterday ({yesterday['fatigue']}/10): keep effort "
                    "controlled and prioritise sleep tonight.",
                )
            )
    focus = None
    zone = program.pace_range("Easy")
    if s.kind in ("easy", "long") and zone and activities is not None and not activities.empty:
        recent = activities[
            (activities["family"] == "run")
            & (activities["date"] >= pd.Timestamp(today - timedelta(days=14)))
            & (activities["date"] < pd.Timestamp(today))
        ]
        if "name" in recent:
            recent = recent[~recent["name"].map(run_kind_hint).eq("quality")]
        recent = recent[recent["distance_m"] > 0]
        if len(recent) >= 2:
            pace = float((recent["duration_s"] / (recent["distance_m"] / 1000)).median())
            if pace < zone[0] - 10:
                focus = Insight(
                    "note",
                    f"Your recent easy runs averaged {pace_str(pace)}. Today, stay in "
                    f"{pace_range_str(zone)}, even if it feels too slow.",
                )
    if focus is None and s.kind in ("quality", "test"):
        focus = Insight(
            "note", "Arrive warm, run the reps at target pace, and stop if form breaks."
        )
    if focus is None and s.kind == "strength":
        focus = Insight("note", "Quality reps: controlled tempo, full range, log your weights.")
    headline = f"Today: {what}."
    if adjustments:
        headline += " Read the adjustments below before you start."
    return MorningBrief(
        day=today,
        session=s,
        title=title,
        headline=headline,
        plan=_plan_lines(program, s),
        readiness=readiness,
        adjustments=adjustments,
        focus=focus,
    )


def summary_dict(brief: MorningBrief) -> dict[str, Any]:
    return {"day": brief.day.isoformat(), "title": brief.title, "adjust": len(brief.adjustments)}
