"""The Saturday review: this week against the plan, and suggestions for next week.

Free and rule-based (see docs/coaching-methodology.md -> "Weekly review"). Pure function of
the stored data: activities, the calendar-driven week plan, recovery, and the program. The
same review is shown on the Today tab and written to Google Calendar every Saturday at 20:00.

Next-week rules (in order; each produces at most one suggestion):
1. Safety first: low recovery markers -> start the week easy, quality only once they recover.
2. Long run: next week's planned long run only if this week's was (nearly) done; otherwise
   cap it at this week's long run + 1.5 km (missed: repeat this week's distance).
3. Volume: if next week's plan is > 20% above what you actually ran, don't add extra km.
4. Missed quality session: do next week's as planned; never make up the missed one.
5. Easy-run pace faster than the plan's easy range -> slow down.
6. Gym: fewer sessions than planned -> prioritise them (they keep strength and muscle).
7. Sleep: 7-day average under 6.5 h -> prioritise sleep before the long run.
8. The week's special purpose (down week, test, taper) from the program.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from coach.analytics.workout import pace_str, run_kind_hint
from coach.checkins import HARD_FATIGUE, HARD_SORENESS, between
from coach.engine.safety import SafetyAssessment
from coach.insights import Insight, Tone
from coach.program import DaySession, Program, ProgramWeek, week_sessions
from coach.week_plan import (
    LONG_RUN_STEP_KM,
    RUN_KINDS,
    WeekPlanView,
    build_week_plan,
    calendar_sessions,
)

VOLUME_JUMP_PCT = 20
SLEEP_TARGET_H = 6.5

_PHASE_NOTES = {
    "down": "Next week is a down week: less volume on purpose. Enjoy it; it's when you absorb "
    "the last weeks of training.",
    "test": "Next week has a test run. Keep the day before easy and arrive fresh; the result "
    "sets your paces for the next block.",
    "taper": "Next week is a taper: less volume, keep a little speed. Resist adding extra.",
    "peak": "Next week is a peak week: the hardest of the plan. Sleep and eat to match.",
    "race": "Race week: short, easy, a few strides. Trust the training.",
}


@dataclass(frozen=True)
class WeeklyReview:
    week: ProgramWeek
    total_weeks: int
    headline: str
    tone: Tone
    stats: list[tuple[str, str, str]]
    went_well: list[Insight]
    watch: list[Insight]
    next_week: ProgramWeek | None
    next_sessions: list[DaySession] = field(default_factory=list)
    next_target_km: float | None = None
    suggestions: list[Insight] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"Weekly review · Week {self.week.week} of {self.total_weeks}"

    def as_text(self) -> str:
        """Plain text for the Google Calendar event description."""
        w = self.week
        lines = [f"{self.title} · {w.phase} · {w.start:%d %b}–{w.end:%d %b}", "", self.headline, ""]
        lines += [
            f"{label}: {value}" + (f" ({sub})" if sub else "") for label, value, sub in self.stats
        ]
        if self.went_well:
            lines += ["", "WHAT WENT WELL"] + [f"✓ {i.text}" for i in self.went_well]
        if self.watch:
            lines += ["", "TO WATCH"] + [f"! {i.text}" for i in self.watch]
        if self.next_week is not None:
            nw = self.next_week
            target = f" · {self.next_target_km:g} km" if self.next_target_km else ""
            lines += ["", f"NEXT WEEK · Week {nw.week} · {nw.phase}{target}"]
            lines += [
                f"{s.date:%a %d %b} · {session_text(s)}"
                for s in self.next_sessions
                if s.kind != "rest"
            ]
        if self.suggestions:
            lines += ["", "SUGGESTIONS FOR NEXT WEEK"] + [f"→ {i.text}" for i in self.suggestions]
        lines += ["", "Rule-based review by Adaptive AI Coach (free, no AI). Details in the app."]
        return "\n".join(lines)


def pace_range_str(zone: tuple[float, float]) -> str:
    return f"{(pace_str(zone[0]) or '').removesuffix('/km')}–{pace_str(zone[1])}"


def session_text(s: DaySession) -> str:
    """'Long run: 12 km easy', 'Gym – Upper: Workout A – Upper Strength' (no repetition)."""
    title = s.title.replace("Gym: ", "Gym – ")
    detail = s.detail.removeprefix("Quality: ").removeprefix("Quality ") if s.detail else ""
    if not detail or detail.lower() in title.lower():
        return title
    return f"{title}: {detail}"


def _week_runs(acts: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    return acts[
        (acts["family"] == "run")
        & (acts["date"] >= pd.Timestamp(start))
        & (acts["date"] <= pd.Timestamp(end))
    ]


def _easy_pace(runs: pd.DataFrame) -> float | None:
    easy = runs[~runs["name"].map(run_kind_hint).eq("quality")] if "name" in runs else runs
    easy = easy[easy["distance_m"] > 0]
    if easy.empty:
        return None
    return float((easy["duration_s"] / (easy["distance_m"] / 1000)).median())


def _recovery(recovery: pd.DataFrame, start: date, end: date) -> dict[str, Any]:
    if recovery is None or recovery.empty:
        return {}
    r = recovery[
        (recovery["date"] >= pd.Timestamp(start)) & (recovery["date"] <= pd.Timestamp(end))
    ]
    if r.empty:
        return {}
    out: dict[str, Any] = {}
    if r["sleep_h"].notna().any():
        out["sleep_h"] = float(r["sleep_h"].mean())
    if "hrv_status" in r:
        out["hrv_low_days"] = int((r["hrv_status"] == "below normal range").sum())
    if r["resting_hr"].notna().any() and "rhr_base" in r and r["rhr_base"].notna().any():
        out["rhr_delta"] = float((r["resting_hr"] - r["rhr_base"]).mean())
    return out


def _next_week_sessions(
    program: Program, week: ProgramWeek | None, planned: pd.DataFrame | None
) -> tuple[list[DaySession], float | None]:
    if week is None:
        return [], None
    sessions = calendar_sessions(program, week, planned) or week_sessions(program, week)
    # Program-only weeks have a flexible Fri/Sat pair: show the long run once, on Friday.
    sessions = [s for s in sessions if not (s.flexible and s.date.weekday() != 4)]
    run_km = [s.km for s in sessions if s.kind in RUN_KINDS]
    target = (
        round(sum(k for k in run_km if k), 1)
        if run_km and all(k is not None for k in run_km)
        else week.total_km
    )
    return sessions, target


def _feel(feedback: pd.DataFrame | None, start: date, end: date, days: list[Any]) -> dict[str, Any]:
    """Averages of the week's check-ins. Easy-run effort uses days planned as easy/long."""
    rows = between(feedback, start, end)
    easy_days = {d.session.date for d in days if d.session.kind in ("easy", "long")}

    def avg(values: list[int]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "n": len(rows),
        "easy_rpe": avg([r["rpe"] for r in rows if r["date"] in easy_days and r["rpe"]]),
        "soreness": avg([r["soreness"] for r in rows if r["soreness"] is not None]),
        "fatigue": avg([r["fatigue"] for r in rows if r["fatigue"] is not None]),
        "pain_days": sum(1 for r in rows if r["pain"]),
        "pain_areas": sorted({r["pain_location"] for r in rows if r["pain_location"]}),
    }


def build_weekly_review(
    program: Program,
    activities: pd.DataFrame,
    planned: pd.DataFrame | None,
    recovery: pd.DataFrame,
    safety: SafetyAssessment,
    day: date,
    as_of: date | None = None,
    feedback: pd.DataFrame | None = None,
) -> WeeklyReview | None:
    """Review of the program week containing `day` (normally its Saturday).

    `feedback` holds the daily check-ins (how runs felt, soreness, tiredness, pain).

    `as_of` (default: the week's last day) is "now": sessions after it are upcoming, not
    missed, so the review can also be shown mid-week as "your week so far".
    """
    week = program.week_for(day)
    if week is None:
        return None
    as_of = min(as_of or week.end, week.end)
    view: WeekPlanView | None = build_week_plan(program, activities, safety, as_of, planned)
    if view is None:
        return None
    total_weeks = max(w.week for w in program.weeks)
    days = view.days
    # A flexible Fri/Sat slot whose partner day held the long run is a rest day ("rest").
    due = [
        d
        for d in days
        if d.session.kind not in ("rest", "none") and d.status != "rest" and d.session.date <= as_of
    ]
    done = [d for d in due if d.status == "done"]
    missed = [d for d in due if d.status == "missed"]
    runs = _week_runs(activities, week.start, week.end)
    run_km = round(float(runs["distance_m"].fillna(0).sum()) / 1000, 1)
    long_day = next((d for d in days if d.session.kind == "long"), None)
    long_done_km = round(float(runs["distance_m"].max()) / 1000, 1) if not runs.empty else None
    quality = [d for d in days if d.session.kind in ("quality", "test")]
    gym_planned = [d for d in due if d.session.kind == "strength"]
    gym_done = [d for d in gym_planned if d.status == "done"]
    rec = _recovery(recovery, week.start, week.end)
    easy_pace = _easy_pace(runs)
    zone = program.pace_range("Easy")

    final = as_of >= week.end
    stats: list[tuple[str, str, str]] = [
        (
            "Sessions",
            f"{len(done)} of {len(due)}",
            f"{len(missed)} missed" if missed else "all done",
        ),
        ("Running", f"{run_km:g} km", f"plan {view.target_km:g} km" if view.target_km else ""),
    ]
    if long_day is not None:
        stats.append(
            (
                "Long run",
                f"{long_done_km:g} km" if long_done_km else "–",
                f"plan {long_day.session.km:g} km" if long_day.session.km else "",
            )
        )
    if gym_planned:
        stats.append(("Gym", f"{len(gym_done)} of {len(gym_planned)}", ""))
    if rec.get("sleep_h") is not None:
        stats.append(("Sleep", f"{rec['sleep_h']:.1f} h", "average"))

    good: list[Insight] = []
    watch: list[Insight] = []
    if due and len(done) == len(due):
        good.append(Insight("good", f"Every planned session done ({len(due)} of {len(due)})."))
    elif missed:
        names = ", ".join(f"{d.session.date:%a} {d.session.title.lower()}" for d in missed)
        watch.append(Insight("note", f"Missed: {names}."))
    if view.target_km and final:
        pct = 100 * run_km / view.target_km
        if 90 <= pct <= 115:
            good.append(
                Insight("good", f"Running volume on plan: {run_km:g} of {view.target_km:g} km.")
            )
        elif pct > 115:
            watch.append(
                Insight(
                    "warn",
                    f"Ran {run_km:g} km, {pct - 100:.0f}% above the plan's {view.target_km:g} km.",
                )
            )
        else:
            watch.append(Insight("note", f"Ran {run_km:g} of {view.target_km:g} planned km."))
    long_ok = False
    if long_day is not None and long_day.session.km:
        planned_long = long_day.session.km
        if long_done_km and long_done_km >= 0.9 * planned_long:
            long_ok = True
            good.append(
                Insight("good", f"Long run done: {long_done_km:g} km (plan {planned_long:g}).")
            )
        elif long_done_km:
            watch.append(
                Insight("note", f"Longest run {long_done_km:g} km, plan was {planned_long:g} km.")
            )
    for d in quality:
        if d.status == "done":
            good.append(Insight("good", f"{session_text(d.session).replace(':', ' done:', 1)}."))
    if easy_pace is not None and zone:
        fast, slow = zone
        if easy_pace < fast - 10:
            watch.append(
                Insight(
                    "warn",
                    f"Easy runs averaged {pace_str(easy_pace)}, faster than your easy range "
                    f"({pace_range_str(zone)}).",
                )
            )
        elif easy_pace <= slow + 10:
            good.append(Insight("good", f"Easy runs at an easy pace ({pace_str(easy_pace)})."))
    if gym_planned:
        if len(gym_done) == len(gym_planned):
            good.append(
                Insight("good", f"Gym: {len(gym_done)} of {len(gym_planned)} sessions done.")
            )
        else:
            watch.append(Insight("note", f"Gym: {len(gym_done)} of {len(gym_planned)} sessions."))
    if rec.get("hrv_low_days"):
        watch.append(
            Insight("warn", f"HRV below your normal range on {rec['hrv_low_days']} day(s).")
        )
    if rec.get("rhr_delta") is not None and rec["rhr_delta"] >= 3:
        watch.append(
            Insight(
                "note", f"Resting HR {rec['rhr_delta']:+.0f} bpm above your baseline on average."
            )
        )
    if rec.get("sleep_h") is not None and rec["sleep_h"] < SLEEP_TARGET_H:
        watch.append(Insight("note", f"Sleep averaged {rec['sleep_h']:.1f} h."))
    feel = _feel(feedback, week.start, as_of, days)
    if feel["n"]:
        stats.append(("Check-ins", f"{feel['n']} of {(as_of - week.start).days + 1}", "days"))
        if feel["easy_rpe"] is not None and feel["easy_rpe"] >= 6:
            watch.append(
                Insight("warn", f"Easy runs felt hard: average effort {feel['easy_rpe']:.1f}/10.")
            )
        elif feel["easy_rpe"] is not None:
            good.append(
                Insight("good", f"Easy runs felt easy: average effort {feel['easy_rpe']:.1f}/10.")
            )
        if feel["soreness"] is not None and feel["soreness"] >= HARD_SORENESS:
            watch.append(Insight("note", f"Soreness averaged {feel['soreness']:.1f}/10."))
        if feel["fatigue"] is not None and feel["fatigue"] >= HARD_FATIGUE:
            watch.append(Insight("warn", f"You felt tired: average {feel['fatigue']:.1f}/10."))
        if feel["pain_days"]:
            areas = ", ".join(feel["pain_areas"]) or "unspecified"
            watch.append(Insight("warn", f"Pain reported on {feel['pain_days']} day(s) ({areas})."))

    # --- headline ----------------------------------------------------------------------
    share = len(done) / len(due) if due else 0
    warns = [i for i in watch if i.tone == "warn"]
    tone: Tone
    if not final:
        tone = "warn" if warns else "good" if share >= 0.9 else "note"
        headline = f"Your week so far: {len(done)} of {len(due)} sessions, {run_km:g} km run."
    elif share >= 0.9 and not warns:
        tone = "good"
        headline = f"Strong week: {len(done)} of {len(due)} sessions and {run_km:g} km run."
    elif share >= 0.9:
        tone = "note"
        headline = f"Consistent week ({len(done)} of {len(due)} sessions), with one thing to fix."
    elif share >= 0.6:
        tone = "note"
        headline = f"Solid but incomplete: {len(done)} of {len(due)} sessions, {run_km:g} km."
    else:
        tone = "warn"
        headline = f"A lighter week than planned: {len(done)} of {len(due)} sessions."

    # --- next week -----------------------------------------------------------------------
    next_week = program.week_for(week.end + timedelta(days=1))
    next_sessions, next_target = _next_week_sessions(program, next_week, planned)
    tips: list[Insight] = []
    if not safety.intensity_allowed:
        reasons = "; ".join(g.reason for g in safety.guardrails if g.severity != "info")
        tips.append(
            Insight(
                "warn",
                f"Recovery markers are low ({reasons}). Start the week with easy running and "
                "do the quality session only once they are back in your normal range.",
            )
        )
    next_long = next((s for s in next_sessions if s.kind == "long"), None)
    if next_long is not None and next_long.km:
        if long_ok or long_day is None:
            tips.append(
                Insight(
                    "good", f"Go ahead with {next_long.date:%A}'s long run: {next_long.km:g} km."
                )
            )
        elif long_done_km:
            cap = min(next_long.km, round(long_done_km + LONG_RUN_STEP_KM, 1))
            tips.append(
                Insight(
                    "note",
                    f"Keep {next_long.date:%A}'s long run to about {cap:g} km "
                    f"(plan {next_long.km:g}): build from this week's {long_done_km:g} km "
                    f"by at most {LONG_RUN_STEP_KM:g} km.",
                )
            )
        else:
            tips.append(
                Insight(
                    "note",
                    "This week's long run was missed: repeat this week's distance before "
                    "stepping up.",
                )
            )
    if final and next_target and run_km > 0 and next_target > run_km * (1 + VOLUME_JUMP_PCT / 100):
        tips.append(
            Insight(
                "note",
                f"Next week's plan is {next_target:g} km, "
                f"{100 * (next_target / run_km - 1):.0f}% more than you ran this week: "
                "run the plan, but don't add extra kilometres.",
            )
        )
    if any(d.status == "missed" for d in quality):
        tips.append(
            Insight(
                "note", "Do next week's quality session as planned; don't make up the missed one."
            )
        )
    if easy_pace is not None and zone and easy_pace < zone[0] - 10:
        tips.append(
            Insight(
                "warn",
                f"Slow your easy runs to {pace_range_str(zone)}. Use heart rate "
                "or conversation pace as the guide, not the watch.",
            )
        )
    if gym_planned and len(gym_done) < len(gym_planned):
        tips.append(
            Insight(
                "note",
                "Fit in all 3 gym sessions: they keep your strength and muscle while "
                "mileage grows.",
            )
        )
    if rec.get("sleep_h") is not None and rec["sleep_h"] < SLEEP_TARGET_H:
        tips.append(
            Insight(
                "note", "Aim for 7+ hours of sleep, especially the two nights before the long run."
            )
        )
    if feel["pain_days"]:
        tips.append(
            Insight(
                "warn",
                "Pain this week: no quality sessions until it's gone, and see a professional if "
                "it persists. Easy running only if it doesn't hurt.",
            )
        )
    elif (feel["fatigue"] or 0) >= HARD_FATIGUE or (feel["soreness"] or 0) >= HARD_SORENESS:
        tips.append(
            Insight(
                "note",
                "You felt worn down this week: keep every run easy until Wednesday, and only "
                "then decide on the quality session.",
            )
        )
    if next_week is not None:
        phase = next_week.phase.lower()
        key = next((k for k in _PHASE_NOTES if k in phase), None)
        if key is None and any("test" in s.detail.lower() for s in next_sessions):
            key = "test"
        if key:
            tips.append(Insight("note", _PHASE_NOTES[key]))
    return WeeklyReview(
        week=week,
        total_weeks=total_weeks,
        headline=headline,
        tone=tone,
        stats=stats,
        went_well=good,
        watch=watch,
        next_week=next_week,
        next_sessions=next_sessions,
        next_target_km=next_target,
        suggestions=tips,
    )
