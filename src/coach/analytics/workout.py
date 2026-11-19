"""Deterministic facts about one workout, compared with the plan and recent history.

Pure functions; every value is computed from stored data or left as None. These facts are
what the coach (rules or AI) comments on, so the AI never has to compute numbers itself.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd


def _r(value: Any, digits: int = 1) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
        return None
    return round(float(value), digits)


def _pace(duration_s: Any, distance_m: Any) -> float | None:
    if pd.isna(duration_s) or pd.isna(distance_m) or not distance_m:
        return None
    return round(float(duration_s) / (float(distance_m) / 1000), 0)


# Quality-session hints in the athlete's own activity names (English + Hebrew), e.g.
# "איכות 6*400", "טסט 5 קמ", "tempo 3x10". Used locally only to choose the right comparison;
# the name itself never leaves this function (the facts carry only the enum hint).
QUALITY_PATTERN = re.compile(
    r"\d+\s*[*x×]\s*\d+|\b(intervals?|tempo|threshold|fartlek|test|race|repeats)\b"
    r"|איכות|אינטרוו|טמפו|טסט|מרוץ|תחרות|פרטלק",
    re.IGNORECASE,
)


def run_kind_hint(name: object) -> str | None:
    return "quality" if isinstance(name, str) and QUALITY_PATTERN.search(name[:200]) else None


def pace_str(seconds_per_km: float | None) -> str | None:
    if seconds_per_km is None:
        return None
    total = round(seconds_per_km)
    return f"{total // 60}:{total % 60:02d}/km"


def workout_facts(
    activity_id: int,
    activities: pd.DataFrame,
    plan_status: pd.DataFrame,
    recovery: pd.DataFrame,
    *,
    lookback_days: int = 28,
) -> dict[str, Any] | None:
    rows = activities[activities["id"] == activity_id]
    if rows.empty:
        return None
    a = rows.iloc[0]
    facts: dict[str, Any] = {
        "activity_id": int(a["id"]),
        "date": f"{a['date']:%Y-%m-%d}",
        "weekday": f"{a['date']:%A}",
        "sport": a["sport"],
        "family": a["family"],
        "duration_min": _r(a["duration_s"] / 60),
        "distance_km": _r(a["distance_m"] / 1000 if pd.notna(a["distance_m"]) else None, 2),
        "pace": pace_str(_pace(a["duration_s"], a["distance_m"])),
        "pace_s_per_km": _pace(a["duration_s"], a["distance_m"]),
        "avg_hr": _r(a["avg_hr"], 0),
        "max_hr": _r(a["max_hr"], 0),
        "training_load": _r(a["training_load"]),
        "aerobic_te": _r(a["aerobic_te"]),
        "anaerobic_te": _r(a["anaerobic_te"]),
        "session_hint": run_kind_hint(a.get("name")) if a["family"] == "run" else None,
    }

    match = plan_status[plan_status["activity_id"] == activity_id]
    if not match.empty and pd.notna(match.iloc[0]["workout_type"]):
        target = match.iloc[0]["target_duration_s"]
        facts["planned"] = {
            "workout_type": match.iloc[0]["workout_type"],
            "target_duration_min": _r(target / 60) if pd.notna(target) else None,
            "duration_vs_target_pct": _r(100 * a["duration_s"] / target, 0)
            if pd.notna(target) and target
            else None,
        }
    else:
        facts["planned"] = None

    # Same-family workouts in the previous `lookback_days` (excluding this one).
    start = a["date"] - pd.Timedelta(days=lookback_days)
    if a["family"] == "run":
        # Compare like with like: easy runs against easy runs, not against interval sessions.
        is_quality = activities["name"].map(run_kind_hint).eq("quality")
        same_kind = is_quality if facts["session_hint"] == "quality" else ~is_quality
    else:
        same_kind = pd.Series(True, index=activities.index)
    prior = activities[
        same_kind
        & (activities["family"] == a["family"])
        & (activities["date"] >= start)
        & (activities["date"] < a["date"])
    ]
    if len(prior) >= 3:
        paces = (prior["duration_s"] / (prior["distance_m"] / 1000)).where(prior["distance_m"] > 0)
        cmp: dict[str, Any] = {
            "n_sessions": len(prior),
            "median_duration_min": _r(prior["duration_s"].median() / 60),
            "median_avg_hr": _r(prior["avg_hr"].median(), 0),
            "median_training_load": _r(prior["training_load"].median()),
        }
        if a["family"] == "run":
            cmp["median_pace"] = pace_str(_r(paces.median(), 0))
            cmp["median_pace_s_per_km"] = _r(paces.median(), 0)
            cmp["median_distance_km"] = _r(prior["distance_m"].median() / 1000, 2)
            cmp["longest_km"] = _r(prior["distance_m"].max() / 1000, 2)
        facts["vs_last_28_days"] = cmp
    else:
        facts["vs_last_28_days"] = None  # not enough history to compare

    morning = recovery[recovery["date"] == a["date"]]
    if not morning.empty:
        m = morning.iloc[0]
        facts["recovery_that_morning"] = {
            "sleep_h": _r(m["sleep_h"]),
            "hrv_ms": _r(m["hrv"], 0),
            "hrv_status_7d": m["hrv_status"],
            "resting_hr": _r(m["resting_hr"], 0),
        }
    else:
        facts["recovery_that_morning"] = None
    return facts
