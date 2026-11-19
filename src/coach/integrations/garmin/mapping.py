"""Map Garmin Personal API payloads to domain models.

Field names follow the official OpenAPI spec (see client.py). Payloads are untrusted:
- Only the fields we use are read; everything else is ignored (never stored).
- Out-of-range or sentinel values (e.g., negative stress levels) become None. Nothing is
  estimated or filled in.
- Records that cannot be mapped are skipped and counted, never partially guessed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError

from coach.domain.models import Activity, DailyWellness, Source, Sport, StrengthSession

log = logging.getLogger(__name__)

# ActivityTypeDTO.typeKey values. The spec does not enumerate them; these are Garmin's
# standard keys. Anything else maps to OTHER (still counted in training time).
_RUN_KEYS = {
    "running": Sport.RUN,
    "street_running": Sport.RUN,
    "track_running": Sport.RUN,
    "indoor_running": Sport.TREADMILL,
    "treadmill_running": Sport.TREADMILL,
    "virtual_run": Sport.TREADMILL,
    "trail_running": Sport.TRAIL_RUN,
    "ultra_run": Sport.TRAIL_RUN,
    "obstacle_run": Sport.RUN,
}
_OTHER_KEYS = {
    "strength_training": Sport.STRENGTH,
    "cycling": Sport.BIKE,
    "road_biking": Sport.BIKE,
    "indoor_cycling": Sport.BIKE,
    "mountain_biking": Sport.BIKE,
    "gravel_cycling": Sport.BIKE,
    "virtual_ride": Sport.BIKE,
    "walking": Sport.WALK,
    "hiking": Sport.WALK,
    "casual_walking": Sport.WALK,
    "speed_walking": Sport.WALK,
}
SPORT_BY_TYPE_KEY = {**_RUN_KEYS, **_OTHER_KEYS}


def sport_for(type_key: object) -> Sport:
    return SPORT_BY_TYPE_KEY.get(str(type_key).lower(), Sport.OTHER) if type_key else Sport.OTHER


def _num(value: object, lo: float, hi: float) -> float | None:
    """A finite number inside [lo, hi], else None (never clamped or estimated)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if lo <= number <= hi else None


def _int(value: object, lo: int, hi: int) -> int | None:
    number = _num(value, lo, hi)
    return round(number) if number is not None else None


def _get(obj: object, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _parse_gmt(value: object) -> datetime | None:
    """'yyyy-MM-dd HH:mm:ss' in GMT (spec: startTimeGMT) -> aware UTC datetime."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip().replace(" ", "T")).replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


@dataclass
class MappingReport:
    mapped: int = 0
    skipped: int = 0
    unknown_type_keys: dict[str, int] = field(default_factory=dict)
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


def map_activity(raw: dict[str, Any], report: MappingReport | None = None) -> Activity | None:
    report = report or MappingReport()
    activity_id = raw.get("activityId")
    start = _parse_gmt(raw.get("startTimeGMT"))
    if start is None and isinstance(raw.get("beginTimestamp"), int):
        start = datetime.fromtimestamp(raw["beginTimestamp"] / 1000, tz=UTC)
    duration = _num(raw.get("duration"), 1, 7 * 86_400)
    if not isinstance(activity_id, int):
        report.skip("activity: no id")
        return None
    if start is None:
        report.skip("activity: no start time")
        return None
    if duration is None:
        report.skip("activity: no/zero duration")
        return None

    type_key = _get(raw, "activityType", "typeKey")
    sport = sport_for(type_key)
    if sport is Sport.OTHER and type_key:
        key = str(type_key)[:40]
        report.unknown_type_keys[key] = report.unknown_type_keys.get(key, 0) + 1

    avg_hr = _int(raw.get("averageHR"), 20, 250)
    max_hr = _int(raw.get("maxHR"), 20, 250)
    if avg_hr is not None and max_hr is not None and avg_hr > max_hr:
        avg_hr = max_hr = None  # inconsistent pair: keep neither rather than guess
    try:
        activity = Activity(
            source=Source.GARMIN,
            source_id=str(activity_id),
            sport=sport,
            start_utc=start,
            duration_s=round(duration),
            distance_m=_num(raw.get("distance"), 0, 1_000_000),
            elevation_gain_m=_num(raw.get("elevationGain"), 0, 20_000),
            avg_hr=avg_hr,
            max_hr=max_hr,
            training_load=_num(raw.get("activityTrainingLoad"), 0, 2_000),
            aerobic_te=_num(raw.get("aerobicTrainingEffect"), 0, 5),
            anaerobic_te=_num(raw.get("anaerobicTrainingEffect"), 0, 5),
            name=raw.get("activityName") if isinstance(raw.get("activityName"), str) else None,
        )
    except ValidationError:
        report.skip("activity: failed validation")
        return None
    report.mapped += 1
    return activity


def strength_session_for(activity: Activity, local_date: date) -> StrengthSession | None:
    """A strength activity becomes a session (sets are not mapped yet: Garmin only provides
    category-level `summarizedExerciseSets`; mapping categories to muscle groups comes later)."""
    if activity.sport is not Sport.STRENGTH:
        return None
    return StrengthSession(source=Source.GARMIN, source_id=activity.source_id, date=local_date)


def map_wellness(
    sleep: Iterable[dict[str, Any]],
    hrv: Iterable[dict[str, Any]],
    daily: Iterable[dict[str, Any]],
    report: MappingReport | None = None,
) -> list[DailyWellness]:
    """Merge the three daily endpoints into one DailyWellness per calendar date."""
    report = report or MappingReport()
    days: dict[date, dict[str, Any]] = {}

    for item in sleep:
        day = _parse_date(item.get("calendarDate"))
        if day is None:
            report.skip("sleep: no calendar date")
            continue
        row = days.setdefault(day, {})
        row["sleep_duration_s"] = _int(item.get("sleepTimeSeconds"), 1, 86_400)
        # sleepScores is a map of components; "overall" is read only if present.
        row["sleep_score"] = _int(_get(item, "sleepScores", "overall", "value"), 0, 100)

    for item in hrv:
        day = _parse_date(item.get("calendarDate"))
        if day is None:
            report.skip("hrv: no calendar date")
            continue
        days.setdefault(day, {})["hrv_rmssd_ms"] = _num(item.get("lastNightAvg"), 1, 300)

    for item in daily:
        day = _parse_date(item.get("calendarDate"))
        if day is None:
            report.skip("daily summary: no calendar date")
            continue
        row = days.setdefault(day, {})
        row["resting_hr"] = _int(_get(item, "heartRate", "restingValue"), 25, 150)
        high = _int(_get(item, "bodyBattery", "maxValue"), 0, 100)
        low = _int(_get(item, "bodyBattery", "minValue"), 0, 100)
        if high is not None and low is not None and low > high:
            high = low = None
        row["body_battery_high"], row["body_battery_low"] = high, low
        row["stress_avg"] = _int(_get(item, "stress", "avgLevel"), 0, 100)  # <0 = no data
        row["steps"] = _int(_get(item, "movement", "steps", "value"), 0, 200_000)

    out: list[DailyWellness] = []
    for day in sorted(days):
        values = {k: v for k, v in days[day].items() if v is not None}
        if not values:
            continue  # a date with nothing measured is not stored
        try:
            out.append(DailyWellness(source=Source.GARMIN, date=day, **values))
            report.mapped += 1
        except ValidationError:
            report.skip("wellness: failed validation")
    return out
