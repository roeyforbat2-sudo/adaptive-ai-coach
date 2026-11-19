"""Plotly figure builders. Pure functions: view-model DataFrames in, figures out.

Visual rules (dataviz reference palette): fixed series colors by entity (running = blue,
strength = orange), status colors only for plan status and always paired with a symbol
and label, one y-axis per chart, hairline solid grid, hover tooltips on every mark.
Every figure built from synthetic data carries a DEMO DATA tag.
"""

from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go

from coach.ui import format as fmt

# --- palette (dark mode; dark steps of the validated dataviz palette) ------------------
RUN = "#3987e5"  # categorical slot 1 (blue, dark step): running
STRENGTH = "#d95926"  # categorical slot 2 (orange, dark step): strength
OTHER = "#199e70"  # categorical slot 3 (aqua, dark step): other activities
STATUS = {
    "completed": "#0ca30c",  # good
    "missed": "#d03b3b",  # critical
    "today": RUN,
    "upcoming": "#898781",  # muted
    "unplanned": "#9085e9",  # violet (slot 7, dark step), not a status color
}
STATUS_SYMBOL = {
    "completed": "circle",
    "missed": "x-thin-open",
    "today": "star",
    "upcoming": "circle-open",
    "unplanned": "diamond",
}
SURFACE = "#16181d"
INK = "#f0efec"
INK_2 = "#c3c2b7"
MUTED = "#898781"
GRID = "#2c2c2a"
BASELINE = "#383835"
BAND = "rgba(240, 239, 236, 0.08)"
SLEEP_BAR = "#1c5cab"
# Sequential (dark mode): low values recede toward the surface, high values are bright.
BLUE_RAMP = ["#1b2230", "#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"]
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

DEMO_TAG = "DEMO DATA"
NO_DATA = "No data in this period"


def _base(
    fig: go.Figure, title: str, *, demo: bool, y_title: str = "", height: int = 330
) -> go.Figure:
    fig.update_layout(
        title={
            "text": title,
            "x": 0,
            "xanchor": "left",
            "xref": "paper",
            "y": 1,
            "yref": "container",
            "yanchor": "top",
            "pad": {"t": 8},
            "font": {"size": 15, "color": INK},
        },
        font={"family": FONT, "size": 12, "color": INK_2},
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        height=height,
        margin={"l": 8, "r": 16, "t": 72, "b": 8},
        hoverlabel={
            "bgcolor": "#22252b",
            "bordercolor": GRID,
            "font": {"color": INK, "family": FONT},
        },
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": -0.12,
            "x": 0,
            "traceorder": "normal",
            "font": {"color": INK_2},
        },
        bargap=0.35,
        barcornerradius=4,
        showlegend=len(fig.data) > 1,
    )
    # automargin: axes reserve room for their own tick labels and titles (never clipped).
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        linecolor=BASELINE,
        ticks="",
        tickfont={"color": MUTED},
        automargin=True,
    )
    fig.update_yaxes(
        gridcolor=GRID,
        gridwidth=1,
        zeroline=False,
        linecolor=BASELINE,
        tickfont={"color": MUTED},
        title={"text": y_title, "font": {"color": MUTED, "size": 12}, "standoff": 8},
        automargin=True,
    )
    if demo:
        # Left, between title and plot: clear of Plotly's hover toolbar at top-right.
        fig.add_annotation(
            text=f"<b>{DEMO_TAG}</b>",
            xref="paper",
            yref="paper",
            x=0,
            y=1.0,
            yshift=6,
            xanchor="left",
            yanchor="bottom",
            showarrow=False,
            font={"size": 10, "color": "#0b0b0b"},  # dark text on the warning yellow
            bgcolor="#fab219",
            borderpad=2,
        )

    def _empty(trace: object) -> bool:
        x = getattr(trace, "x", None)
        return x is None or len(x) == 0

    if not fig.data or all(_empty(t) for t in fig.data):
        fig.add_annotation(
            text=NO_DATA,
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"color": MUTED, "size": 13},
        )
    return fig


def _week_hover_label(weeks: pd.Series, last_day: pd.Timestamp) -> list[str]:
    labels = []
    for week in pd.to_datetime(weeks):
        suffix = " (in progress)" if week + pd.Timedelta(days=6) > last_day else ""
        labels.append(f"Week of {week:%d %b}{suffix}")
    return labels


# --- overview --------------------------------------------------------------------------


def training_time(df: pd.DataFrame, *, demo: bool, last_day: pd.Timestamp) -> go.Figure:
    fig = go.Figure()
    for family, color, name in (
        ("run", RUN, "Running"),
        ("strength", STRENGTH, "Strength"),
        ("other", OTHER, "Other"),
    ):
        part = df[df["family"] == family]
        if family == "other" and part["hours"].sum() == 0:
            continue
        fig.add_bar(
            x=part["week_start"],
            y=part["hours"],
            name=name,
            marker={"color": color, "line": {"color": SURFACE, "width": 2}},
            customdata=_week_hover_label(part["week_start"], last_day),
            hovertemplate="%{customdata}<br>" + name + ": %{y:.1f} h<extra></extra>",
        )
    fig.update_layout(barmode="stack")
    return _base(fig, "Weekly training time", demo=demo, y_title="hours")


# --- running ---------------------------------------------------------------------------


def weekly_distance(df: pd.DataFrame, *, demo: bool, last_day: pd.Timestamp) -> go.Figure:
    hover = [
        f"{label}<br>{km:.1f} km · {runs} run{'s' if runs != 1 else ''}<br>Avg pace {fmt.pace(p)}"
        for label, km, runs, p in zip(
            _week_hover_label(df["week_start"], last_day),
            df["distance_km"],
            df["runs"],
            df["pace_s_per_km"],
            strict=True,
        )
    ]
    fig = go.Figure(
        go.Bar(
            x=df["week_start"],
            y=df["distance_km"],
            name="Distance",
            marker={"color": RUN},
            customdata=hover,
            hovertemplate="%{customdata}<extra></extra>",
        )
    )
    return _base(fig, "Weekly running distance", demo=demo, y_title="km")


def _pace_ticks(values: pd.Series) -> tuple[list[float], list[str]]:
    clean = values.dropna()
    if clean.empty:
        return [], []
    lo = math.floor(clean.min() / 15) * 15
    hi = math.ceil(clean.max() / 15) * 15
    step = 15 if hi - lo <= 120 else 30
    ticks = list(range(int(lo), int(hi) + 1, step))
    return [float(t) for t in ticks], [fmt.pace(t).replace(" /km", "") for t in ticks]


def pace_trend(df: pd.DataFrame, *, demo: bool) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(
        x=df["date"],
        y=df["pace_s_per_km"],
        mode="markers",
        name="Each run",
        marker={"color": RUN, "size": 8, "opacity": 0.45, "line": {"color": SURFACE, "width": 2}},
        customdata=list(
            zip([fmt.pace(p) for p in df["pace_s_per_km"]], df["distance_km"], strict=True)
        ),
        hovertemplate="%{x|%a %d %b}<br>%{customdata[0]} · %{customdata[1]:.1f} km<extra></extra>",
    )
    fig.add_scatter(
        x=df["date"],
        y=df["rolling_median_s_per_km"],
        mode="lines",
        name="28-day median",
        line={"color": RUN, "width": 2},
        customdata=[fmt.pace(p) for p in df["rolling_median_s_per_km"]],
        hovertemplate="%{x|%d %b}<br>28-day median %{customdata}<extra></extra>",
    )
    tickvals, ticktext = _pace_ticks(df["pace_s_per_km"])
    fig = _base(fig, "Pace trend (faster ↑)", demo=demo, y_title="min/km")
    fig.update_yaxes(autorange="reversed", tickvals=tickvals, ticktext=ticktext)
    return fig


# --- strength --------------------------------------------------------------------------


def strength_weekly(df: pd.DataFrame, *, demo: bool, last_day: pd.Timestamp) -> go.Figure:
    hover = [
        f"{label}<br>{s} session{'s' if s != 1 else ''} · {w} working sets"
        for label, s, w in zip(
            _week_hover_label(df["week_start"], last_day),
            df["sessions"],
            df["working_sets"],
            strict=True,
        )
    ]
    fig = go.Figure(
        go.Bar(
            x=df["week_start"],
            y=df["sessions"],
            name="Sessions",
            marker={"color": STRENGTH},
            customdata=hover,
            hovertemplate="%{customdata}<extra></extra>",
        )
    )
    fig = _base(fig, "Strength sessions per week", demo=demo, y_title="sessions", height=280)
    fig.update_yaxes(dtick=1, rangemode="tozero")
    fig.update_layout(bargap=0.6)
    return fig


def muscle_heatmap(pivot: pd.DataFrame, *, demo: bool) -> go.Figure:
    weeks = [f"{w:%d %b}" for w in pd.to_datetime(pivot.columns)]
    groups = [g.title() for g in pivot.index]
    scale = [[i / (len(BLUE_RAMP) - 1), c] for i, c in enumerate(BLUE_RAMP)]
    fig = go.Figure(
        go.Heatmap(
            z=pivot.to_numpy(),
            x=weeks,
            y=groups,
            colorscale=scale,
            zmin=0,
            xgap=2,
            ygap=2,
            colorbar={
                "title": {"text": "sets", "side": "right"},
                "thickness": 10,
                "outlinewidth": 0,
            },
            hovertemplate="%{y} · week of %{x}<br>%{z} working sets<extra></extra>",
        )
    )
    fig = _base(
        fig, "Working sets by muscle group", demo=demo, height=max(260, 46 + 34 * len(groups))
    )
    fig.update_yaxes(gridcolor=SURFACE, autorange="reversed")
    if not len(groups):
        fig.add_annotation(
            text=NO_DATA,
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font={"color": MUTED},
        )
    return fig


def muscle_average(df: pd.DataFrame, *, demo: bool) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=df["avg_weekly_sets"],
            y=[g.title() for g in df["muscle_group"]],
            orientation="h",
            name="Avg weekly sets",
            marker={"color": STRENGTH},
            hovertemplate="%{y}: %{x:.1f} working sets / week<extra></extra>",
        )
    )
    fig = _base(
        fig,
        "Average weekly sets, last 4 weeks",
        demo=demo,
        y_title="",
        height=max(260, 46 + 34 * len(df)),
    )
    fig.update_xaxes(
        showgrid=True, gridcolor=GRID, title={"text": "sets / week", "font": {"color": MUTED}}
    )
    fig.update_yaxes(showgrid=False)
    return fig


# --- recovery --------------------------------------------------------------------------


def hrv(df: pd.DataFrame, *, demo: bool) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(
        x=pd.concat([df["date"], df["date"][::-1]]),
        y=pd.concat([df["hrv_high"], df["hrv_low"][::-1]]),
        fill="toself",
        fillcolor=BAND,
        line={"width": 0},
        name="Your normal range (60-day)",
        hoverinfo="skip",
    )
    fig.add_scatter(
        x=df["date"],
        y=df["hrv"],
        mode="markers",
        name="Nightly HRV",
        marker={"color": RUN, "size": 7, "opacity": 0.4},
        hovertemplate="%{x|%a %d %b}<br>HRV %{y:.0f} ms<extra></extra>",
    )
    fig.add_scatter(
        x=df["date"],
        y=df["hrv_7d"],
        mode="lines",
        name="7-day average",
        line={"color": RUN, "width": 2},
        customdata=df["hrv_status"],
        hovertemplate="%{x|%d %b}<br>7-day avg %{y:.1f} ms<br>%{customdata}<extra></extra>",
    )
    return _base(fig, "Heart-rate variability (rMSSD)", demo=demo, y_title="ms")


def sleep(df: pd.DataFrame, *, demo: bool) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(
        x=df["date"],
        y=df["sleep_h"],
        name="Nightly sleep",
        marker={"color": SLEEP_BAR},
        hovertemplate="%{x|%a %d %b}<br>%{y:.1f} h<extra></extra>",
    )
    fig.add_scatter(
        x=df["date"],
        y=df["sleep_7d"],
        mode="lines",
        name="7-day average",
        line={"color": RUN, "width": 2},
        hovertemplate="%{x|%d %b}<br>7-day avg %{y:.1f} h<extra></extra>",
    )
    fig = _base(fig, "Sleep duration", demo=demo, y_title="hours")
    fig.update_layout(bargap=0.15)
    return fig


def resting_hr(df: pd.DataFrame, *, demo: bool) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(
        x=df["date"],
        y=df["resting_hr"],
        mode="markers",
        name="Daily resting HR",
        marker={"color": STRENGTH, "size": 7, "opacity": 0.4},
        hovertemplate="%{x|%a %d %b}<br>%{y:.0f} bpm<extra></extra>",
    )
    fig.add_scatter(
        x=df["date"],
        y=df["rhr_7d"],
        mode="lines",
        name="7-day average",
        line={"color": STRENGTH, "width": 2},
        hovertemplate="%{x|%d %b}<br>7-day avg %{y:.1f} bpm<extra></extra>",
    )
    return _base(fig, "Resting heart rate", demo=demo, y_title="bpm")


# --- plan vs. completed ----------------------------------------------------------------

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def plan_grid(df: pd.DataFrame, *, demo: bool) -> go.Figure:
    """Calendar grid: one row per week, one column per weekday; runs left, strength right."""
    fig = go.Figure()
    if df.empty:
        return _base(fig, "Planned vs. completed", demo=demo, height=300)
    weeks = df["date"] - pd.to_timedelta(df["date"].dt.day_of_week, unit="D")
    labels = [f"{w:%d %b}" for w in weeks]
    x = df["date"].dt.day_of_week + df["family"].map({"run": -0.17, "strength": 0.17}).fillna(0)
    for status in ("completed", "missed", "today", "upcoming", "unplanned"):
        mask = df["status"] == status
        if not mask.any():
            continue
        part = df[mask]
        hover = [
            f"{d:%a %d %b}<br>{fmt.workout_label(t)}<br>{fmt.STATUS_LABELS[status]}"
            + (f"<br>Target {fmt.duration(td)}" if pd.notna(td) else "")
            + (f"<br>Done {fmt.duration(ad)}" if pd.notna(ad) else "")
            for d, t, td, ad in zip(
                part["date"],
                part["workout_type"],
                part["target_duration_s"],
                part["actual_duration_s"],
                strict=True,
            )
        ]
        fig.add_scatter(
            x=x[mask],
            y=[labels[i] for i in range(len(df)) if mask.iloc[i]],
            mode="markers",
            name=fmt.STATUS_LABELS[status].split(" ", 1)[1],
            marker={
                "color": STATUS[status],
                "symbol": STATUS_SYMBOL[status],
                "size": 15,
                "line": {
                    "width": 2,
                    "color": STATUS[status] if "open" in STATUS_SYMBOL[status] else SURFACE,
                },
            },
            customdata=hover,
            hovertemplate="%{customdata}<extra></extra>",
        )
    n_weeks = len(set(labels))
    fig = _base(fig, "Planned vs. completed", demo=demo, height=max(300, 90 + 38 * n_weeks))
    fig.update_xaxes(
        tickvals=list(range(7)),
        ticktext=_DAYS,
        range=[-0.6, 6.6],
        side="top",
        showgrid=True,
        gridcolor=GRID,
    )
    fig.update_yaxes(
        autorange="reversed",
        type="category",
        showgrid=True,
        categoryorder="array",
        categoryarray=list(dict.fromkeys(labels)),
    )
    # Weekday labels sit on top here, so the plot starts lower and the DEMO tag sits above them.
    fig.update_layout(showlegend=True, margin={"l": 8, "r": 16, "t": 100, "b": 8})
    fig.update_yaxes(showline=False)
    for ann in fig.layout.annotations:
        if DEMO_TAG in ann.text:
            ann.yshift = 30
    return fig


def weekly_adherence(df: pd.DataFrame, *, demo: bool) -> go.Figure:
    past = df[df["planned_due"] > 0]
    fig = go.Figure()
    fig.add_bar(
        x=past["week_start"],
        y=past["planned_due"],
        name="Planned",
        marker={"color": BASELINE},
        hovertemplate="Week of %{x|%d %b}<br>Planned %{y}<extra></extra>",
    )
    fig.add_bar(
        x=past["week_start"],
        y=past["completed"],
        name="Completed",
        marker={"color": RUN},
        customdata=past["adherence_pct"],
        hovertemplate="Week of %{x|%d %b}<br>Completed %{y} (%{customdata:.0f}%)<extra></extra>",
    )
    fig.update_layout(barmode="group", bargroupgap=0.12)
    fig = _base(fig, "Planned vs. completed per week", demo=demo, y_title="workouts")
    fig.update_yaxes(dtick=2, rangemode="tozero")
    return fig
