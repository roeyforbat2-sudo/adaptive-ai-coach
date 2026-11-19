"""Free, rule-based coaching: workout feedback, weekly conclusions, and goal progress.

Every conclusion is a fixed, documented rule applied to measured numbers (see
docs/coaching-methodology.md -> "Rule-based insights"). No AI, no network, no cost.
Rules only comment on data that exists; missing values produce no sentence, never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from coach.domain import Goal
from coach.domain.labels import workout_label

Tone = Literal["good", "note", "warn"]


@dataclass(frozen=True)
class Insight:
    tone: Tone
    text: str


# --- per workout -------------------------------------------------------------------------

PACE_TOLERANCE_S = 10  # s/km treated as "the same pace"
HR_TOLERANCE = 3  # bpm treated as "the same heart rate"
EASY_TYPES = {"easy", "recovery", "long"}


def workout_insights(facts: dict[str, Any]) -> list[Insight]:
    out: list[Insight] = []
    family = facts["family"]
    duration = facts.get("duration_min")
    planned = facts.get("planned")
    cmp = facts.get("vs_last_28_days") or {}

    if planned:
        label = workout_label(planned["workout_type"])
        pct, target = planned.get("duration_vs_target_pct"), planned.get("target_duration_min")
        if pct is None or target is None:
            out.append(Insight("good", f"Done as planned: {label}."))
        elif family == "strength" and pct >= 50:
            # Calendar gym blocks include changing/travel; Garmin counts active time only.
            out.append(Insight("good", f"Done as planned: {label}, {duration:.0f} min active."))
        elif 90 <= pct <= 115:
            out.append(Insight("good", f"On plan: {label}, {duration:.0f} of {target:.0f} min."))
        elif pct < 90:
            out.append(
                Insight(
                    "note", f"Shorter than your calendar slot: {duration:.0f} of {target:.0f} min."
                )
            )
        else:
            out.append(Insight("note", f"Longer than planned: {duration:.0f} of {target:.0f} min."))
    else:
        out.append(Insight("note", "Extra session: not in your calendar plan."))

    if family == "run" and facts.get("session_hint") == "quality":
        # Interval / tempo / test session: average pace mixes warm-up, efforts and recoveries,
        # so it is only compared with other quality sessions (cmp is already filtered that way).
        out.append(Insight("good", "Quality session (intervals, tempo or test): a key workout."))
        pace, med_pace = facts.get("pace_s_per_km"), cmp.get("median_pace_s_per_km")
        if pace is not None and med_pace is not None and abs(pace - med_pace) > PACE_TOLERANCE_S:
            faster = pace < med_pace
            out.append(
                Insight(
                    "good" if faster else "note",
                    f"Average pace {abs(pace - med_pace):.0f} s/km "
                    f"{'faster' if faster else 'slower'} than your recent quality sessions "
                    "(the mix of efforts and recoveries can differ).",
                )
            )
    elif family == "run":
        pace, med_pace = facts.get("pace_s_per_km"), cmp.get("median_pace_s_per_km")
        hr, med_hr = facts.get("avg_hr"), cmp.get("median_avg_hr")
        if pace is not None and med_pace is not None and hr is not None and med_hr is not None:
            dp, dhr = pace - med_pace, hr - med_hr
            if abs(dp) <= PACE_TOLERANCE_S and abs(dhr) <= HR_TOLERANCE:
                out.append(
                    Insight("good", "Steady: pace and heart rate in line with your usual runs.")
                )
            elif dp < -PACE_TOLERANCE_S and dhr <= HR_TOLERANCE:
                out.append(
                    Insight(
                        "good",
                        f"{-dp:.0f} s/km faster than usual without a higher heart "
                        "rate: a sign of improving fitness.",
                    )
                )
            elif dp < -PACE_TOLERANCE_S:
                out.append(
                    Insight(
                        "note",
                        f"{-dp:.0f} s/km faster than usual, heart rate +{dhr:.0f} bpm: "
                        "a harder effort than your typical run.",
                    )
                )
            elif dhr >= HR_TOLERANCE:
                pace_part = (
                    "Similar pace to usual"
                    if abs(dp) <= PACE_TOLERANCE_S
                    else f"{dp:.0f} s/km slower than usual"
                )
                out.append(
                    Insight(
                        "warn",
                        f"{pace_part} but heart rate +{dhr:.0f} bpm. Fatigue, heat or "
                        "illness can do this; watch whether it repeats.",
                    )
                )
            else:
                out.append(
                    Insight(
                        "note",
                        f"{dp:.0f} s/km slower than usual at a similar or lower heart "
                        "rate: fine for an easy day.",
                    )
                )
            planned_type = planned["workout_type"] if planned else None
            anaerobic = facts.get("anaerobic_te") or 0
            if planned_type in EASY_TYPES and (anaerobic >= 2.0 or dhr >= 8):
                out.append(
                    Insight(
                        "warn",
                        "Planned as easy but ran harder than easy. Keeping easy days "
                        "easy helps you recover for the key sessions.",
                    )
                )
        longest, dist = cmp.get("longest_km"), facts.get("distance_km")
        if longest is not None and dist is not None and dist >= 8 and dist >= longest + 0.5:
            out.append(Insight("good", f"Your longest run in 4 weeks: {dist:.1f} km."))
    elif family == "strength":
        load, med_load = facts.get("training_load"), cmp.get("median_training_load")
        if duration is not None and cmp.get("median_duration_min"):
            med_dur = cmp["median_duration_min"]
            if duration < 0.75 * med_dur:
                out.append(
                    Insight(
                        "note",
                        f"Shorter gym session than usual ({duration:.0f} vs ~{med_dur:.0f} min).",
                    )
                )
        if load is not None and med_load and load > 1.5 * med_load:
            out.append(
                Insight(
                    "note",
                    "A heavier gym session than usual by training load; keep the next run easy.",
                )
            )

    rec = facts.get("recovery_that_morning") or {}
    if rec.get("hrv_status_7d") == "below normal range":
        out.append(Insight("warn", "Done on a morning with HRV below your normal range."))
    if rec.get("sleep_h") is not None and rec["sleep_h"] < 6:
        out.append(Insight("note", f"After a short night ({rec['sleep_h']:.1f} h of sleep)."))
    return out


# --- per week ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WeekReview:
    week_start: str
    complete: bool
    stats: dict[str, Any]
    insights: list[Insight] = field(default_factory=list)


def week_review(
    weeks: list[dict[str, Any]],
    adherence: dict[str, int] | None = None,
    avg_sleep_h: float | None = None,
) -> WeekReview | None:
    """Conclusions for the last week in `weeks`, compared with the (up to) 4 weeks before.

    `weeks` is coach.ai.context.weekly_summary output (oldest first).
    """
    if not weeks:
        return None
    week, prior = weeks[-1], [w for w in weeks[-5:-1] if w["complete"]]
    out: list[Insight] = []
    km = week["run_km"] or 0.0
    if prior:
        avg_km = sum(w["run_km"] or 0 for w in prior) / len(prior)
        if week["complete"] and avg_km >= 5:
            change = 100 * (km - avg_km) / avg_km
            if change > 30:
                out.append(
                    Insight(
                        "warn",
                        f"Running volume up {change:.0f}% on your recent average "
                        f"({km:.0f} vs {avg_km:.0f} km). Big jumps raise injury risk; "
                        "hold this level next week.",
                    )
                )
            elif change > 10:
                out.append(
                    Insight(
                        "good", f"Building: {km:.0f} km, {change:.0f}% above your recent average."
                    )
                )
            elif change >= -10:
                out.append(
                    Insight("good", f"Steady volume: {km:.0f} km (recent average {avg_km:.0f} km).")
                )
            else:
                out.append(
                    Insight("note", f"Lighter week: {km:.0f} km vs {avg_km:.0f} km average.")
                )
        prev_long = max((w["long_run_km"] or 0 for w in prior), default=0)
        if (
            week["long_run_km"]
            and week["long_run_km"] >= prev_long + 0.5
            and week["long_run_km"] >= 8
        ):
            out.append(
                Insight("good", f"New longest run of the last weeks: {week['long_run_km']:.1f} km.")
            )
    strength = week["strength_sessions"]
    if week["complete"]:
        if strength >= 3:
            out.append(Insight("good", f"{strength} gym sessions: strength well maintained."))
        elif strength == 2:
            out.append(Insight("note", "2 gym sessions this week (your plan has 3)."))
        else:
            out.append(
                Insight(
                    "warn", f"Only {strength} gym session(s): strength may slip if this repeats."
                )
            )
    if adherence and (adherence["completed"] + adherence["missed"]) > 0:
        done, total = adherence["completed"], adherence["completed"] + adherence["missed"]
        pct = 100 * done / total
        tone: Tone = "good" if pct >= 90 else "note" if pct >= 70 else "warn"
        out.append(Insight(tone, f"Plan adherence: {done} of {total} sessions ({pct:.0f}%)."))
    if avg_sleep_h is not None and avg_sleep_h < 6.5:
        out.append(
            Insight(
                "note",
                f"Average sleep {avg_sleep_h:.1f} h: more sleep is the cheapest performance boost.",
            )
        )
    stats = {
        "runs": week["runs"],
        "run_km": km,
        "long_run_km": week["long_run_km"],
        "strength_sessions": strength,
        "total_hours": week["total_hours"],
    }
    return WeekReview(week["week_start"], week["complete"], stats, out)


# --- goal progress ----------------------------------------------------------------------


@dataclass(frozen=True)
class GoalProgress:
    goal: Goal
    days_to_go: int | None
    phase: str
    current_long_km: float | None
    peak_long_km: float | None
    peak_by: date | None
    needed_per_week_km: float | None
    status: Literal["no_date", "on_track", "comfortable", "behind", "peak_reached", "taper", "done"]
    insights: list[Insight]

    @property
    def long_run_pct(self) -> float | None:
        if self.current_long_km is None or not self.peak_long_km:
            return None
        return min(100.0, 100 * self.current_long_km / self.peak_long_km)


def _race_profile(distance_km: float) -> tuple[float, int, float]:
    """(peak long run km, taper weeks, max sensible long-run increase per week in km)."""
    if distance_km >= 40:
        return 32.0, 3, 2.0
    if distance_km >= 20:
        return 18.0, 2, 1.5
    if distance_km >= 10:
        return min(12.0, distance_km * 1.2), 1, 1.0
    return distance_km, 1, 1.0


def goal_progress(
    goal: Goal,
    today: date,
    longest_recent_km: float | None,
    plan_peak: tuple[float, date] | None = None,
) -> GoalProgress:
    """Long-run progression toward a race; rules in docs/coaching-methodology.md.

    `plan_peak` (km, date) comes from the athlete's own program when it targets this race;
    it replaces the generic peak heuristic.
    """
    if goal.target_date is None or goal.target_value is None or goal.unit != "km":
        return GoalProgress(
            goal,
            None,
            "set the date",
            longest_recent_km,
            None,
            None,
            None,
            "no_date",
            [Insight("note", "Add the race date to track progress toward this goal.")],
        )
    days = (goal.target_date - today).days
    if days < 0:
        return GoalProgress(goal, days, "done", longest_recent_km, None, None, None, "done", [])
    peak, taper_weeks, max_inc = _race_profile(goal.target_value)
    peak_by = goal.target_date - timedelta(weeks=taper_weeks)
    if plan_peak is not None:
        peak, peak_by = plan_peak
    weeks_to_peak = (peak_by - today).days / 7
    phase = (
        "race week"
        if days <= 7
        else "taper"
        if weeks_to_peak <= 0
        else "base"
        if days > 16 * 7
        else "build"
    )
    out: list[Insight] = []
    current = longest_recent_km
    if phase in ("taper", "race week"):
        status: Any = "taper"
        out.append(
            Insight("good", "Taper time: keep runs short and easy, sleep well, trust the work.")
        )
        return GoalProgress(goal, days, phase, current, peak, peak_by, None, status, out)
    if current is None:
        return GoalProgress(
            goal,
            days,
            phase,
            None,
            peak,
            peak_by,
            None,
            "behind",
            [Insight("note", "No runs in the last 4 weeks to measure progress from.")],
        )
    if current >= peak:
        out.append(
            Insight(
                "good",
                f"Your long run ({current:.1f} km) already reaches the ~{peak:.0f} km "
                "peak: maintain it and focus on consistency.",
            )
        )
        return GoalProgress(goal, days, phase, current, peak, peak_by, 0.0, "peak_reached", out)
    needed = (peak - current) / max(weeks_to_peak, 1)
    status = (
        "comfortable" if needed <= 0.75 * max_inc else "on_track" if needed <= max_inc else "behind"
    )
    out.append(
        Insight(
            "good" if status != "behind" else "warn",
            f"Longest run in the last 4 weeks: {current:.1f} km. Reaching ~{peak:.0f} km by "
            f"{peak_by:%d %b} means adding about {needed:.1f} km per week to your long run"
            + (
                " (comfortable)."
                if status == "comfortable"
                else "."
                if status == "on_track"
                else f", more than the ~{max_inc:.1f} km/week that is usually safe. "
                "Consider a slightly "
                "lower peak or start extending the long run now."
            ),
        )
    )
    return GoalProgress(goal, days, phase, current, peak, peak_by, round(needed, 1), status, out)
