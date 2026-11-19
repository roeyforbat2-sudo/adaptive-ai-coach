"""Display formatting. Missing values render as an em dash, never as 0."""

from __future__ import annotations

import math

from coach.domain.labels import WORKOUT_LABELS, workout_label

__all__ = [
    "MISSING",
    "STATUS_LABELS",
    "WORKOUT_LABELS",
    "duration",
    "number",
    "pace",
    "workout_label",
]

MISSING = "—"

STATUS_LABELS = {
    "completed": "✅ Completed",
    "missed": "❌ Missed",
    "today": "📍 Today",
    "upcoming": "🗓️ Upcoming",
    "unplanned": "➕ Unplanned",
}


HRV_STATUS_SHORT = {
    "within normal range": "Normal",
    "below normal range": "Below range",
    "above normal range": "Above range",
    "insufficient data": "Not enough data",
}


def hrv_status_short(status: str) -> str:
    return HRV_STATUS_SHORT.get(status, status)


def _missing(value: float | None) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def pace(seconds_per_km: float | None) -> str:
    if _missing(seconds_per_km):
        return MISSING
    assert seconds_per_km is not None
    total = round(seconds_per_km)
    return f"{total // 60}:{total % 60:02d} /km"


def duration(seconds: float | None) -> str:
    if _missing(seconds):
        return MISSING
    assert seconds is not None
    minutes = round(seconds / 60)
    hours, mins = divmod(minutes, 60)
    return f"{hours} h {mins:02d} min" if hours else f"{mins} min"


def number(value: float | None, unit: str = "", digits: int = 1) -> str:
    if _missing(value):
        return MISSING
    assert value is not None
    text = f"{value:.{digits}f}"
    return f"{text} {unit}".strip()
