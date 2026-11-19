"""Reading daily check-ins (pure): one per day, stored in the `feedback` table.

See services/checkin.py for saving. The coaching rules (workout analysis, morning brief,
weekly review) read check-ins only through these helpers. Pain and illness are also read
by engine.safety directly.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

HARD_SORENESS = 6  # 0-10: from here soreness changes the advice
HARD_FATIGUE = 7  # 0-10


def source_id(day: date) -> str:
    return f"checkin-{day.isoformat()}"


def for_day(feedback: pd.DataFrame | None, day: date) -> dict[str, Any] | None:
    """That day's check-in as a plain dict, or None."""
    if feedback is None or feedback.empty or "source_id" not in feedback:
        return None
    rows = feedback[feedback["source_id"] == source_id(day)]
    if rows.empty:
        return None
    r = rows.iloc[-1]

    def num(key: str) -> int | None:
        v = r.get(key)
        return None if v is None or pd.isna(v) else int(v)

    pain = r.get("pain_location")
    return {
        "rpe": num("rpe"),
        "soreness": num("soreness"),
        "fatigue": num("fatigue"),
        "pain": bool(r.get("pain_flag")),
        "pain_location": pain if isinstance(pain, str) and pain else None,
        "ill": bool(r.get("illness_flag")),
        "activity_id": num("activity_id"),
    }


def between(feedback: pd.DataFrame | None, start: date, end: date) -> list[dict[str, Any]]:
    """All check-ins from `start` to `end` (inclusive), oldest first, with their date."""
    out = []
    day = start
    while day <= end:
        c = for_day(feedback, day)
        if c is not None:
            out.append({**c, "date": day})
        day = date.fromordinal(day.toordinal() + 1)
    return out
