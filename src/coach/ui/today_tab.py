"""The Today tab (home): countdown, today / tomorrow, recovery, training program, goals.

Everything here is deterministic and free: rules + measured data + the athlete's own program.
No AI calls.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from coach.ai.coach import rules_only_daily
from coach.ai.context import summary_for_chat, weekly_summary
from coach.ai.llm import CoachLLM
from coach.analytics.workout import pace_str
from coach.checkins import for_day
from coach.config import get_settings
from coach.db.connection import DbTarget, as_target
from coach.domain.labels import workout_label
from coach.insights import GoalProgress, Insight, goal_progress, week_review, workout_insights
from coach.morning_brief import build_morning_brief
from coach.program import DaySession, Program, load_program
from coach.services import calendar_write
from coach.services import checkin as checkin_service
from coach.services import weekly_review as weekly_review_service
from coach.services.coach import CoachService, CoachSnapshot
from coach.services.dashboard import DashboardData
from coach.ui import components as ui
from coach.ui import format as fmt
from coach.week_plan import WeekPlanView, build_week_plan, scheduled_session
from coach.weekly_review import build_weekly_review, session_text
from coach.workout_analysis import analyze_workout

ICONS = {"run": "🏃", "strength": "🏋️", "other": "⚡"}
FAMILY_NAME = {"run": "Run", "strength": "Gym", "other": "Workout"}
KIND_LABEL = {
    "quality": "quality",
    "easy": "easy",
    "test": "test",
    "long": "long",
    "strength": "gym",
    "race": "race",
    "rest": "rest",
    "none": "—",
}


@st.cache_resource(show_spinner=False)
def _service(db_path: str, tz_name: str) -> CoachService:
    # The LLM object exists only for the service signature; AI calls are disabled by setting.
    return CoachService(as_target(db_path), ZoneInfo(tz_name), CoachLLM(get_settings().llm_model))


@st.cache_data(show_spinner=False)
def _program(path: str, mtime: float) -> Program | None:
    del mtime  # cache key: reload when the file changes
    return load_program(Path(path))


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _longest_recent_km(data: DashboardData, today: date, days: int = 28) -> float | None:
    acts = data.activities
    runs = acts[
        (acts["family"] == "run") & (acts["date"] > pd.Timestamp(today) - pd.Timedelta(days=days))
    ]
    longest = runs["distance_m"].max()
    return None if pd.isna(longest) else round(float(longest) / 1000, 1)


def _primary_goal(progress: list[GoalProgress]) -> GoalProgress | None:
    dated = [p for p in progress if p.days_to_go is not None and p.days_to_go >= 0]
    return min(dated, key=lambda p: (p.goal.priority, p.days_to_go or 0)) if dated else None


def _plan_progress_pct(program: Program | None, today: date) -> float | None:
    """How far through the training program we are (by days), for the hero ring."""
    if program is None:
        return None
    total = (program.race.date - program.start_date).days + 1
    done = (today - program.start_date).days + 1
    return max(0.0, min(100.0, 100 * done / total)) if total > 0 else None


def _pace_hint(program: Program | None, s: DaySession) -> str:
    if s.kind in ("easy", "long") and program and (rng := program.pace_range("Easy")):
        return (
            f"Easy pace {pace_str(rng[0])} to {pace_str(rng[1])}, RPE 2-3: "
            "you can talk in full sentences."
        )
    if s.kind == "quality":
        return "Warm up well, hit the paces in the session, keep the recoveries easy."
    if s.kind == "test":
        return "Warm up fully, then run the test evenly: start controlled, finish strong."
    if s.kind == "strength":
        return "Follow your gym program; keep 1-2 reps in reserve on the big lifts."
    if s.kind == "race":
        return "Race day: start calm (5:18-5:20 for km 1-3) and settle at goal pace."
    return ""


# --- cards -------------------------------------------------------------------------------


def _today_card(
    snap: CoachSnapshot, service: CoachService, program: Program | None, today: date
) -> str:
    acts = snap.data.activities
    todays = acts[acts["date"] == pd.Timestamp(today)].sort_values("start_utc")
    planned = scheduled_session(program, snap.data.planned, today) if program else None
    if not todays.empty:
        parts, notes = [], []
        for r in todays.itertuples():
            facts = service.facts(snap, int(r.id))
            if r.family == "run":
                km = (r.distance_m or 0) / 1000
                parts.append(
                    f"Run {km:.1f} km"
                    + (f" · {facts['pace']}" if facts and facts.get("pace") else "")
                )
            elif r.family == "strength":
                parts.append(f"Gym {r.duration_s / 60:.0f} min")
            else:
                parts.append(f"Workout {r.duration_s / 60:.0f} min")
            if facts:
                notes.extend(workout_insights(facts)[:2])
        desc = f"Planned: {planned.title}." if planned and planned.kind != "none" else ""
        return ui.card(f"Today · {today:%A} · done ✓", " + ".join(parts), desc, insights=notes[:3])

    rules = [
        Insight("warn" if g.severity != "info" else "note", g.reason)
        for g in snap.safety.guardrails
    ]
    if planned and planned.kind not in ("none",):
        title = (
            planned.detail if planned.kind in ("quality", "easy", "test", "long") else planned.title
        )
        hint = _pace_hint(program, planned)
        if not snap.safety.intensity_allowed and planned.kind in ("quality", "test"):
            rules.insert(
                0, Insight("warn", "Recovery is low: run it easy instead of the quality session.")
            )
        return ui.card(f"Today · {today:%A} · to do", title, hint, insights=rules)
    advice = rules_only_daily(snap.context, snap.safety, "free mode")
    return ui.card(
        f"Today · {today:%A}", advice.today.planned, advice.today.session, insights=rules
    )


def _tomorrow_card(snap: CoachSnapshot, program: Program | None, today: date) -> str:
    tomorrow = today + timedelta(days=1)
    label = f"Tomorrow · {tomorrow:%A %d %b}"
    if program is None or program.week_for(tomorrow) is None:
        plan = snap.view.plan_status
        rows = plan[(plan["date"] == pd.Timestamp(tomorrow)) & plan["workout_type"].notna()]
        if rows.empty:
            return ui.card(label, "Rest day", "Nothing planned in your calendar.")
        names = ", ".join(workout_label(t) for t in rows["workout_type"])
        return ui.card(label, names, "From your calendar.")
    s = scheduled_session(program, snap.data.planned, tomorrow)
    title = s.detail if s.kind in ("quality", "easy", "test", "long", "race") else s.title
    desc = s.title if s.kind in ("quality", "easy", "test", "long") else ""
    if s.flexible:
        desc = "Long run on Friday or Saturday; full rest on the other day."
    tips = [Insight("note", h)] if (h := _pace_hint(program, s)) else []
    if not snap.safety.intensity_allowed and s.kind in ("quality", "test"):
        tips.insert(0, Insight("warn", "If recovery is still low tomorrow, run easy instead."))
    stats = [(f"{s.km:g} km", "Distance", "planned")] if s.km else []
    return ui.card(label, title, desc, stats=stats, insights=tips)


def _recovery_card(snap: CoachSnapshot) -> str:
    r = snap.context.get("recovery_now") or {}
    low, high = (r.get("hrv_normal_range_ms") or [None, None])[:2]
    status = r.get("hrv_status") or "insufficient data"
    title = {
        "within normal range": "Recovered",
        "above normal range": "Fresh",
        "below normal range": "Under-recovered",
    }.get(status, "Building baseline")
    stats = [
        (
            fmt.number(r.get("hrv_7d_ms"), "ms", 0),
            "HRV 7-day",
            f"range {low:.0f}–{high:.0f}" if low and high else "range pending",
        ),
        (
            fmt.number(r.get("resting_hr_7d"), "", 0),
            "Resting HR",
            f"baseline {r['resting_hr_baseline']:.0f}" if r.get("resting_hr_baseline") else "",
        ),
        (fmt.number(r.get("sleep_7d_h"), "h"), "Sleep 7-day", "average"),
    ]
    return ui.card("Recovery", title, f"HRV is {status} for you.", stats=stats)


def _program_banner(program: Program, view: WeekPlanView) -> str:
    w = view.week
    block = program.block_for(w)
    kicker = f"Training program · week {w.week} of {len(program.weeks)}"
    if block:
        kicker += f" · block {block.number}: {block.name}"
    target = view.target_km
    headline = f"{w.phase} week · {target:g} km planned" if target else f"{w.phase} week"
    if view.from_calendar:
        kicker += " · synced from your Google Calendar"
    goal = f"{program.race.name} · {program.race.date:%a %d %b %Y}" + (
        f" · goal {program.race.goal_time} ({program.race.goal_pace})"
        if program.race.goal_time
        else ""
    )
    days = []
    for d in view.days:
        s = d.session
        title = s.title.replace("Strength: ", "Gym: ") if s.kind == "strength" else s.title
        detail = (
            d.actual
            if d.status == "done" and d.actual
            else (s.detail if s.kind != "strength" else "")
        )
        days.append(
            {
                "day": f"{s.date:%a}",
                "date": f"{s.date:%d}",
                "title": title,
                "detail": detail,
                "kind": KIND_LABEL.get(s.kind, s.kind),
                "status": d.status,
            }
        )
    km_pct = 100 * view.done_km / view.target_km if view.target_km else None
    km_label = (
        f"Running this week: {view.done_km:.1f} of {view.target_km:g} km"
        if view.target_km
        else f"Running this week: {view.done_km:.1f} km"
    )
    weeks_pct = 100 * w.week / len(program.weeks)
    return ui.program_banner(
        kicker,
        headline,
        goal,
        f"Week {w.week}/{len(program.weeks)}",
        weeks_pct,
        days,
        km_label,
        km_pct,
        view.suggestions,
    )


def _program_tables(program: Program, today: date) -> None:
    current = program.week_for(today)
    _html(
        ui.heading(
            program.title,
            f"{program.race.name} · {program.race.date:%A %d %B %Y} · "
            f"goal {program.race.goal_time or '—'} ({program.race.goal_pace or '—'}) · "
            f"plan started {program.start_date:%d %b %Y}",
        )
    )
    for note in program.notes:
        _html(f'<div class="ac-p">• {ui._e(note)}</div>')
    _html(ui.heading("Weekly structure"))
    _html(ui.table(["Day", "Session"], [(d.day, d.session) for d in program.weekly_structure]))
    _html(ui.heading("Pace & intensity key"))
    _html(
        ui.table(
            ["Type", "Pace", "RPE", "Purpose"],
            [(p.type, p.pace, p.rpe, p.purpose) for p in program.paces],
        )
    )
    for block in program.blocks:
        _html(
            ui.heading(
                f"Block {block.number}: {block.name} (weeks {block.weeks[0]}-{block.weeks[1]})",
                block.goal,
            )
        )
        weeks = [w for w in program.weeks if block.weeks[0] <= w.week <= block.weeks[1]]
        rows = [
            (
                w.week,
                f"{w.start:%d %b}–{w.end:%d %b}",
                w.phase,
                w.monday,
                w.wednesday,
                w.long,
                f"{w.total_km:g}" if w.total_km else "",
            )
            for w in weeks
        ]
        classes = [
            "current" if current and w.week == current.week else "past" if w.end < today else ""
            for w in weeks
        ]
        _html(
            ui.table(
                [
                    "Wk",
                    "Dates",
                    "Phase",
                    "Monday: quality",
                    "Wednesday: easy",
                    "Fri/Sat: long",
                    "km",
                ],
                rows,
                classes,
            )
        )
    if program.race_week:
        _html(ui.heading("Race week"))
        _html(
            ui.table(["Day", "Plan"], [(f"{r.date:%a %d %b}", r.plan) for r in program.race_week])
        )
    if program.race_strategy:
        _html(ui.heading("Race strategy", program.race_note or ""))
        _html(
            ui.table(
                ["Segment", "Pace", "Guidance"],
                [(r.segment, r.pace, r.guidance) for r in program.race_strategy],
            )
        )
    if program.success_signs:
        _html(ui.heading("Signs you're ready"))
        for sign in program.success_signs:
            _html(f'<div class="ac-p">✓ {ui._e(sign)}</div>')


def _goal_card(p: GoalProgress) -> str:
    g = p.goal
    dist = f" · {g.target_value:g} km" if g.target_value else ""
    if p.days_to_go is None:
        return ui.card("Goal · date to be set", f"{g.description}{dist}", insights=p.insights)
    stats = []
    if p.current_long_km is not None:
        stats.append((f"{p.current_long_km:.1f} km", "Longest run", "last 4 weeks"))
    if p.peak_long_km:
        stats.append(
            (
                f"~{p.peak_long_km:.0f} km",
                "Peak target",
                f"by {p.peak_by:%d %b}" if p.peak_by else "",
            )
        )
    if p.needed_per_week_km:
        stats.append((f"+{p.needed_per_week_km:.1f} km", "Per week", "long run"))
    return ui.card(
        f"Goal · {p.days_to_go} days · {p.phase}",
        f"{g.description}{dist}",
        f"Race day {g.target_date:%a %d %b %Y}." if g.target_date else "",
        stats=stats,
        insights=p.insights,
        progress_pct=p.long_run_pct,
    )


def _last_week_card(snap: CoachSnapshot, today: date) -> str:
    weeks = [w for w in weekly_summary(snap.data, today, weeks=6) if w["complete"]]
    if not weeks:
        return ui.card("Last week", "Not enough data yet")
    start = pd.Timestamp(weeks[-1]["week_start"])
    end = start + pd.Timedelta(days=6)
    plan = snap.view.plan_status
    wk_plan = plan[(plan["date"] >= start) & (plan["date"] <= end)]
    adherence = {
        "completed": int((wk_plan["status"] == "completed").sum()),
        "missed": int((wk_plan["status"] == "missed").sum()),
    }
    rec = snap.view.recovery
    sleep = rec[(rec["date"] >= start) & (rec["date"] <= end)]["sleep_h"].dropna()
    review = week_review(
        weeks, adherence, round(float(sleep.mean()), 1) if len(sleep) >= 4 else None
    )
    assert review is not None
    s = review.stats
    stats = [
        (f"{s['run_km']:.1f} km", "Running", f"{s['runs']} runs"),
        (f"{s['long_run_km']:.1f} km" if s["long_run_km"] else "—", "Long run", ""),
        (str(s["strength_sessions"]), "Gym", "sessions"),
    ]
    return ui.card(
        f"Last week · Mon {start:%d %b}", "Week review", stats=stats, insights=review.insights
    )


def _workout_html(
    snap: CoachSnapshot, service: CoachService, activity_id: int, tz: ZoneInfo
) -> str:
    facts = service.facts(snap, activity_id)
    if facts is None:
        return ""
    row = snap.data.activities[snap.data.activities["id"] == activity_id].iloc[0]
    family = facts["family"]
    planned = facts.get("planned")
    if facts.get("session_hint") == "quality":
        name = "Quality run"
    else:
        name = (
            workout_label(planned["workout_type"])
            if planned
            else FAMILY_NAME.get(family, "Workout")
        )
    when = f"{row['start_utc'].tz_convert(tz):%a %d %b · %H:%M}"
    chips: list[tuple[str, str]] = []
    if facts.get("distance_km"):
        chips.append(("Distance", f"{facts['distance_km']:.1f} km"))
    chips.append(("Time", fmt.duration((facts["duration_min"] or 0) * 60)))
    if facts.get("pace") and family == "run":
        chips.append(("Pace", facts["pace"].replace("/km", " /km")))
    if facts.get("avg_hr"):
        chips.append(("Avg HR", f"{facts['avg_hr']:.0f}"))
    if facts.get("training_load"):
        chips.append(("Load", f"{facts['training_load']:.0f}"))
    return ui.workout(
        family,
        ICONS.get(family, "⚡"),
        name,
        when,
        chips,
        "Planned" if planned else "Extra",
        not planned,
        workout_insights(facts),
    )


# --- page --------------------------------------------------------------------------------


ANALYSIS_DAYS = 3  # the "Coach's take" banner shows a workout from the last 3 days


def _hero_chips(snap: CoachSnapshot, program: Program | None, today: date) -> list[tuple[str, str]]:
    """Quick-glance facts under the countdown: week, km this week, what's next."""
    if program is None:
        return []
    chips: list[tuple[str, str]] = []
    week = program.week_for(today)
    if week is not None:
        total = max(w.week for w in program.weeks)
        chips.append(("📅", f"Week {week.week}/{total} · {week.phase}"))
        view = build_week_plan(program, snap.data.activities, snap.safety, today, snap.data.planned)
        if view is not None and view.target_km:
            chips.append(("🏃", f"{view.done_km:g} / {view.target_km:g} km this week"))
    nxt = scheduled_session(program, snap.data.planned, today + timedelta(days=1))
    if nxt.kind not in ("none",):
        what = "Rest day" if nxt.kind == "rest" else nxt.title.replace("Gym: ", "Gym – ")
        chips.append(("⏭️", f"Tomorrow: {what}"))
    return chips


def _analysis_html(
    snap: CoachSnapshot, service: CoachService, program: Program | None, today: date
) -> str | None:
    acts = snap.data.activities
    recent = acts[acts["date"] >= pd.Timestamp(today - timedelta(days=ANALYSIS_DAYS - 1))]
    if recent.empty:
        return None
    latest = recent.sort_values("start_utc").iloc[-1]
    facts = service.facts(snap, int(latest["id"]))
    if facts is None:
        return None
    day = latest["date"].date()
    planned = snap.data.planned
    session = scheduled_session(program, planned, day) if program else None
    nxt_day = today if day < today else today + timedelta(days=1)
    nxt = scheduled_session(program, planned, nxt_day) if program else None
    a = analyze_workout(
        facts,
        session,
        program=program,
        safety=snap.safety,
        next_session=nxt,
        checkin=for_day(snap.data.feedback, day),
    )
    when = "today" if day == today else "yesterday" if day == today - timedelta(1) else f"{day:%A}"
    return ui.analysis_banner(
        f"Coach's take · {when}'s workout · {day:%a %d %b}",
        a.tag,
        a.tone,
        a.headline,
        a.planned,
        a.stats,
        a.points,
        a.next_step,
    )


def _brief_html(snap: CoachSnapshot, program: Program, today: date) -> str | None:
    """Before today's workout: the morning brief (also posted to the calendar at 05:30)."""
    session = scheduled_session(program, snap.data.planned, today)
    b = build_morning_brief(
        program, session, snap.view.recovery, snap.safety, snap.data.feedback, snap.data.activities
    )
    if b is None:
        return None
    return ui.brief_card(
        f"☀️ Today's brief · {today:%A %d %b}",
        b.headline.removeprefix("Today: "),
        b.plan,
        b.readiness,
        b.adjustments,
        b.focus,
    )


def _checkin_section(snap: CoachSnapshot, today: date, db_path: DbTarget, trained: bool) -> None:
    """The 10-second daily check-in (reminder at 17:30). Saving again updates today's."""
    current = for_day(snap.data.feedback, today)
    if current is not None:
        parts = []
        if current["rpe"]:
            parts.append(f"effort {current['rpe']}/10")
        parts.append(f"soreness {current['soreness']}/10")
        parts.append(f"tiredness {current['fatigue']}/10")
        if current["pain"]:
            parts.append(f"pain: {current['pain_location'] or 'yes'}")
        if current["ill"]:
            parts.append("feeling ill")
        _html(ui.checkin_done(" · ".join(parts) + "."))
    label = "✏️ Update today's check-in" if current else "✅ Daily check-in · 10 seconds"
    expanded = current is None and datetime.now(get_settings().tz).hour >= 12
    with st.expander(label, expanded=expanded), st.form("checkin", border=False):
        rpe = None
        if trained:
            rpe = st.select_slider(
                "How hard did today's workout feel? (1 = very easy, 10 = all-out)",
                options=list(range(1, 11)),
                value=(current or {}).get("rpe") or 5,
            )
        c1, c2 = st.columns(2)
        soreness = c1.slider(
            "Muscle soreness (0 = none)", 0, 10, (current or {}).get("soreness") or 0
        )
        fatigue = c2.slider(
            "How tired are you? (0 = fresh)", 0, 10, (current or {}).get("fatigue") or 0
        )
        options = ["No pain", *checkin_service.PAIN_AREAS]
        prev_pain = (current or {}).get("pain_location")
        pain = c1.selectbox(
            "Any pain?", options, index=options.index(prev_pain) if prev_pain in options else 0
        )
        ill = c2.toggle("Feeling ill", value=bool((current or {}).get("ill")))
        if st.form_submit_button("Save check-in", type="primary"):
            today_acts = snap.data.activities[snap.data.activities["date"] == pd.Timestamp(today)]
            activity_id = (
                int(today_acts.sort_values("start_utc").iloc[-1]["id"])
                if not today_acts.empty
                else None
            )
            checkin_service.save(
                db_path,
                checkin_service.CheckIn(
                    day=today,
                    rpe=int(rpe) if rpe is not None else None,
                    soreness=int(soreness),
                    fatigue=int(fatigue),
                    pain=None if pain == "No pain" else str(pain),
                    ill=bool(ill),
                    activity_id=activity_id,
                ),
            )
            st.cache_data.clear()  # the dashboard data is cached; show the new check-in now
            st.rerun()


def _review_html(
    snap: CoachSnapshot, program: Program, today: date, db_path: DbTarget
) -> str | None:
    """Saturday: this week's review. Other days: the last finished week's review."""
    saturday = today if today.weekday() == 5 else today - timedelta(days=(today.weekday() - 5) % 7)
    review = build_weekly_review(
        program,
        snap.data.activities,
        snap.data.planned,
        snap.view.recovery,
        snap.safety,
        saturday,
        as_of=min(today, saturday),
        feedback=snap.data.feedback,
    )
    if review is None:
        return None
    w = review.week
    target = f" · {review.next_target_km:g} km" if review.next_target_km else ""
    next_title = (
        f"Next week · Week {review.next_week.week} · {review.next_week.phase}{target}"
        if review.next_week
        else "Next week"
    )
    lines = [
        (f"{s.date:%a %d %b}", session_text(s))
        for s in review.next_sessions
        if s.kind not in ("rest", "none")
    ]
    if weekly_review_service.last_posted(db_path) == saturday:
        footer = f"✓ Posted to your Google Calendar · {saturday:%a %d %b} 20:00"
    elif not calendar_write.write_connected():
        footer = "Connect calendar write access to get this in Google Calendar every Saturday."
    else:
        footer = "Posted to your Google Calendar every Saturday at 20:00 (while the app runs)."
    return ui.review_card(
        f"Weekly review · Week {w.week} of {review.total_weeks} · {w.start:%d %b}–{w.end:%d %b}",
        review.headline,
        review.tone,
        review.stats,
        review.went_well,
        review.watch,
        next_title,
        lines,
        review.suggestions,
        footer,
    )


def render_today(data: DashboardData) -> None:
    settings = get_settings()
    service = _service(str(data.db_path), settings.timezone)
    today = datetime.now(settings.tz).date()
    snap = service.snapshot(today)
    path = settings.program_file
    program = _program(str(path), path.stat().st_mtime) if path.is_file() else None

    longest = _longest_recent_km(snap.data, today)
    progress = []
    for g in snap.goals:
        plan_peak = (
            program.peak_long_run if program and g.target_date == program.race.date else None
        )
        progress.append(goal_progress(g, today, longest, plan_peak))
    primary = _primary_goal(progress)
    if primary is not None and primary.goal.target_date:
        g = primary.goal
        big = f"{primary.days_to_go} days"
        if program and g.target_date == program.race.date:
            race = program.race
            sub = f"to the {race.name} · goal {race.goal_time} · {g.target_date:%d %b %Y}"
        else:
            sub = f"to your {g.description.lower()} · {g.target_date:%d %b %Y}"
    else:
        big, sub = "Let's train", "Add a goal to start the countdown."
    _html(
        ui.hero(
            f"{today:%A · %d %B %Y}",
            big,
            sub,
            snap.safety.readiness,
            _plan_progress_pct(program, today),
            "of plan",
            chips=_hero_chips(snap, program, today),
        )
    )

    trained_today = not snap.data.activities[
        snap.data.activities["date"] == pd.Timestamp(today)
    ].empty
    if program is not None and not trained_today:
        brief = _brief_html(snap, program, today)
        if brief:
            _html(brief)
    analysis = _analysis_html(snap, service, program, today)
    if analysis:
        _html(analysis)
    _checkin_section(snap, today, data.db_path, trained_today)
    review = _review_html(snap, program, today, data.db_path) if program else None
    if review and today.weekday() == 5:  # Saturday: the week's review comes first
        _html(review)

    c1, c2, c3 = st.columns(3, gap="medium")
    with c1:
        _html(_today_card(snap, service, program, today))
    with c2:
        _html(_tomorrow_card(snap, program, today))
    with c3:
        _html(_recovery_card(snap))

    if program is not None:
        view = build_week_plan(program, snap.data.activities, snap.safety, today, snap.data.planned)
        if view is not None:
            _html(_program_banner(program, view))
        with st.expander("📄 Full training program (English)"):
            _program_tables(program, today)
    else:
        st.info(
            f"No training program loaded. Put it at `{path.as_posix()}` "
            "to see your weekly plan here."
        )

    _html(ui.section("Goals", "long-run progression toward each race"))
    cols = st.columns(max(1, min(2, len(progress))), gap="medium")
    for col, p in zip(cols * len(progress), progress, strict=False):
        with col:
            _html(_goal_card(p))
    if not progress:
        st.info(
            "No goals yet. Add one with "
            "`python -m coach.goals add --desc ... --date ... --distance ...`."
        )

    if review and today.weekday() != 5:
        _html(ui.section("Last week's review", "rule-based · posted every Saturday 20:00"))
        _html(review)
    elif review is None:
        _html(ui.section("Last week", "rules-based review"))
        _html(_last_week_card(snap, today))

    _html(ui.section("Recent workouts", "last 7 days"))
    recent = snap.data.activities[
        snap.data.activities["date"] >= pd.Timestamp(today) - pd.Timedelta(days=7)
    ]
    if recent.empty:
        st.info("No workouts in the last 7 days.")
    workout_cols = st.columns(2, gap="medium")
    ordered = recent.sort_values("start_utc", ascending=False)["id"]
    for i, activity_id in enumerate(ordered):
        with workout_cols[i % 2]:
            _html(_workout_html(snap, service, int(activity_id), settings.tz))

    _html(ui.section("Chat with Claude", "free, with your own Claude account"))
    with st.expander("📋 Copy my training summary"):
        st.caption(
            "Click the copy icon (top-right of the box), then paste it into a chat at claude.ai "
            "and ask anything. It contains numbers only: no names, titles or locations. "
            "This app makes no AI calls and costs nothing."
        )
        st.code(summary_for_chat(snap.context), language="markdown", wrap_lines=True)
