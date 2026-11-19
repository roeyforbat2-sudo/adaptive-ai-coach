"""Synthetic, spec-shaped Garmin payloads and a fake API server (httpx.MockTransport).

Field names follow the official OpenAPI spec; all values are invented. No real data.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import httpx

FAKE_KEY = "fake-personal-api-key-0123456789"


def fake_jwt(exp: float) -> str:
    def enc(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp), 'sub': 'fake'})}.c2lnbmF0dXJl"


def activity(
    activity_id: int, day: date, type_key: str = "running", **overrides: Any
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "activityId": activity_id,
        "activityName": f"Fake {type_key}",
        "activityType": {"typeId": 1, "typeKey": type_key, "parentTypeId": 17},
        "startTimeGMT": f"{day.isoformat()} 04:30:00",
        "startTimeLocal": f"{day.isoformat()} 06:30:00",
        "duration": 2712.4,
        "distance": 8012.7,
        "elevationGain": 54.0,
        "averageHR": 143.0,
        "maxHR": 171.0,
        "activityTrainingLoad": 61.2,
        "aerobicTrainingEffect": 2.9,
        "anaerobicTrainingEffect": 0.4,
        "ownerFullName": "Should Not Be Stored",
        "startLatitude": 31.0,
        "startLongitude": 34.0,
    }
    raw.update(overrides)
    return raw


def sleep(day: date, seconds: int | None = 26_000, score: int | None = 81) -> dict[str, Any]:
    item: dict[str, Any] = {
        "calendarDate": day.isoformat(),
        "sleepTimeSeconds": seconds,
        "deepSleepSeconds": 5000,
        "userNote": "untrusted note",
    }
    if score is not None:
        item["sleepScores"] = {"overall": {"value": score, "qualifierKey": "GOOD"}}
    return item


def hrv(day: date, value: int | None = 58) -> dict[str, Any]:
    return {
        "calendarDate": day.isoformat(),
        "lastNightAvg": value,
        "weeklyAvg": 57,
        "status": "BALANCED",
        "baseline": {"balancedLow": 50, "balancedUpper": 65},
    }


def daily(day: date, resting: int | None = 49, stress: int | None = 27) -> dict[str, Any]:
    return {
        "calendarDate": day.isoformat(),
        "heartRate": {"restingValue": resting, "minValue": 45, "maxValue": 160},
        "bodyBattery": {"maxValue": 88, "minValue": 21},
        "stress": {"avgLevel": stress, "maxLevel": 90},
        "movement": {"steps": {"value": 10432, "goal": 8000}},
    }


class FakeGarmin:
    """A tiny fake Personal API. Records every request for assertions."""

    def __init__(self, start: date, days: int, *, token_exp: float = 4_000_000_000) -> None:
        self.days = [start + timedelta(days=i) for i in range(days)]
        self.activities = [activity(1000 + i, d) for i, d in enumerate(self.days)]
        self.token_exp = token_exp
        self.requests: list[httpx.Request] = []
        self.override: Callable[[httpx.Request], httpx.Response | None] | None = None

    def _in_range(
        self, items: list[dict[str, Any]], lo: str, hi: str, key: str = "calendarDate"
    ) -> list[dict[str, Any]]:
        return [i for i in items if lo <= i[key][:10] <= hi]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.override and (response := self.override(request)) is not None:
            return response
        path, q = request.url.path, request.url.params
        if path.endswith("/personal-api-key/token"):
            if request.headers.get("Authorization") != f"ApiKey {FAKE_KEY}":
                return httpx.Response(401)
            return httpx.Response(200, text=fake_jwt(self.token_exp))
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            return httpx.Response(401)
        if path.startswith("/activitylist-service/"):
            items = [
                a
                for a in self.activities
                if q["startDate"] <= a["startTimeGMT"][:10] <= q["endDate"]
            ]
            start, limit = int(q["start"]), int(q["limit"])
            return httpx.Response(200, json=items[start : start + limit])
        if path.startswith("/sleep-service/"):
            return httpx.Response(
                200,
                json=self._in_range([sleep(d) for d in self.days], q["startDate"], q["endDate"]),
            )
        if path.startswith("/hrv-service/hrv/daily/"):
            lo, hi = path.rsplit("/", 2)[-2:]
            items = self._in_range([hrv(d) for d in self.days], lo, hi)
            return (
                httpx.Response(200, json={"hrvSummaries": items, "userProfilePk": 1})
                if items
                else httpx.Response(204)
            )
        if path.startswith("/usersummary-service/v2/dailysummaries"):
            return httpx.Response(
                200,
                json={
                    "data": self._in_range(
                        [daily(d) for d in self.days], q["startDate"], q["endDate"]
                    )
                },
            )
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class FakeClock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
