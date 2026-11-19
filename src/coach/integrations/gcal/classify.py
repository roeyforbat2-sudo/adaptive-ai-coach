"""Recognise training sessions in calendar event titles (untrusted text).

Simple, transparent keyword rules (English + Hebrew). A title is only *matched*, never
interpreted or executed. Anything unrecognised is treated as a normal busy event.
"""

from __future__ import annotations

import re

from coach.domain.models import Sport, WorkoutType

# Order matters: the first matching rule wins.
_RULES: tuple[tuple[re.Pattern[str], Sport, WorkoutType], ...] = (
    # Hebrew gym titles, e.g. "מכון- UPPER", "מכון- רגליים", "מכון- FB".
    (
        re.compile(r"מכון.*\bupper\b|\bupper\b.*מכון", re.I),
        Sport.STRENGTH,
        WorkoutType.STRENGTH_UPPER,
    ),
    (
        re.compile(r"מכון.*(רגליים|\blegs?\b|\blower\b)", re.I),
        Sport.STRENGTH,
        WorkoutType.STRENGTH_LOWER,
    ),
    # Titles the app writes: "Gym – Upper", "Gym – Legs", "Quality run", "Test run", "Race – …".
    (re.compile(r"\bgym\b.*\bupper\b", re.I), Sport.STRENGTH, WorkoutType.STRENGTH_UPPER),
    (re.compile(r"\b(quality|test) run\b", re.I), Sport.RUN, WorkoutType.INTERVALS),
    (re.compile(r"^\s*race\b|\bhalf marathon\b|\bmarathon\b", re.I), Sport.RUN, WorkoutType.LONG),
    # Quality runs, e.g. "ריצה- איכות: 2 חימום+ 6x400", "Run 4x4 min".
    (
        re.compile(r"(ריצה|\brun\b).*(איכות|\d+\s*[x×*]\s*\d+)|איכות", re.I),
        Sport.RUN,
        WorkoutType.INTERVALS,
    ),
    (
        re.compile(r"\b(upper body|upper)\b.*\b(gym|strength|weights?|lift)", re.I),
        Sport.STRENGTH,
        WorkoutType.STRENGTH_UPPER,
    ),
    (
        re.compile(r"\b(lower body|legs?|leg day)\b", re.I),
        Sport.STRENGTH,
        WorkoutType.STRENGTH_LOWER,
    ),
    (
        re.compile(
            r"\b(gym|strength|weights?|lifting|lift|kettlebells?|crossfit)\b|מכון|חדר כושר|כוח",
            re.I,
        ),
        Sport.STRENGTH,
        WorkoutType.STRENGTH_FULL,
    ),
    (
        re.compile(r"\b(intervals?|repeats|vo2|track)\b|אינטרוול", re.I),
        Sport.RUN,
        WorkoutType.INTERVALS,
    ),
    (re.compile(r"\b(tempo|threshold)\b|טמפו", re.I), Sport.RUN, WorkoutType.TEMPO),
    (re.compile(r"\blong run\b|ריצה ארוכה|ארוכה", re.I), Sport.RUN, WorkoutType.LONG),
    (re.compile(r"\brecovery run\b|ריצת התאוששות", re.I), Sport.RUN, WorkoutType.RECOVERY),
    (re.compile(r"\b(run|running|jog|jogging|easy run)\b|ריצה", re.I), Sport.RUN, WorkoutType.EASY),
)


def classify(title: str | None) -> tuple[Sport, WorkoutType] | None:
    if not title:
        return None
    text = title[:200]
    for pattern, sport, workout_type in _RULES:
        if pattern.search(text):
            return sport, workout_type
    return None
