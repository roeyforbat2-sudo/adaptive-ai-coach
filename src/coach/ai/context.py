"""Build the minimized, numbers-only context the AI coach sees.

Privacy and prompt-injection rules (docs/security.md):
- No free text from external sources: no activity names, calendar titles, notes, emails,
  locations or GPS. Only dates, sport types, numbers, and our own enum labels.
- Missing values are sent as null and called out in `data_notes`; nothing is estimated.
- Output is deterministic (stable key order) so prompt caching works across chat turns.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from coach.analytics.workout import workout_facts
from coach.domain import Goal
from coach.engine.safety import SafetyAssessment
from coach.services.dashboard import DashboardData, DashboardView


def _num(value: Any, digits: int = 1) -> float | None:
    return None if value is None or pd.isna(value) else round(float(value), digits)


def goals_payload(goals: list[Goal], today: date) -> list[dict[str, Any]]:
    return [
        {
            "goal": g.description,
            "kind": g.kind.value,
            "date": g.target_date.isoformat() if g.target_date else None,
            "days_to_go": (g.target_date - today).days if g.target_date else None,
            "distance_km": g.target_value if g.unit == "km" else None,
            "priority": g.priority,
        }
        for g in goals
    ]


def weekly_summary(data: DashboardData, end: date, weeks: int = 8) -> list[dict[str, Any]]:
    acts = data.activities
    out = []
    last_monday = pd.Timestamp(end) - pd.Timedelta(days=end.weekday())
    for i in range(weeks - 1, -1, -1):
        start = last_monday - pd.Timedelta(weeks=i)
        stop = start + pd.Timedelta(days=6)
        wk = acts[(acts["date"] >= start) & (acts["date"] <= min(stop, pd.Timestamp(end)))]
        runs = wk[wk["family"] == "run"]
        out.append(
            {
                "week_start": f"{start:%Y-%m-%d}",
                "complete": bool(stop <= pd.Timestamp(end)),
                "runs": len(runs),
                "run_km": _num(runs["distance_m"].fillna(0).sum() / 1000),
                "long_run_km": _num(runs["distance_m"].max() / 1000) if len(runs) else None,
                "run_hours": _num(runs["duration_s"].sum() / 3600),
                "strength_sessions": int((wk["family"] == "strength").sum()),
                "total_hours": _num(wk["duration_s"].sum() / 3600),
                "total_training_load": _num(wk["training_load"].sum(min_count=1)),
            }
        )
    return out


def build_context(
    data: DashboardData,
    view: DashboardView,
    goals: list[Goal],
    safety: SafetyAssessment,
    *,
    recent_days: int = 14,
) -> dict[str, Any]:
    today = data.as_of
    yesterday = today - timedelta(days=1)
    acts = data.activities
    recent = acts[acts["date"] > pd.Timestamp(today - timedelta(days=recent_days))]
    recent_facts = [
        workout_facts(int(i), acts, view.plan_status, view.recovery)
        for i in recent.sort_values("start_utc")["id"]
    ]

    rec = view.recovery
    rec_recent = rec[rec["date"] > pd.Timestamp(today - timedelta(days=recent_days))]
    latest = rec[rec["date"] <= pd.Timestamp(yesterday)].tail(1)
    missing_nights = int(rec_recent["hrv"].isna().sum())

    plan = view.plan_status
    upcoming = plan[
        (plan["date"] >= pd.Timestamp(today))
        & (plan["date"] < pd.Timestamp(today + timedelta(days=7)))
        & plan["workout_type"].notna()
    ]
    last4 = plan[
        (plan["date"] >= pd.Timestamp(today - timedelta(days=28)))
        & (plan["date"] < pd.Timestamp(today))
    ]

    notes = [
        "Strength sessions come from Garmin without exercise/set detail.",
        "Training load and training effect are Garmin's own values.",
    ]
    if missing_nights:
        notes.append(f"{missing_nights} of the last {recent_days} nights have no HRV/sleep data.")
    if data.is_demo:
        notes.append("This is SYNTHETIC demo data, not the athlete's real training.")

    return {
        "today": {"date": today.isoformat(), "weekday": f"{today:%A}"},
        "goals": goals_payload(goals, today),
        "readiness": {
            "status": safety.readiness,
            "rules": [g.as_dict() for g in safety.guardrails],
            "intensity_allowed_today": safety.intensity_allowed,
        },
        "recovery_now": None
        if latest.empty
        else {
            "hrv_7d_ms": _num(latest["hrv_7d"].iloc[0]),
            "hrv_normal_range_ms": [
                _num(latest["hrv_low"].iloc[0]),
                _num(latest["hrv_high"].iloc[0]),
            ],
            "hrv_status": latest["hrv_status"].iloc[0],
            "resting_hr_7d": _num(latest["rhr_7d"].iloc[0]),
            "resting_hr_baseline": _num(latest["rhr_base"].iloc[0]),
            "sleep_7d_h": _num(latest["sleep_7d"].iloc[0]),
        },
        "recovery_daily": [
            {
                "date": f"{r.date:%Y-%m-%d}",
                "sleep_h": _num(r.sleep_h),
                "hrv_ms": _num(r.hrv, 0),
                "resting_hr": _num(r.resting_hr, 0),
            }
            for r in rec_recent.itertuples()
        ],
        "recent_workouts": [f for f in recent_facts if f is not None],
        "weekly_summary": weekly_summary(data, yesterday),
        "plan_next_7_days": [
            {
                "date": f"{r.date:%Y-%m-%d}",
                "weekday": f"{r.date:%A}",
                "workout_type": r.workout_type,
                "target_min": _num(r.target_duration_s / 60, 0)
                if pd.notna(r.target_duration_s)
                else None,
                "status": r.status,
            }
            for r in upcoming.itertuples()
        ],
        "plan_adherence_last_4_weeks": {
            "completed": int((last4["status"] == "completed").sum()),
            "missed": int((last4["status"] == "missed").sum()),
            "unplanned_extra": int((last4["status"] == "unplanned").sum()),
        },
        "data_notes": notes,
    }


CHAT_SUMMARY_INTRO = (
    "I'm sharing a numbers-only summary from my training app (Garmin + my calendar plan). "
    "Please act as my running and strength coach: look at how my training is going, whether "
    "I'm on track for my goals, and what I should focus on next. The `readiness.rules` come "
    "from fixed safety rules in the app. Ask me if anything is unclear."
)


def summary_for_chat(context: dict[str, Any]) -> str:
    """Text the athlete can paste into their own Claude chat (no API call, no cost)."""
    import json

    body = json.dumps(context, ensure_ascii=False, indent=1)
    fence = "`" * 3
    return "\n".join([CHAT_SUMMARY_INTRO, "", fence + "json", body, fence, ""])
