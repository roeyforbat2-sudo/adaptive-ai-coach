"""Adaptive AI Coach dashboard.

Run with:  .venv/Scripts/python.exe -m streamlit run src/coach/ui/app.py
Read-only: the database is opened with SQLite mode=ro. No external services are contacted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from coach import access, backup
from coach.analytics.dashboard import PeriodComparison
from coach.config import Settings, get_settings
from coach.db.connection import DbTarget, as_target, changed_marker, describe
from coach.engine.safety import SafetyAssessment
from coach.program import load_program
from coach.services import daily_messages, weekly_review
from coach.services import sync as live_sync
from coach.services.dashboard import (
    DashboardData,
    DashboardView,
    DatabaseNotFoundError,
    build_view,
    load_dashboard_data,
    resolve_db_path,
)
from coach.ui import charts
from coach.ui import components as ui
from coach.ui import format as fmt
from coach.ui.calendar_writer import render_calendar_writer
from coach.ui.style import inject as inject_style
from coach.ui.today_tab import render_today
from coach.week_plan import build_week_plan

RANGES: dict[str, int | None] = {"4 weeks": 4, "8 weeks": 8, "12 weeks": 12, "All": None}
TAB_LABELS = [
    "🏠 Today",
    "📊 Overview",
    "🏃 Running",
    "🏋️ Strength",
    "💤 Recovery",
    "📅 Training Calendar",
]
PLOTLY_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"]}


@st.cache_data(show_spinner=False, ttl=300)
def _load(db_path: str, mtime: float, tz_name: str, today: date, synced: str) -> DashboardData:
    # `mtime` and `synced` (last sync times) are cache keys: new data is picked up at once.
    del mtime, synced
    return load_dashboard_data(as_target(db_path), ZoneInfo(tz_name), today)


def _sync_marker(db_path: DbTarget) -> str:
    return "|".join(
        str(live_sync.last_success(db_path, s)) for s in (live_sync.GCAL, live_sync.GARMIN)
    )


def _ago(when: datetime | None) -> str:
    if when is None:
        return "never"
    minutes = int((datetime.now(UTC) - when).total_seconds() // 60)
    return (
        "just now"
        if minutes < 1
        else f"{minutes} min ago"
        if minutes < 120
        else f"{minutes // 60} h ago"
    )


CALENDAR_MAX_AGE = timedelta(minutes=5)
GARMIN_MAX_AGE = timedelta(minutes=30)


@st.fragment(run_every=timedelta(minutes=5))
def _sync_panel(db_path: DbTarget, settings: Settings) -> None:
    """Keeps Garmin and Google Calendar fresh while the page is open (read-only, free).

    Runs on page load and every 5 minutes: the calendar is re-read when older than 5 min,
    Garmin when older than 30 min. If anything changed, the whole page re-renders.
    """
    sources = [
        (live_sync.GCAL, "Calendar", CALENDAR_MAX_AGE),
        (live_sync.GARMIN, "Garmin", GARMIN_MAX_AGE),
    ]
    force = st.button(
        "🔄 Sync now",
        width="stretch",
        type="primary",
        help="Re-read Google Calendar and Garmin now",
    )
    changed, errors = False, []
    for source, _, max_age in sources:
        if not live_sync.connected(source):
            continue
        if force or live_sync.is_stale(db_path, source, max_age):
            with st.spinner(
                "Syncing your calendar..." if source == live_sync.GCAL else "Syncing Garmin..."
            ):
                outcome = (
                    live_sync.sync_calendar(db_path, settings)
                    if source == live_sync.GCAL
                    else live_sync.sync_garmin(db_path, settings)
                )
            changed |= outcome.changed or (force and outcome.ok)
            if not outcome.ok:
                errors.append(outcome.message)
    # In the cloud, the scheduled jobs are the ONLY posters (brief, review, backup); the
    # dashboard posting too caused a duplicate brief on 2026-11-19. On the PC it still posts.
    cloud = settings.auth_mode == "iap"
    if not cloud:
        # Saturday 20:00: post the weekly review to Google Calendar (once per week, automatic).
        posted = weekly_review.publish_if_due(db_path, settings)
        if posted is not None and not posted.ok:
            errors.append(posted.message)
        # 04:30-09:00: today's morning brief; and the daily 17:30 check-in reminder (once).
        for daily in (
            daily_messages.publish_brief_if_due(db_path, settings),
            daily_messages.ensure_checkin_reminder(db_path, settings),
        ):
            if daily is not None and not daily.ok:
                errors.append(daily.message)
    rows: list[tuple[str, str, str]] = []
    for source, label, max_age in sources:
        if not live_sync.connected(source):
            rows.append((label, "not connected", "off"))
            continue
        last_sync = live_sync.last_success(db_path, source)
        stale = last_sync is None or datetime.now(UTC) - last_sync > 2 * max_age
        rows.append((label, _ago(last_sync), "stale" if stale else "ok"))
    # Once a day: back up the database and program (OneDrive by default).
    try:
        if not cloud:  # in the cloud the nightly job backs up
            backup.backup_if_due(settings)
    except Exception as exc:  # a failed backup must never break the page
        errors.append(f"Backup failed ({type(exc).__name__}).")
    latest = next(iter(backup.list_backups(backup.default_dir(settings))), None)
    rows.append(
        (
            "Backup",
            f"{latest.when:%a %d %b %H:%M}" if latest else "none yet",
            "ok" if latest else "off",
        )
    )
    last = weekly_review.last_posted(db_path)
    rows.append(
        (
            "Weekly review",
            f"posted {last:%a %d %b}" if last else "Saturday 20:00",
            "ok" if last else "off",
        )
    )
    rows += [("Problem", msg, "err") for msg in errors]
    st.markdown(ui.side_status(rows), unsafe_allow_html=True)
    if changed:
        _load.clear()
        st.rerun(scope="app")


def _chart(fig: object) -> None:
    st.plotly_chart(fig, width="stretch", theme=None, config=PLOTLY_CONFIG)


def _delta(value: float | None, unit: str = "", digits: int = 1) -> str | None:
    """Neutral comparison text. Volume going down is not 'bad' (e.g., a taper)."""
    if value is None:
        return None
    if abs(value) < 0.5 * 10**-digits:
        return "same as 4-wk avg"
    return f"{value:+.{digits}f} {unit} vs 4-wk avg".replace("  ", " ")


_COMPARE_HELP = "Compared with your weekly average over the 4 weeks before (descriptive only)."


def _compare_metric(
    col: DeltaGenerator, label: str, comparison: PeriodComparison, unit: str, digits: int
) -> None:
    col.metric(
        label,
        fmt.number(comparison.last_7d, unit, digits),
        _delta(comparison.delta, unit, 1),
        delta_color="off",
        border=True,
        help=_COMPARE_HELP,
    )


# --- sections --------------------------------------------------------------------------


def render_overview(data: DashboardData, view: DashboardView) -> None:
    ov = view.overview
    st.subheader("Last 7 days")
    cols = st.columns(4)
    _compare_metric(cols[0], "Running distance", ov.run_km, "km", 1)
    _compare_metric(cols[1], "Runs", ov.runs, "", 0)
    _compare_metric(cols[2], "Strength sessions", ov.strength_sessions, "", 0)
    _compare_metric(cols[3], "Training time", ov.training_hours, "h", 1)

    cols = st.columns(4)
    cols[0].metric(
        "Plan adherence",
        fmt.number(ov.adherence_pct_7d, "%", 0),
        border=True,
        help="Completed ÷ planned workouts that were due in the last 7 days.",
    )
    cols[1].metric("Sleep (7-day avg)", fmt.number(ov.sleep_h_7d, "h"), border=True)
    cols[2].metric(
        "HRV (7-day avg)",
        fmt.number(ov.hrv_7d, "ms"),
        border=True,
        help=f"Status vs your 60-day normal range: {ov.hrv_status}.",
    )
    cols[3].metric("Resting HR (7-day avg)", fmt.number(ov.rhr_7d, "bpm"), border=True)
    st.caption(f"HRV status: **{ov.hrv_status}** (descriptive, not a readiness verdict).")

    left, right = st.columns([1, 1], gap="large")
    with left:
        _chart(
            charts.training_time(
                view.training_time, demo=data.is_demo, last_day=pd.Timestamp(view.end)
            )
        )
    with right:
        st.markdown("**Upcoming workouts · next 7 days**")
        if view.upcoming.empty:
            st.info("No planned workouts in the next 7 days.")
        else:
            up = view.upcoming
            st.dataframe(
                pd.DataFrame(
                    {
                        "Day": [
                            f"📍 {d:%a %d %b}" if s == "today" else f"{d:%a %d %b}"
                            for d, s in zip(up["date"], up["status"], strict=True)
                        ],
                        "Workout": [fmt.workout_label(t) for t in up["workout_type"]],
                        "Target": [fmt.duration(s) for s in up["target_duration_s"]],
                        "Distance": [
                            fmt.number(m / 1000 if pd.notna(m) else None, "km")
                            for m in up["target_distance_m"]
                        ],
                    }
                ),
                hide_index=True,
                width="stretch",
            )


def render_running(data: DashboardData, view: DashboardView) -> None:
    weekly = view.weekly_running
    cols = st.columns(4)
    cols[0].metric("Distance in range", fmt.number(weekly["distance_km"].sum(), "km"), border=True)
    cols[1].metric("Runs", f"{int(weekly['runs'].sum())}", border=True)
    total_km = weekly["distance_km"].sum()
    avg_pace = weekly["duration_h"].sum() * 3600 / total_km if total_km else None
    cols[2].metric(
        "Average pace",
        fmt.pace(avg_pace),
        border=True,
        help="Total running time ÷ total distance (runs with a distance only).",
    )
    longest = view.run_history["distance_km"].max() if not view.run_history.empty else None
    cols[3].metric("Longest run", fmt.number(longest, "km"), border=True)

    left, right = st.columns(2, gap="large")
    with left:
        _chart(charts.weekly_distance(weekly, demo=data.is_demo, last_day=pd.Timestamp(view.end)))
    with right:
        _chart(charts.pace_trend(view.pace_trend, demo=data.is_demo))

    st.markdown("**Activity history**")
    hist = view.run_history
    st.dataframe(
        pd.DataFrame(
            {
                "Date": [f"{d:%a %d %b %Y}" for d in hist["date"]],
                "Activity": hist["name"].fillna("Run"),
                "Distance (km)": hist["distance_km"],
                "Time": [fmt.duration(m * 60) for m in hist["duration_min"]],
                "Pace": [fmt.pace(p) for p in hist["pace_s_per_km"]],
                "Avg HR": hist["avg_hr"],
                "Elevation (m)": hist["elevation_gain_m"],
            }
        ),
        hide_index=True,
        width="stretch",
        height=360,
        column_config={"Distance (km)": st.column_config.NumberColumn(format="%.2f")},
    )


def render_strength(data: DashboardData, view: DashboardView) -> None:
    weekly = view.strength_weekly
    n_weeks = max(len(weekly), 1)
    cols = st.columns(3)
    cols[0].metric("Sessions in range", f"{int(weekly['sessions'].sum())}", border=True)
    cols[1].metric("Sessions / week", fmt.number(weekly["sessions"].sum() / n_weeks), border=True)
    cols[2].metric(
        "Working sets / week",
        fmt.number(weekly["working_sets"].sum() / n_weeks),
        border=True,
        help="Non-warm-up sets. Effort-based (RIR) hard-set counts come later.",
    )

    _chart(charts.strength_weekly(weekly, demo=data.is_demo, last_day=pd.Timestamp(view.end)))
    left, right = st.columns([3, 2], gap="large")
    with left:
        _chart(charts.muscle_heatmap(view.muscle_weekly, demo=data.is_demo))
    with right:
        _chart(charts.muscle_average(view.muscle_average, demo=data.is_demo))
    st.caption(
        "Volume = working sets per muscle group (each exercise counts toward its primary "
        "muscle group only). Muscle groups never trained in the data are not shown."
    )


def render_recovery(data: DashboardData, view: DashboardView) -> None:
    rec = view.recovery
    latest = rec.dropna(subset=["hrv_7d"]).tail(1)
    cols = st.columns(4)
    cols[0].metric(
        "HRV (7-day avg)",
        fmt.number(latest["hrv_7d"].iloc[0] if not latest.empty else None, "ms"),
        border=True,
    )
    cols[1].metric(
        "HRV status",
        fmt.hrv_status_short(latest["hrv_status"].iloc[0]) if not latest.empty else fmt.MISSING,
        border=True,
        help="7-day average vs your 60-day mean ±1 SD of ln(rMSSD); needs ≥14 nights.",
    )
    cols[2].metric("Sleep (7-day avg)", fmt.number(view.overview.sleep_h_7d, "h"), border=True)
    cols[3].metric("Resting HR (7-day avg)", fmt.number(view.overview.rhr_7d, "bpm"), border=True)

    _chart(charts.hrv(rec, demo=data.is_demo))
    left, right = st.columns(2, gap="large")
    with left:
        _chart(charts.sleep(rec, demo=data.is_demo))
    with right:
        _chart(charts.resting_hr(rec, demo=data.is_demo))
    st.caption(
        "These are descriptive trends compared with your own baseline, not a diagnosis or "
        "readiness score. Missing nights are left as gaps."
    )


def render_calendar(data: DashboardData, view: DashboardView) -> None:
    status = view.plan_status
    past = status[status["status"].isin(["completed", "missed"])]
    cols = st.columns(4)
    done, missed = (
        int((past["status"] == "completed").sum()),
        int((past["status"] == "missed").sum()),
    )
    cols[0].metric("Completed", f"{done}", border=True)
    cols[1].metric("Missed", f"{missed}", border=True)
    cols[2].metric(
        "Adherence",
        fmt.number(100 * done / (done + missed) if done + missed else None, "%", 0),
        border=True,
    )
    cols[3].metric(
        "Unplanned workouts", f"{int((status['status'] == 'unplanned').sum())}", border=True
    )

    _chart(charts.plan_grid(status, demo=data.is_demo))
    left, right = st.columns([3, 2], gap="large")
    with left:
        _chart(charts.weekly_adherence(view.adherence, demo=data.is_demo))
    with right:
        st.markdown("**Workout list**")
        st.dataframe(
            pd.DataFrame(
                {
                    "Day": [f"{d:%a %d %b}" for d in status["date"]],
                    "Workout": [fmt.workout_label(t) for t in status["workout_type"]],
                    "Status": [fmt.STATUS_LABELS[s] for s in status["status"]],
                }
            ).iloc[::-1],
            hide_index=True,
            width="stretch",
            height=330,
        )
    st.caption(
        "A planned workout counts as completed when an activity of the same kind (run or "
        "strength) is recorded on the same day. The plan is read-only here; calendar changes "
        "will always require your explicit approval."
    )


@st.cache_resource
def _limiter() -> access.Limiter:
    """One limiter for the whole server, so reloading the page does not reset it."""
    return access.Limiter()


def _require_passcode() -> None:
    """Who may see the dashboard.

    Cloud (auth_mode "iap"): only a verified, allowed Google account behind IAP, checked once
    per session; everything else is refused (fail closed). Local: this PC freely, other
    devices with the passcode.
    """
    settings = get_settings()
    if settings.auth_mode == "iap":
        if st.session_state.get("iap_user"):
            return
        user = access.iap_user(
            dict(st.context.headers), settings.iap_audience, settings.allowed_email_set
        )
        if user is None:
            st.title("Adaptive AI Coach")
            st.error("Access denied. Sign in with an allowed Google account.", icon="🔒")
            st.stop()
        st.session_state["iap_user"] = user
        return
    if st.session_state.get("unlocked") or not access.is_remote(dict(st.context.headers)):
        return
    st.title("Adaptive AI Coach")
    if not access.passcode_is_set():
        st.error(
            "Opening the coach from another device is switched off until you set a passcode. "
            "On your PC, run `python -m coach.set_passcode`.",
            icon="🔒",
        )
        st.stop()
    limiter = _limiter()
    with st.form("unlock", border=True):
        st.markdown("🔒 **Enter your passcode**")
        code = st.text_input("Passcode", type="password", label_visibility="collapsed")
        submitted = st.form_submit_button("Unlock", type="primary", width="stretch")
    if submitted:
        if limiter.check(code):
            st.session_state["unlocked"] = True
            st.rerun()
        wait = limiter.wait_s()
        st.error(f"Too many wrong tries. Wait {wait:.0f} seconds." if wait else "Wrong passcode.")
    st.stop()


# --- page ------------------------------------------------------------------------------


def _side_week(slot: DeltaGenerator, data: DashboardData, settings: Settings, today: date) -> None:
    """Sidebar card: program week, km this week, plan progress, race countdown."""
    program = load_program(settings.program_file)
    week = program.week_for(today) if program else None
    if program is None or week is None:
        return
    view = build_week_plan(
        program, data.activities, SafetyAssessment("green", []), today, data.planned
    )
    block = program.block_for(week)
    total = max(w.week for w in program.weeks)
    span = (program.race.date - program.start_date).days or 1
    pct = 100 * (today - program.start_date).days / span
    days = (program.race.date - today).days
    slot.markdown(
        ui.side_week(
            f"Week {week.week} of {total}" + (f" · Block {block.number}" if block else ""),
            f"{week.phase} week",
            view.done_km if view else 0.0,
            view.target_km if view else week.total_km,
            pct,
            f"{days} days",
            f"to {program.race.name}",
        ),
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="Adaptive AI Coach", page_icon="🏃", layout="wide")
    inject_style()
    _require_passcode()
    settings = get_settings()

    try:
        db_path = resolve_db_path(settings.db_target)
    except DatabaseNotFoundError as exc:
        st.title("Adaptive AI Coach")
        st.error(str(exc))
        st.stop()
        return

    now = datetime.now(settings.tz)
    today = now.date()
    # Sidebar order: brand, this week, live data (sync), chart range, about.
    side = st.sidebar
    side.markdown(
        ui.side_brand("Adaptive Coach", "Running + strength · free"), unsafe_allow_html=True
    )
    week_slot = side.container()
    with side.container():
        _sync_panel(db_path, settings)
    data = _load(
        str(db_path), changed_marker(db_path), settings.timezone, today, _sync_marker(db_path)
    )
    _side_week(week_slot, data, settings, today)

    with side:
        st.markdown(
            ui.side_label("📈 Chart range", "Overview · Running · Strength · Recovery"),
            unsafe_allow_html=True,
        )
        choice = (
            st.segmented_control(
                "Chart range", list(RANGES), default="8 weeks", label_visibility="collapsed"
            )
            or "8 weeks"
        )
        with st.expander("ℹ️ About & data"):
            st.caption(f"**Database:** `{describe(db_path)}` (read-only)")
            if data.data_start and data.data_end:
                st.caption(f"**Data:** {data.data_start:%d %b %Y} – {data.data_end:%d %b %Y}")
            st.caption(f"**Timezone:** {settings.timezone}")
            st.caption(
                "Garmin and Calendar sync automatically while the page is open. No AI calls "
                "are made: all coaching here is rule-based and free."
            )

    part = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
    st.markdown(
        ui.page_header(
            f"Good {part} 👋", f"{today:%A, %d %B %Y} · Adaptive AI Coach", "✦ Free · rule-based"
        ),
        unsafe_allow_html=True,
    )
    if data.is_demo:
        st.warning(
            "**DEMO DATA** — everything on this dashboard is synthetic, generated for "
            "development. It is not your real training data.",
            icon="🧪",
        )
    if data.as_of != today:
        st.info(
            f"The demo data ends on {data.data_end:%d %b %Y}; the dashboard is shown as of "
            f"{data.as_of:%d %b %Y}."
        )

    view = build_view(data, RANGES[choice])
    side.caption(f"Charts show {view.start:%d %b} – {view.end:%d %b %Y}")

    tabs = st.tabs(TAB_LABELS)
    with tabs[0]:
        render_today(data)
    sections = (render_overview, render_running, render_strength, render_recovery)
    for tab, render in zip(tabs[1:5], sections, strict=True):
        with tab:
            st.caption(
                f"Showing {view.start:%d %b} – {view.end:%d %b %Y} · change the range in the "
                "sidebar"
            )
            render(data, view)
    with tabs[5]:
        if not data.is_demo:

            def _after_write() -> None:
                live_sync.sync_calendar(db_path, settings)
                _load.clear()
                st.rerun(scope="app")

            with st.container(border=True):
                render_calendar_writer(db_path, settings, today, _after_write)
        render_calendar(data, view)


main()
