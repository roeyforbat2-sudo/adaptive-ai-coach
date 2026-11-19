"""The daily check-in: how the workout felt and how you feel (saved from the Today tab).

One check-in per day (source 'manual', source_id 'checkin-YYYY-MM-DD'), so saving again the
same day updates it. Stored in the existing `feedback` table, which the safety rules already
read: pain -> no intensity, illness -> rest (engine.safety). Free text is untrusted and is
never shown as HTML or sent anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from coach.checkins import source_id
from coach.db import connect, migrate
from coach.db.connection import DbTarget
from coach.db.repositories import FeedbackRepository
from coach.domain.models import Feedback, FeedbackSource

PAIN_AREAS = [
    "Calf",
    "Shin",
    "Knee",
    "Hamstring",
    "Hip",
    "Foot / ankle",
    "Back",
    "Shoulder",
    "Other",
]


@dataclass(frozen=True)
class CheckIn:
    day: date
    rpe: int | None  # 1-10, how hard the workout felt (None on a rest day)
    soreness: int  # 0-10
    fatigue: int  # 0-10, how tired you feel
    pain: str | None  # area, or None
    ill: bool
    activity_id: int | None = None
    note: str | None = None


def save(db_path: DbTarget, checkin: CheckIn) -> int:
    feedback = Feedback(
        date=checkin.day,
        source=FeedbackSource.MANUAL,
        source_id=source_id(checkin.day),
        activity_id=checkin.activity_id,
        rpe=checkin.rpe,
        soreness=checkin.soreness,
        fatigue=checkin.fatigue,
        pain_flag=checkin.pain is not None,
        pain_location=checkin.pain,
        illness_flag=checkin.ill,
        notes=checkin.note or None,
    )
    conn = connect(db_path)
    try:
        migrate(conn)
        return FeedbackRepository(conn).add(feedback)
    finally:
        conn.close()
