"""'Coach's take' on one workout: compared with the session your calendar planned that day.

Free and rule-based (see docs/coaching-methodology.md -> "Workout analysis"). It builds on
`insights.workout_insights` (comparison with your own recent runs) and adds:
- plan vs actual: distance against the planned km, the planned session text;
- pace vs the program's pace zone for that kind of run (easy / long runs);
- effort: Garmin's training-effect scores, described with Garmin's published scale;
- what to do next: the next session in your calendar, adjusted by the safety rules.
Missing data produces no sentence; nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from coach.analytics.workout import pace_str
from coach.checkins import HARD_SORENESS
from coach.domain.labels import workout_label
from coach.engine.safety import SafetyAssessment
from coach.insights import Insight, Tone, workout_insights
from coach.program import DaySession, Program

EASY_KINDS = {"easy", "long"}
RUN_KINDS = {"easy", "long", "quality", "test", "race"}
PACE_MARGIN_S = 5  # s/km of slack around the plan's pace zone

# Garmin's published Training Effect scale (descriptive labels only).
_TE_SCALE = (
    (5.0, "overreaching"),
    (4.0, "highly improving"),
    (3.0, "improving"),
    (2.0, "maintaining"),
    (1.0, "minor"),
    (0.0, "no benefit"),
)


def te_label(value: float | None) -> str | None:
    if value is None:
        return None
    return next(label for floor, label in _TE_SCALE if value >= floor)


@dataclass(frozen=True)
class WorkoutAnalysis:
    activity_id: int
    day: date
    family: str
    title: str  # "Long run" / "Gym – Legs" / "Run"
    tag: str  # "On target", "Harder than planned", ...
    tone: Tone
    headline: str
    planned: str | None  # "12 km easy" (the session your calendar had that day)
    stats: list[tuple[str, str, str]] = field(default_factory=list)  # (label, value, sub)
    points: list[Insight] = field(default_factory=list)
    next_step: Insight | None = None


def _gym_title(title: str) -> str:
    return title.replace("Gym: ", "Gym – ")


def _minutes(value: float | None) -> str:
    if value is None:
        return "–"
    h, m = divmod(round(value), 60)
    return f"{h} h {m:02d} min" if h else f"{m} min"


def _run_points(
    facts: dict[str, Any], session: DaySession | None, program: Program | None
) -> tuple[list[Insight], str | None]:
    """Plan-vs-actual points for a run, and a tag when the run clearly missed the plan."""
    out: list[Insight] = []
    tag = None
    dist, pace = facts.get("distance_km"), facts.get("pace_s_per_km")
    if session is not None and session.km and dist:
        pct = 100 * dist / session.km
        if pct >= 95 and pct <= 110:
            out.append(Insight("good", f"Distance on plan: {dist:.1f} of {session.km:g} km."))
        elif pct > 110:
            out.append(Insight("note", f"Longer than planned: {dist:.1f} of {session.km:g} km."))
        elif pct >= 80:
            out.append(
                Insight("note", f"A bit short of the plan: {dist:.1f} of {session.km:g} km.")
            )
        else:
            quality = session.kind in ("quality", "test")  # planned km includes jog recoveries
            out.append(
                Insight(
                    "note" if quality else "warn",
                    f"Well short of the plan: {dist:.1f} of {session.km:g} km.",
                )
            )
            tag = "Shorter than planned"
    kind = session.kind if session else None
    mixed = session is not None and any(
        k in session.detail.lower() for k in ("hmp", " at ", "+ ", "strides", "progress")
    )
    zone = program.pace_range("Easy") if program else None
    if kind in EASY_KINDS and pace is not None and zone and not mixed:
        fast, slow = zone
        rng = f"{(pace_str(fast) or '').removesuffix('/km')}–{pace_str(slow)}"
        if pace < fast - PACE_MARGIN_S:
            out.append(
                Insight(
                    "warn",
                    f"{pace_str(pace)} is {fast - pace:.0f} s/km faster than your easy range "
                    f"({rng}). Easy days build the engine at low cost; running them fast adds "
                    "fatigue without extra benefit.",
                )
            )
            tag = tag or "Faster than planned"
        elif pace > slow + 15:
            out.append(
                Insight(
                    "note",
                    f"{pace_str(pace)}: slower than your easy range ({rng}). Fine on a tired, "
                    "hot or hilly day.",
                )
            )
        else:
            out.append(Insight("good", f"Pace {pace_str(pace)}: right in your easy range ({rng})."))
    elif mixed and kind in EASY_KINDS:
        out.append(
            Insight(
                "note",
                "Mixed session (easy + faster segments): the average pace can't judge the "
                "faster part. Check the segment splits on your watch.",
            )
        )
    aerobic = facts.get("aerobic_te")
    if aerobic is not None:
        label = te_label(aerobic)
        if kind in EASY_KINDS and kind != "long" and aerobic >= 3.5:
            out.append(
                Insight(
                    "note",
                    f"Aerobic training effect {aerobic:.1f} ({label}): high for an easy run.",
                )
            )
        elif aerobic >= 3.0:
            out.append(
                Insight("good", f"Aerobic training effect {aerobic:.1f}: {label} for your fitness.")
            )
    anaerobic = facts.get("anaerobic_te")
    if kind in ("quality", "test") and anaerobic is not None and anaerobic >= 2.0:
        out.append(
            Insight(
                "good", f"Anaerobic training effect {anaerobic:.1f}: the fast reps did their job."
            )
        )
    return out, tag


def _next_step(
    analysis_kind: str | None,
    tone: Tone,
    next_session: DaySession | None,
    safety: SafetyAssessment | None,
    program: Program | None,
) -> Insight | None:
    if next_session is None:
        return None
    nxt = next_session
    when = f"{nxt.date:%A}"
    title = _gym_title(nxt.title)
    name = title if not nxt.detail or nxt.detail in title else f"{title} ({nxt.detail})"
    if safety is not None and not safety.intensity_allowed and nxt.kind in RUN_KINDS:
        return Insight(
            "warn",
            f"{when}: {name}. Recovery markers are low, so keep it easy and shorter, or rest.",
        )
    if nxt.kind == "rest":
        return Insight("note", f"{when} is a rest day: sleep, eat well and let it sink in.")
    if nxt.kind == "strength":
        if analysis_kind in ("long", "quality", "test") and "leg" in nxt.title.lower():
            tip = next(
                (n for n in (program.strength_notes if program else []) if "leg fatigue" in n),
                "keep 2 reps in reserve on squats and deadlifts.",
            )
            return Insight("note", f"{when}: {title} after a hard run. {tip}")
        return Insight("note", f"{when}: {name}.")
    if tone == "warn" or analysis_kind in ("long", "quality", "test"):
        if nxt.kind in ("quality", "test", "long"):
            return Insight("note", f"{when}: {name}. Arrive rested: easy day and sleep before it.")
        return Insight("note", f"{when}: {name}. Keep it truly easy to absorb today's work.")
    return Insight("note", f"{when}: {name}.")


def checkin_points(checkin: dict[str, Any] | None, kind: str | None, family: str) -> list[Insight]:
    """What your check-in adds: how hard it felt (RPE) against what was planned, and how
    your body is doing (soreness, pain, illness)."""
    if not checkin:
        return []
    out: list[Insight] = []
    rpe = checkin.get("rpe")
    if rpe is not None and family == "run":
        if kind in EASY_KINDS and rpe >= 7:
            out.append(
                Insight(
                    "warn",
                    f"It felt hard (effort {rpe}/10) for an easy run. Slow down next time; if "
                    "easy keeps feeling hard, you may need more recovery.",
                )
            )
        elif kind in EASY_KINDS and rpe <= 4:
            out.append(Insight("good", f"Felt easy (effort {rpe}/10): exactly right for this run."))
        elif kind in ("quality", "test") and rpe >= 9:
            out.append(
                Insight("note", f"Very hard (effort {rpe}/10): keep the next two days easy.")
            )
        elif kind in ("quality", "test") and rpe <= 5:
            out.append(
                Insight(
                    "note",
                    f"The quality session felt easy (effort {rpe}/10): your paces may be "
                    "conservative. Check them at the next test.",
                )
            )
        else:
            out.append(Insight("good", f"Effort {rpe}/10: matches the session."))
    elif rpe is not None and family == "strength" and rpe >= 9:
        out.append(Insight("note", f"Gym felt very hard (effort {rpe}/10): watch tomorrow's legs."))
    soreness = checkin.get("soreness")
    if soreness is not None and soreness >= HARD_SORENESS:
        out.append(Insight("note", f"Soreness {soreness}/10: take it easy until it settles."))
    if checkin.get("pain"):
        area = f" ({checkin['pain_location']})" if checkin.get("pain_location") else ""
        out.append(
            Insight(
                "warn",
                f"You reported pain{area}: no hard sessions until it's gone; if it persists or "
                "gets worse, see a professional.",
            )
        )
    if checkin.get("ill"):
        out.append(Insight("warn", "You reported feeling ill: rest until you're well."))
    return out


def analyze_workout(
    facts: dict[str, Any],
    session: DaySession | None,
    *,
    program: Program | None = None,
    safety: SafetyAssessment | None = None,
    next_session: DaySession | None = None,
    checkin: dict[str, Any] | None = None,
) -> WorkoutAnalysis:
    """`session` is what the calendar/program planned that day (None if nothing);
    `checkin` is that day's check-in (coach.checkins.for_day), if you did one."""
    family = facts["family"]
    base = workout_insights(facts)
    planned_session = (
        session if session is not None and session.kind not in ("rest", "none") else None
    )
    if family == "run" and planned_session is not None and planned_session.kind != "strength":
        title = planned_session.title
    elif family == "strength":
        title = _gym_title(
            planned_session.title
            if planned_session and planned_session.kind == "strength"
            else "Gym"
        )
    else:
        title = workout_label(facts.get("sport") or family)
    if family == "run":
        extra, tag = _run_points(facts, planned_session, program)
        # The generic "Done as planned/On plan" line duplicates the distance line.
        base = [i for i in base if not i.text.startswith(("On plan:", "Done as planned"))] or base
        points = [*extra, *base]
    else:
        tag = None
        points = base
        if planned_session and planned_session.detail and planned_session.detail not in title:
            points.insert(0, Insight("good", f"Planned: {planned_session.detail}."))
    points += checkin_points(checkin, planned_session.kind if planned_session else None, family)
    if not facts.get("planned") and planned_session is None:
        tag = "Extra session"
    warns = [p for p in points if p.tone == "warn"]
    tone: Tone = "warn" if warns else "good" if not tag or tag == "Extra session" else "note"
    if tag is None:
        tag = "Check recovery" if warns else "On target"
    if tag == "Extra session" and not warns:
        tone = "note"
    if any("harder than easy" in p.text for p in points) and tag == "On target":
        tag = "Harder than planned"

    dist, pace, hr = facts.get("distance_km"), facts.get("pace"), facts.get("avg_hr")
    stats: list[tuple[str, str, str]] = []
    if family == "run" and dist:
        sub = f"of {planned_session.km:g} km" if planned_session and planned_session.km else ""
        stats.append(("Distance", f"{dist:.1f} km", sub))
    stats.append(("Time", _minutes(facts.get("duration_min")), ""))
    if family == "run" and pace:
        stats.append(("Pace", pace.replace("/km", " /km"), ""))
    if hr:
        cmp = facts.get("vs_last_28_days") or {}
        sub = f"usual {cmp['median_avg_hr']:.0f}" if cmp.get("median_avg_hr") else ""
        stats.append(("Avg HR", f"{hr:.0f}", sub))
    if facts.get("aerobic_te") is not None:
        stats.append(
            ("Aerobic TE", f"{facts['aerobic_te']:.1f}", te_label(facts["aerobic_te"]) or "")
        )

    lead = next((p for p in points if p.tone == "warn"), None) or next(
        (p for p in points if p.tone == "good"), points[0] if points else None
    )
    summary = (
        f"{dist:.1f} km at {pace}"
        if family == "run" and dist and pace
        else _minutes(facts.get("duration_min"))
    )
    headline = f"{title} · {summary}"
    if lead is not None:
        verdict = lead.text.split(". ")[0].rstrip(".")
        if pace and verdict.startswith(pace):  # don't repeat the pace already in the summary
            verdict = verdict.removeprefix(pace).removeprefix(" is ").strip()
        headline += f" — {verdict}."

    kind = planned_session.kind if planned_session else None
    return WorkoutAnalysis(
        activity_id=int(facts["activity_id"]),
        day=date.fromisoformat(facts["date"]),
        family=family,
        title=title,
        tag=tag,
        tone=tone,
        headline=headline,
        planned=planned_session.detail if planned_session else None,
        stats=stats,
        points=points,
        next_step=_next_step(kind, tone, next_session, safety, program),
    )
