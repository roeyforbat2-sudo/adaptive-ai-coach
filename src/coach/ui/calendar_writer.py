"""'Write my plan to Google Calendar': preview, then one approving click.

Nothing is written until the athlete presses the approve button under a preview they have
just seen. The preview is a live READ of the calendar; the write uses a separate grant.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

from coach.config import Settings
from coach.db.connection import DbTarget
from coach.program import load_program
from coach.services import calendar_write as cw

PREVIEW_KEY = "calendar_write_preview"
ACTION_LABELS = {"create": "➕ New", "update": "✏️ Update", "unchanged": "✓ Up to date"}


def _next_sunday(today: date) -> date:
    return today + timedelta(days=(6 - today.weekday()) % 7 or 7)


def _option_label(change: cw.Change) -> str:
    return f"{change.planned.day:%a %d %b} · {change.planned.title}"


def _preview_table(preview: cw.Preview) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Day": [f"{c.planned.day:%a %d %b %Y}" for c in preview.changes],
            "Change": [ACTION_LABELS[c.action] for c in preview.changes],
            "New title": [c.planned.title for c in preview.changes],
            "Time": [f"{c.planned.start:%H:%M}–{c.planned.end:%H:%M}" for c in preview.changes],
            "Now in your calendar": [
                c.existing.title if c.existing else "—" for c in preview.changes
            ],
        }
    )


def render_calendar_writer(
    db_path: DbTarget, settings: Settings, today: date, on_written: Callable[[], None]
) -> None:
    st.markdown("#### 📅 Write my plan to Google Calendar")
    done = st.session_state.pop("calendar_write_done", None)
    if done:
        st.success(done)
    st.caption(
        "Each training day becomes one event (default 06:00–07:00). The title is the "
        "session; the description has the full workout. Your existing training event on the "
        "same day is updated in place, so nothing is duplicated, and nothing is ever deleted. "
        "Saturday stays free (the long run is on Friday)."
    )
    program = load_program(settings.program_file)
    if program is None:
        st.info("No training program found (data/program.json).")
        return
    if not cw.read_connected():
        st.info("Connect Google Calendar first: `python -m coach.integrations.gcal.auth`.")
        return

    last_day = program.race.date
    c1, c2, c3, c4 = st.columns(4)
    first = c1.date_input(
        "From", value=min(_next_sunday(today), last_day), min_value=today, max_value=last_day
    )
    last = c2.date_input("To", value=last_day, min_value=today, max_value=last_day)
    start_time = c3.time_input("Start time", value=time(6, 0), step=timedelta(minutes=15))
    minutes = c4.number_input("Minutes", min_value=15, max_value=240, value=60, step=15)

    if st.button("🔍 Preview changes", help="Reads your calendar; changes nothing"):
        try:
            with st.spinner("Reading your calendar..."):
                st.session_state[PREVIEW_KEY] = cw.preview(
                    settings,
                    program,
                    first,
                    last,
                    start_time=start_time,
                    minutes=int(minutes),
                )
        except cw.CalendarWriteError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Could not read your calendar ({type(exc).__name__}).")

    preview: cw.Preview | None = st.session_state.get(PREVIEW_KEY)
    if preview is None:
        return
    new, upd, same = preview.count("create"), preview.count("update"), preview.count("unchanged")
    st.markdown(
        f"**{preview.first:%d %b} – {preview.last:%d %b %Y}:** {new} new · {upd} to update · "
        f"{same} already up to date"
    )
    st.dataframe(_preview_table(preview), hide_index=True, width="stretch", height=320)
    with st.expander("See an event's description"):
        options = list(range(len(preview.changes)))
        if options:
            i = st.selectbox(
                "Event",
                options,
                format_func=lambda k: _option_label(preview.changes[k]),
            )
            st.code(preview.changes[i].planned.description, language=None, wrap_lines=True)

    if not preview.pending:
        st.success("Your calendar already matches the plan.")
        return
    if not cw.write_connected():
        st.warning(
            "To write these events, give the app calendar write access once. In PowerShell: "
            "`.venv\\Scripts\\python.exe -m coach.integrations.gcal.auth --write`, then "
            "preview again.",
            icon="🔐",
        )
        return
    left, right = st.columns(2)
    if left.button(
        f"✅ Write {len(preview.pending)} changes to my calendar", type="primary", width="stretch"
    ):
        try:
            with st.spinner("Writing to your calendar..."):
                result = cw.apply(db_path, settings, preview)
        except cw.CalendarWriteError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            st.error(f"Writing failed ({type(exc).__name__}). Nothing else was changed.")
            return
        del st.session_state[PREVIEW_KEY]
        parts = [f"{result.created} created", f"{result.updated} updated"]
        if result.stale:
            parts.append(f"{result.stale} skipped because they changed since the preview")
        if result.failed:
            parts.append(f"{result.failed} failed ({', '.join(result.errors[:5])})")
        st.session_state["calendar_write_done"] = (
            f"Calendar updated at {datetime.now(settings.tz):%H:%M}: " + ", ".join(parts) + "."
        )
        on_written()
    if right.button("Cancel", width="stretch"):
        del st.session_state[PREVIEW_KEY]
        st.rerun()
