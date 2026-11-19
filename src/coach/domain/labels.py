"""Human-readable labels for domain enums (shared by the coach and the UI)."""

from __future__ import annotations

WORKOUT_LABELS = {
    "easy": "Easy run",
    "long": "Long run",
    "tempo": "Tempo run",
    "intervals": "Intervals",
    "recovery": "Recovery run",
    "strength_full": "Gym session",
    "strength_upper": "Strength · upper + calves",
    "strength_lower": "Strength · lower body",
}


def workout_label(workout_type: object) -> str:
    if not isinstance(workout_type, str):
        return "Unplanned workout"
    return WORKOUT_LABELS.get(workout_type, workout_type.replace("_", " ").title())
