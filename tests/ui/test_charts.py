from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import pytest

from coach.db import connect, migrate
from coach.services.dashboard import DashboardView, build_view, load_dashboard_data
from coach.synthetic import generate, seed_database
from coach.ui import charts
from coach.ui import format as fmt

TZ = ZoneInfo("Asia/Jerusalem")


@pytest.fixture(scope="module")
def view(tmp_path_factory: pytest.TempPathFactory) -> DashboardView:
    db = tmp_path_factory.mktemp("charts") / "dev.sqlite"
    conn = connect(db)
    migrate(conn)
    seed_database(conn, generate(70, end_date=date(2026, 11, 16), seed=1, tz=TZ))
    conn.close()
    return build_view(load_dashboard_data(db, TZ, today=date(2026, 11, 17)), 8)


def _all_figures(view: DashboardView, demo: bool) -> dict[str, go.Figure]:
    last = pd.Timestamp(view.end)
    return {
        "training_time": charts.training_time(view.training_time, demo=demo, last_day=last),
        "weekly_distance": charts.weekly_distance(view.weekly_running, demo=demo, last_day=last),
        "pace_trend": charts.pace_trend(view.pace_trend, demo=demo),
        "strength_weekly": charts.strength_weekly(view.strength_weekly, demo=demo, last_day=last),
        "muscle_heatmap": charts.muscle_heatmap(view.muscle_weekly, demo=demo),
        "muscle_average": charts.muscle_average(view.muscle_average, demo=demo),
        "hrv": charts.hrv(view.recovery, demo=demo),
        "sleep": charts.sleep(view.recovery, demo=demo),
        "resting_hr": charts.resting_hr(view.recovery, demo=demo),
        "plan_grid": charts.plan_grid(view.plan_status, demo=demo),
        "weekly_adherence": charts.weekly_adherence(view.adherence, demo=demo),
    }


def _texts(fig: go.Figure) -> list[str]:
    return [a.text for a in fig.layout.annotations]


def test_every_chart_is_tagged_demo(view: DashboardView) -> None:
    for name, fig in _all_figures(view, demo=True).items():
        assert any(charts.DEMO_TAG in t for t in _texts(fig)), name
        assert fig.data, name


def test_real_data_charts_have_no_demo_tag(view: DashboardView) -> None:
    for name, fig in _all_figures(view, demo=False).items():
        assert not any(charts.DEMO_TAG in t for t in _texts(fig)), name


def test_entity_colors_are_fixed(view: DashboardView) -> None:
    figs = _all_figures(view, demo=True)
    assert figs["weekly_distance"].data[0].marker.color == charts.RUN
    assert figs["strength_weekly"].data[0].marker.color == charts.STRENGTH
    colors = {t.name: t.marker.color for t in figs["training_time"].data}
    assert colors == {"Running": charts.RUN, "Strength": charts.STRENGTH}


def test_single_y_axis_everywhere(view: DashboardView) -> None:
    for name, fig in _all_figures(view, demo=True).items():
        layout = fig.to_plotly_json()["layout"]
        assert not [k for k in layout if k.startswith("yaxis") and k != "yaxis"], name
        assert all(getattr(t, "yaxis", None) in (None, "y") for t in fig.data), name


def test_pace_axis_is_reversed_with_minute_labels(view: DashboardView) -> None:
    fig = charts.pace_trend(view.pace_trend, demo=True)
    assert fig.layout.yaxis.autorange == "reversed"
    assert all(":" in t for t in fig.layout.yaxis.ticktext)


def test_plan_grid_uses_symbol_and_label_per_status(view: DashboardView) -> None:
    fig = charts.plan_grid(view.plan_status, demo=True)
    symbols = {t.name: t.marker.symbol for t in fig.data}
    assert symbols["Completed"] == "circle"
    assert symbols["Upcoming"] == "circle-open"
    assert len(set(symbols.values())) == len(symbols)  # never color alone


def test_empty_data_shows_message() -> None:
    empty = pd.DataFrame(
        {"week_start": pd.to_datetime([]), "distance_km": [], "runs": [], "pace_s_per_km": []}
    )
    fig = charts.weekly_distance(empty, demo=False, last_day=pd.Timestamp("2026-11-16"))
    assert charts.NO_DATA in _texts(fig)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(330.0, "5:30 /km"), (299.6, "5:00 /km"), (None, "—"), (float("nan"), "—")],
)
def test_format_pace(value: float | None, expected: str) -> None:
    assert fmt.pace(value) == expected


def test_format_helpers() -> None:
    assert fmt.duration(2700) == "45 min"
    assert fmt.duration(6000) == "1 h 40 min"
    assert fmt.duration(None) == "—"
    assert fmt.number(33.456, "km") == "33.5 km"
    assert fmt.number(None, "km") == "—"
    assert fmt.workout_label("strength_full") == "Gym session"
    assert fmt.workout_label(float("nan")) == "Unplanned workout"
    assert fmt.hrv_status_short("below normal range") == "Below range"
    assert fmt.hrv_status_short("insufficient data") == "Not enough data"
