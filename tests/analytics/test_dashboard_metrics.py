"""Hand-built fixtures with known answers for the dashboard metrics."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from coach.analytics import dashboard as an

TZ = ZoneInfo("Asia/Jerusalem")


def _acts(rows: list[dict[str, object]]) -> pd.DataFrame:
    base = {
        "source": "synthetic",
        "elevation_gain_m": np.nan,
        "avg_hr": np.nan,
        "max_hr": np.nan,
        "training_load": np.nan,
        "name": "x",
    }
    df = pd.DataFrame([{**base, "id": i + 1, **r} for i, r in enumerate(rows)])
    df["start_utc"] = pd.to_datetime(df["start_utc"], utc=True)
    return an.add_local_dates(df, TZ)


def test_week_start_is_monday() -> None:
    dates = pd.Series(pd.to_datetime(["2026-11-16", "2026-11-19", "2026-11-22"]))  # Mon, Thu, Sun
    assert (an.week_start(dates) == pd.Timestamp("2026-11-16")).all()


def test_local_date_uses_athlete_timezone() -> None:
    # 23:30 UTC on Nov 15 is 01:30 on Nov 16 in Jerusalem (UTC+2).
    acts = _acts(
        [
            {
                "sport": "run",
                "start_utc": "2026-11-15T23:30:00Z",
                "duration_s": 1800,
                "distance_m": 5000.0,
            }
        ]
    )
    assert acts["date"].iloc[0] == pd.Timestamp("2026-11-16")
    assert acts["family"].iloc[0] == "run"


class TestRunning:
    @pytest.fixture
    def acts(self) -> pd.DataFrame:
        return _acts(
            [
                {
                    "sport": "run",
                    "start_utc": "2026-11-02T05:00:00Z",
                    "duration_s": 3000,
                    "distance_m": 10000.0,
                },
                {
                    "sport": "trail_run",
                    "start_utc": "2026-11-04T05:00:00Z",
                    "duration_s": 3600,
                    "distance_m": 10000.0,
                },
                {
                    "sport": "treadmill",
                    "start_utc": "2026-11-05T05:00:00Z",
                    "duration_s": 1200,
                    "distance_m": np.nan,
                },
                {
                    "sport": "strength",
                    "start_utc": "2026-11-03T16:00:00Z",
                    "duration_s": 2700,
                    "distance_m": np.nan,
                },
            ]
        )

    def test_weekly_running_totals_and_time_weighted_pace(self, acts: pd.DataFrame) -> None:
        weekly = an.weekly_running(acts, date(2026, 11, 2), date(2026, 11, 15))
        assert list(weekly["week_start"]) == [
            pd.Timestamp("2026-11-02"),
            pd.Timestamp("2026-11-09"),
        ]
        first = weekly.iloc[0]
        assert first["runs"] == 3
        assert first["distance_km"] == 20.0
        # Pace only from runs WITH distance: (3000 + 3600) s / 20 km = 330 s/km.
        assert first["pace_s_per_km"] == pytest.approx(330.0)

    def test_empty_week_inside_range_is_zero_with_unknown_pace(self, acts: pd.DataFrame) -> None:
        second = an.weekly_running(acts, date(2026, 11, 2), date(2026, 11, 15)).iloc[1]
        assert second["runs"] == 0 and second["distance_km"] == 0
        assert np.isnan(second["pace_s_per_km"])

    def test_run_history_never_invents_pace(self, acts: pd.DataFrame) -> None:
        hist = an.run_history(acts, date(2026, 11, 1), date(2026, 11, 30))
        assert len(hist) == 3  # strength excluded
        assert hist.iloc[0]["date"] == pd.Timestamp("2026-11-05")  # newest first
        assert np.isnan(hist.iloc[0]["pace_s_per_km"])  # treadmill without distance
        assert hist.iloc[1]["pace_s_per_km"] == pytest.approx(360.0)

    def test_pace_trend_needs_three_runs(self) -> None:
        acts = _acts(
            [
                {
                    "sport": "run",
                    "start_utc": f"2026-11-0{d}T05:00:00Z",
                    "duration_s": 300 * 10,
                    "distance_m": 10000.0,
                }
                for d in (1, 3, 5, 7)
            ]
        )
        trend = an.pace_trend(acts, date(2026, 11, 1), date(2026, 11, 30))
        assert trend["rolling_median_s_per_km"].isna().sum() == 2
        assert trend["rolling_median_s_per_km"].iloc[-1] == pytest.approx(300.0)

    def test_weekly_training_time_by_family(self, acts: pd.DataFrame) -> None:
        tt = an.weekly_training_time(acts, date(2026, 11, 2), date(2026, 11, 8))
        hours = tt.set_index("family")["hours"]
        assert hours["run"] == pytest.approx((3000 + 3600 + 1200) / 3600, abs=0.01)
        assert hours["strength"] == pytest.approx(0.75)
        assert hours["other"] == 0


class TestStrength:
    @pytest.fixture
    def sessions_and_sets(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        sessions = pd.DataFrame(
            {
                "session_id": [1, 2],
                "date": pd.to_datetime(["2026-11-02", "2026-11-05"]),
                "source": "synthetic",
                "activity_id": [None, None],
            }
        )
        sets = pd.DataFrame(
            {
                "session_id": [1, 1, 1, 2, 2],
                "date": pd.to_datetime(["2026-11-02"] * 3 + ["2026-11-05"] * 2),
                "exercise": ["Back Squat", "Back Squat", "Bench Press", "Pull-Up", "Pull-Up"],
                "muscle_group": ["quads", "quads", "chest", "back", "back"],
                "is_lower_body": [True, True, False, False, False],
                "set_index": [0, 1, 2, 0, 1],
                "reps": [5] * 5,
                "load_kg": [60.0, 80.0, 60.0, np.nan, np.nan],
                "rir": [np.nan, 2, 2, 1, 1],
                "is_warmup": [True, False, False, False, False],
            }
        )
        return sessions, sets

    def test_weekly_sessions_and_working_sets_exclude_warmups(
        self, sessions_and_sets: tuple[pd.DataFrame, pd.DataFrame]
    ) -> None:
        sessions, sets = sessions_and_sets
        weekly = an.strength_weekly(sessions, sets, date(2026, 11, 2), date(2026, 11, 15))
        assert list(weekly["sessions"]) == [2, 0]
        assert list(weekly["working_sets"]) == [4, 0]

    def test_muscle_group_pivot(self, sessions_and_sets: tuple[pd.DataFrame, pd.DataFrame]) -> None:
        _, sets = sessions_and_sets
        pivot = an.muscle_group_weekly(sets, date(2026, 11, 2), date(2026, 11, 8))
        assert pivot.loc["quads"].iloc[0] == 1  # warm-up excluded
        assert pivot.loc["back"].iloc[0] == 2
        avg = an.muscle_group_average(sets, date(2026, 11, 8), weeks=1)
        assert dict(zip(avg["muscle_group"], avg["avg_weekly_sets"], strict=True)) == {
            "quads": 1.0,
            "chest": 1.0,
            "back": 2.0,
        }


class TestRecovery:
    @staticmethod
    def _wellness(hrv: list[float | None], rhr: int = 50) -> pd.DataFrame:
        days = pd.date_range("2026-09-01", periods=len(hrv), freq="D")
        return pd.DataFrame(
            {
                "date": days,
                "source": "synthetic",
                "sleep_duration_s": 7 * 3600,
                "sleep_score": 80,
                "hrv_rmssd_ms": hrv,
                "resting_hr": rhr,
                "body_battery_high": 80,
                "body_battery_low": 20,
                "stress_avg": 30,
                "steps": 8000,
            }
        )

    def test_baseline_needs_fourteen_values(self) -> None:
        rec = an.recovery_daily(self._wellness([60.0] * 20), date(2026, 9, 1), date(2026, 9, 30))
        assert (rec["hrv_status"].iloc[:13] == "insufficient data").all()
        assert rec["hrv_status"].iloc[19] == "within normal range"

    def test_drop_below_individual_range_is_flagged(self) -> None:
        rng = np.random.default_rng(0)
        hrv = list(60 + rng.normal(0, 3, 40)) + [40.0] * 7
        rec = an.recovery_daily(self._wellness(hrv), date(2026, 9, 1), date(2026, 12, 1))
        assert rec["hrv_status"].iloc[-1] == "below normal range"

    def test_missing_nights_stay_missing(self) -> None:
        hrv: list[float | None] = [60.0] * 10 + [None] * 3 + [60.0] * 5
        rec = an.recovery_daily(self._wellness(hrv), date(2026, 9, 1), date(2026, 9, 30))
        assert rec["hrv"].isna().sum() == 3
        assert rec["sleep_h"].iloc[0] == 7.0


class TestPlanStatus:
    @pytest.fixture
    def status(self) -> pd.DataFrame:
        planned = pd.DataFrame(
            {
                "id": [1, 2, 3, 4, 5, 6],
                "source": "synthetic",
                "date": pd.to_datetime(
                    [
                        "2026-11-10",
                        "2026-11-10",
                        "2026-11-11",
                        "2026-11-12",
                        "2026-11-17",
                        "2026-11-18",
                    ]
                ),
                "sport": ["run", "run", "strength", "run", "run", "run"],
                "workout_type": ["easy", "tempo", "strength_full", "long", "intervals", "easy"],
                "target_duration_s": [2700, 2400, 2700, 6000, 3300, 2700],
                "target_distance_m": np.nan,
                "notes": None,
            }
        )
        acts = _acts(
            [
                {
                    "sport": "run",
                    "start_utc": "2026-11-10T05:00:00Z",
                    "duration_s": 2600,
                    "distance_m": 7000.0,
                },
                {
                    "sport": "strength",
                    "start_utc": "2026-11-11T16:00:00Z",
                    "duration_s": 2500,
                    "distance_m": np.nan,
                },
                {
                    "sport": "strength",
                    "start_utc": "2026-11-13T16:00:00Z",
                    "duration_s": 2000,
                    "distance_m": np.nan,
                },
            ]
        )
        return an.plan_status(
            planned, acts, date(2026, 11, 9), date(2026, 11, 22), as_of=date(2026, 11, 17)
        )

    def test_statuses(self, status: pd.DataFrame) -> None:
        got = [
            (d.strftime("%m-%d"), t if isinstance(t, str) else None, s)
            for d, t, s in zip(
                status["date"], status["workout_type"], status["status"], strict=True
            )
        ]
        assert got == [
            ("11-10", "easy", "completed"),  # one run done, matched to the first plan of the day
            ("11-10", "tempo", "missed"),  # second run that day not done
            ("11-11", "strength_full", "completed"),
            ("11-12", "long", "missed"),
            ("11-13", None, "unplanned"),
            ("11-17", "intervals", "today"),
            ("11-18", "easy", "upcoming"),
        ]

    def test_completed_carries_actuals(self, status: pd.DataFrame) -> None:
        row = status.iloc[0]
        assert row["actual_duration_s"] == 2600 and row["target_duration_s"] == 2700

    def test_weekly_adherence(self, status: pd.DataFrame) -> None:
        adh = an.weekly_adherence(status, date(2026, 11, 9), date(2026, 11, 22)).set_index(
            "week_start"
        )
        week = adh.loc[pd.Timestamp("2026-11-09")]
        assert (week["planned_due"], week["completed"], week["missed"], week["unplanned"]) == (
            4,
            2,
            2,
            1,
        )
        assert week["adherence_pct"] == 50
        nxt = adh.loc[pd.Timestamp("2026-11-16")]
        assert nxt["planned_due"] == 0 and np.isnan(nxt["adherence_pct"]) and nxt["upcoming"] == 2

    def test_upcoming(self, status: pd.DataFrame) -> None:
        up = an.upcoming(status, date(2026, 11, 17), days=7)
        assert list(up["status"]) == ["today", "upcoming"]


def test_overview_comparison_requires_coverage() -> None:
    acts = _acts(
        [
            {
                "sport": "run",
                "start_utc": f"2026-11-{d:02d}T05:00:00Z",
                "duration_s": 3600,
                "distance_m": 10000.0,
            }
            for d in range(1, 17)
        ]
    )
    sessions = pd.DataFrame({"session_id": [], "date": pd.to_datetime([])})
    empty_rec = an.recovery_daily(pd.DataFrame(), date(2026, 11, 1), date(2026, 11, 16))
    empty_status = pd.DataFrame({"date": pd.to_datetime([]), "status": []})
    ov = an.overview(acts, sessions, empty_rec, empty_status, date(2026, 11, 17), date(2026, 11, 1))
    assert ov.run_km.last_7d == pytest.approx(70.0)
    assert ov.run_km.prior_weekly_avg is None  # the 4 weeks before are not covered by data
    assert ov.run_km.delta is None
    assert ov.adherence_pct_7d is None and ov.sleep_h_7d is None and ov.hrv_7d is None
