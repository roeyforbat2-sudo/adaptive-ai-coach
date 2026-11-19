"""Basic descriptive metrics for the Phase 2 dashboard.

Training-load models (ATL/CTL/TSB/ACWR) come later; see docs/coaching-methodology.md.
Rules followed here:
- Missing measurements stay NaN; rolling statistics require a minimum number of real values.
- A zero (e.g., 0 km in a week) is shown only for periods inside the data's coverage, where
  "no activity recorded" is a fact rather than a gap.
- Dates are the athlete's local calendar dates; weeks start on Monday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

RUN_SPORTS = frozenset({"run", "trail_run", "treadmill"})
STATUS_ORDER = ("completed", "missed", "today", "upcoming", "unplanned")


# --- helpers ---------------------------------------------------------------------------


def week_start(dates: pd.Series) -> pd.Series:
    """Monday of each date's week (dates are naive, normalized datetimes)."""
    return (dates - pd.to_timedelta(dates.dt.day_of_week, unit="D")).dt.normalize()


def _week_index(start: date, end: date) -> pd.DatetimeIndex:
    first = pd.Timestamp(start) - pd.Timedelta(days=start.weekday())
    last = pd.Timestamp(end) - pd.Timedelta(days=end.weekday())
    return pd.date_range(first, last, freq="7D", name="week_start")


def _in_range(df: pd.DataFrame, start: date, end: date, col: str = "date") -> pd.DataFrame:
    return df[(df[col] >= pd.Timestamp(start)) & (df[col] <= pd.Timestamp(end))]


def add_local_dates(activities: pd.DataFrame, tz: ZoneInfo) -> pd.DataFrame:
    """Add `date` (athlete-local calendar date) and `family` (run/strength/other)."""
    out = activities.copy()
    out["date"] = out["start_utc"].dt.tz_convert(tz).dt.tz_localize(None).dt.normalize()
    out["family"] = np.where(
        out["sport"].isin(RUN_SPORTS),
        "run",
        np.where(out["sport"] == "strength", "strength", "other"),
    )
    return out


# --- running ---------------------------------------------------------------------------


def weekly_running(activities: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Per week: runs, distance_km, duration_h, pace_s_per_km (time-weighted; NaN w/o distance)."""
    runs = _in_range(activities[activities["family"] == "run"], start, end).copy()
    runs["week_start"] = week_start(runs["date"])
    with_dist = runs[runs["distance_m"].notna() & (runs["distance_m"] > 0)]
    grouped = runs.groupby("week_start").agg(
        runs=("id", "count"), distance_m=("distance_m", "sum"), duration_s=("duration_s", "sum")
    )
    pace_parts = with_dist.groupby("week_start").agg(
        d_m=("distance_m", "sum"), d_s=("duration_s", "sum")
    )
    out = grouped.reindex(_week_index(start, end))
    out[["runs", "distance_m", "duration_s"]] = out[["runs", "distance_m", "duration_s"]].fillna(0)
    pace = (pace_parts["d_s"] / (pace_parts["d_m"] / 1000)).reindex(out.index)
    return pd.DataFrame(
        {
            "week_start": out.index,
            "runs": out["runs"].astype(int).to_numpy(),
            "distance_km": (out["distance_m"] / 1000).round(2).to_numpy(),
            "duration_h": (out["duration_s"] / 3600).round(2).to_numpy(),
            "pace_s_per_km": pace.to_numpy(),
        }
    )


def run_history(activities: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """One row per run in range, newest first. Pace is NaN when distance is unknown."""
    runs = _in_range(activities[activities["family"] == "run"], start, end)
    distance_km = runs["distance_m"] / 1000
    pace = runs["duration_s"] / distance_km.where(distance_km > 0)
    return (
        pd.DataFrame(
            {
                "date": runs["date"],
                "start_utc": runs["start_utc"],
                "name": runs["name"],
                "distance_km": distance_km.round(2),
                "duration_min": (runs["duration_s"] / 60).round(1),
                "pace_s_per_km": pace,
                "avg_hr": runs["avg_hr"],
                "elevation_gain_m": runs["elevation_gain_m"],
            }
        )
        .sort_values("start_utc", ascending=False)
        .reset_index(drop=True)
    )


def pace_trend(
    activities: pd.DataFrame, start: date, end: date, window_days: int = 28
) -> pd.DataFrame:
    """Each run's pace plus a rolling median over the previous `window_days` (min. 3 runs).

    The rolling median is computed over all history, then clipped to the range, so the first
    weeks of a range still have a trend line.
    """
    runs = activities[(activities["family"] == "run") & (activities["distance_m"] > 0)]
    runs = runs.sort_values("start_utc")
    pace = (runs["duration_s"] / (runs["distance_m"] / 1000)).to_numpy()
    series = pd.Series(pace, index=pd.DatetimeIndex(runs["start_utc"]))
    rolling = series.rolling(f"{window_days}D", min_periods=3).median()
    out = pd.DataFrame(
        {
            "date": runs["date"].to_numpy(),
            "pace_s_per_km": pace,
            "rolling_median_s_per_km": rolling.to_numpy(),
            "distance_km": (runs["distance_m"] / 1000).round(2).to_numpy(),
            "name": runs["name"].to_numpy(),
        }
    )
    return _in_range(out, start, end).reset_index(drop=True)


def weekly_training_time(activities: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Hours per week by family (run / strength / other); long format."""
    acts = _in_range(activities, start, end).copy()
    acts["week_start"] = week_start(acts["date"])
    pivot = (
        acts.pivot_table(
            index="week_start", columns="family", values="duration_s", aggfunc="sum", fill_value=0
        )
        .reindex(_week_index(start, end), fill_value=0)
        .rename_axis("week_start")
    )
    for family in ("run", "strength", "other"):
        if family not in pivot.columns:
            pivot[family] = 0
    long = (
        pivot[["run", "strength", "other"]]
        .reset_index()
        .melt(id_vars="week_start", var_name="family", value_name="duration_s")
    )
    long["hours"] = (long["duration_s"] / 3600).round(2)
    return long


# --- strength --------------------------------------------------------------------------


def strength_weekly(
    sessions: pd.DataFrame, sets: pd.DataFrame, start: date, end: date
) -> pd.DataFrame:
    """Per week: sessions and working sets (non-warm-up sets)."""
    sess = _in_range(sessions, start, end).copy()
    sess["week_start"] = week_start(sess["date"])
    work = _in_range(sets[~sets["is_warmup"]], start, end).copy()
    work["week_start"] = week_start(work["date"])
    index = _week_index(start, end)
    return pd.DataFrame(
        {
            "week_start": index,
            "sessions": sess.groupby("week_start")["session_id"]
            .nunique()
            .reindex(index, fill_value=0)
            .to_numpy(),
            "working_sets": work.groupby("week_start")
            .size()
            .reindex(index, fill_value=0)
            .to_numpy(),
        }
    )


def muscle_group_weekly(sets: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Working sets per muscle group (rows) per week (columns). Groups seen in any data are kept."""
    groups = sorted(sets["muscle_group"].dropna().unique())
    work = _in_range(sets[~sets["is_warmup"]], start, end).copy()
    work["week_start"] = week_start(work["date"])
    return (
        work.pivot_table(
            index="muscle_group", columns="week_start", values="set_index", aggfunc="count"
        )
        .reindex(index=groups, columns=_week_index(start, end))
        .fillna(0)
        .astype(int)
    )


def muscle_group_average(sets: pd.DataFrame, end: date, weeks: int = 4) -> pd.DataFrame:
    """Average weekly working sets per muscle group over the last `weeks` full weeks to `end`."""
    start = end - timedelta(weeks=weeks) + timedelta(days=1)
    pivot = muscle_group_weekly(sets, start, end)
    if pivot.empty:
        return pd.DataFrame({"muscle_group": [], "avg_weekly_sets": []})
    avg = pivot.sum(axis=1) / weeks
    return (
        avg.rename("avg_weekly_sets")
        .round(1)
        .rename_axis("muscle_group")
        .reset_index()
        .sort_values("avg_weekly_sets", ascending=True)
        .reset_index(drop=True)
    )


# --- recovery --------------------------------------------------------------------------


def recovery_daily(wellness: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Daily sleep/HRV/RHR with 7-day means and an individual HRV baseline band.

    HRV is analysed as ln(rMSSD): 7-day rolling mean (min. 4 values) compared with a 60-day
    baseline mean ± 1 SD (min. 14 values), converted back to ms for display.
    Days without a measurement stay NaN; rolling windows count calendar days.
    """
    if wellness.empty:
        cols = [
            "date",
            "sleep_h",
            "hrv",
            "hrv_7d",
            "hrv_low",
            "hrv_high",
            "hrv_status",
            "resting_hr",
            "rhr_7d",
            "rhr_base",
            "sleep_7d",
            "sleep_score",
            "stress_avg",
        ]
        return pd.DataFrame(columns=cols)
    daily = (
        wellness.groupby("date")
        .first()  # one row per day (single source today; multi-source precedence comes later)
        .reindex(pd.date_range(wellness["date"].min(), wellness["date"].max(), freq="D"))
    )
    ln_hrv = np.log(daily["hrv_rmssd_ms"])
    hrv_7d_ln = ln_hrv.rolling(7, min_periods=4).mean()
    base_mean = ln_hrv.rolling(60, min_periods=14).mean()
    base_sd = ln_hrv.rolling(60, min_periods=14).std()
    sleep_h = daily["sleep_duration_s"] / 3600
    out = pd.DataFrame(
        {
            "date": daily.index,
            "sleep_h": sleep_h.round(2).to_numpy(),
            "sleep_7d": sleep_h.rolling(7, min_periods=4).mean().round(2).to_numpy(),
            "sleep_score": daily["sleep_score"].to_numpy(),
            "hrv": daily["hrv_rmssd_ms"].to_numpy(),
            "hrv_7d": np.exp(hrv_7d_ln).round(1).to_numpy(),
            "hrv_low": np.exp(base_mean - base_sd).round(1).to_numpy(),
            "hrv_high": np.exp(base_mean + base_sd).round(1).to_numpy(),
            "resting_hr": daily["resting_hr"].to_numpy(),
            "rhr_7d": daily["resting_hr"].rolling(7, min_periods=4).mean().round(1).to_numpy(),
            # Personal resting-HR baseline: 60-day rolling median (needs >= 14 values).
            "rhr_base": daily["resting_hr"]
            .rolling(60, min_periods=14)
            .median()
            .round(1)
            .to_numpy(),
            "stress_avg": daily["stress_avg"].to_numpy(),
        }
    )
    out["hrv_status"] = np.select(
        [
            out["hrv_7d"].isna() | out["hrv_low"].isna(),
            out["hrv_7d"] < out["hrv_low"],
            out["hrv_7d"] > out["hrv_high"],
        ],
        ["insufficient data", "below normal range", "above normal range"],
        default="within normal range",
    )
    return _in_range(out, start, end).reset_index(drop=True)


# --- plan vs. completed ----------------------------------------------------------------


def plan_status(
    planned: pd.DataFrame, activities: pd.DataFrame, start: date, end: date, as_of: date
) -> pd.DataFrame:
    """Match planned workouts to activities on (local date, family).

    Each activity completes at most one planned workout of the same family on the same day.
    Status: completed | missed (past, not done) | today | upcoming | unplanned (done, not planned).
    """
    plans = _in_range(planned, start, end).copy()
    plans["family"] = np.where(plans["workout_type"].str.startswith("strength"), "strength", "run")
    plans["rank"] = plans.sort_values("id").groupby(["date", "family"]).cumcount()

    acts = _in_range(activities[activities["family"].isin(["run", "strength"])], start, end).copy()
    acts = acts.sort_values("start_utc")
    acts["rank"] = acts.groupby(["date", "family"]).cumcount()
    acts = acts.rename(
        columns={
            "id": "activity_id",
            "duration_s": "actual_duration_s",
            "distance_m": "actual_distance_m",
        }
    )
    merged = plans.merge(
        acts[["date", "family", "rank", "activity_id", "actual_duration_s", "actual_distance_m"]],
        on=["date", "family", "rank"],
        how="outer",
    )
    today = pd.Timestamp(as_of)
    planned_mask = merged["workout_type"].notna()
    done_mask = merged["activity_id"].notna()
    merged["status"] = np.select(
        [
            planned_mask & done_mask,
            ~planned_mask & done_mask,
            planned_mask & (merged["date"] < today),
            planned_mask & (merged["date"] == today),
        ],
        ["completed", "unplanned", "missed", "today"],
        default="upcoming",
    )
    # An activity recorded after "today" (clock skew / time zones) still counts as done.
    columns = [
        "date",
        "family",
        "workout_type",
        "target_duration_s",
        "target_distance_m",
        "activity_id",
        "actual_duration_s",
        "actual_distance_m",
        "status",
    ]
    return merged[columns].sort_values(["date", "family"]).reset_index(drop=True)


def weekly_adherence(status: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Per week: planned-so-far (completed + missed), completed, missed, unplanned, adherence %."""
    df = status.copy()
    df["week_start"] = week_start(df["date"])
    counts = (
        df.pivot_table(index="week_start", columns="status", values="family", aggfunc="count")
        .reindex(_week_index(start, end))
        .fillna(0)
        .astype(int)
    )
    for col in STATUS_ORDER:
        if col not in counts.columns:
            counts[col] = 0
    due = counts["completed"] + counts["missed"]
    return pd.DataFrame(
        {
            "week_start": counts.index,
            "planned_due": due.to_numpy(),
            "completed": counts["completed"].to_numpy(),
            "missed": counts["missed"].to_numpy(),
            "unplanned": counts["unplanned"].to_numpy(),
            "upcoming": (counts["upcoming"] + counts["today"]).to_numpy(),
            "adherence_pct": (100 * counts["completed"] / due.where(due > 0)).round(0).to_numpy(),
        }
    )


# --- overview --------------------------------------------------------------------------


@dataclass(frozen=True)
class PeriodComparison:
    """A value for the last 7 days and the weekly average of the 4 weeks before (or None)."""

    last_7d: float | None
    prior_weekly_avg: float | None

    @property
    def delta(self) -> float | None:
        if self.last_7d is None or self.prior_weekly_avg is None:
            return None
        return self.last_7d - self.prior_weekly_avg


@dataclass(frozen=True)
class Overview:
    run_km: PeriodComparison
    runs: PeriodComparison
    strength_sessions: PeriodComparison
    training_hours: PeriodComparison
    adherence_pct_7d: float | None
    sleep_h_7d: float | None
    hrv_7d: float | None
    hrv_status: str
    rhr_7d: float | None


def _compare(
    values: pd.Series, dates: pd.Series, as_of: date, data_start: date | None
) -> PeriodComparison:
    """Sum over the last 7 days vs. weekly average of the 28 days before.

    Returns None for a period that is not fully covered by the data (nothing is invented).
    """
    end = pd.Timestamp(as_of) - pd.Timedelta(days=1)
    last_start = end - pd.Timedelta(days=6)
    prior_start = last_start - pd.Timedelta(days=28)
    covered_from = pd.Timestamp(data_start) if data_start else None

    def total(lo: pd.Timestamp, hi: pd.Timestamp) -> float | None:
        if covered_from is None or lo < covered_from:
            return None
        return float(values[(dates >= lo) & (dates <= hi)].sum())

    last = total(last_start, end)
    prior = total(prior_start, last_start - pd.Timedelta(days=1))
    return PeriodComparison(last, None if prior is None else prior / 4)


def overview(
    activities: pd.DataFrame,
    sessions: pd.DataFrame,
    recovery: pd.DataFrame,
    status: pd.DataFrame,
    as_of: date,
    data_start: date | None,
) -> Overview:
    runs = activities[activities["family"] == "run"]
    yesterday = pd.Timestamp(as_of) - pd.Timedelta(days=1)
    last7 = status[
        (status["date"] > yesterday - pd.Timedelta(days=7)) & (status["date"] <= yesterday)
    ]
    due = int(last7["status"].isin(["completed", "missed"]).sum())
    done = int((last7["status"] == "completed").sum())
    rec7 = recovery[
        (recovery["date"] > yesterday - pd.Timedelta(days=7)) & (recovery["date"] <= yesterday)
    ]
    latest = recovery[recovery["date"] <= yesterday].tail(1)

    def mean_or_none(series: pd.Series) -> float | None:
        clean = series.dropna()
        return round(float(clean.mean()), 2) if len(clean) >= 4 else None

    def latest_or_none(col: str) -> float | None:
        if latest.empty or pd.isna(latest[col].iloc[0]):
            return None
        return float(latest[col].iloc[0])

    return Overview(
        run_km=_compare(runs["distance_m"].fillna(0) / 1000, runs["date"], as_of, data_start),
        runs=_compare(pd.Series(1, index=runs.index), runs["date"], as_of, data_start),
        strength_sessions=_compare(
            pd.Series(1, index=sessions.index), sessions["date"], as_of, data_start
        ),
        training_hours=_compare(
            activities["duration_s"] / 3600, activities["date"], as_of, data_start
        ),
        adherence_pct_7d=round(100 * done / due) if due else None,
        sleep_h_7d=mean_or_none(rec7["sleep_h"]) if not rec7.empty else None,
        hrv_7d=latest_or_none("hrv_7d"),
        hrv_status=str(latest["hrv_status"].iloc[0]) if not latest.empty else "insufficient data",
        rhr_7d=latest_or_none("rhr_7d"),
    )


def upcoming(status: pd.DataFrame, as_of: date, days: int = 7) -> pd.DataFrame:
    """Planned workouts from `as_of` (inclusive) for `days` days that are not yet completed."""
    lo = pd.Timestamp(as_of)
    hi = lo + pd.Timedelta(days=days - 1)
    mask = (
        status["status"].isin(["today", "upcoming"])
        & (status["date"] >= lo)
        & (status["date"] <= hi)
    )
    return status[mask].reset_index(drop=True)
