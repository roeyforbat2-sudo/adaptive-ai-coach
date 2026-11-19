"""Google Calendar read-only integration, fully offline (fake service, fake OAuth flow)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from coach import secrets
from coach.db.repositories import CalendarEventRepository, PlannedWorkoutRepository
from coach.domain import PlanSource, Sport, WorkoutType
from coach.integrations.gcal import auth, sync
from coach.integrations.gcal.classify import classify

TZ = ZoneInfo("Asia/Jerusalem")
TODAY = date(2026, 11, 17)
START = datetime.combine(TODAY - timedelta(days=7), time(0), tzinfo=TZ)
END = datetime.combine(TODAY + timedelta(days=22), time(0), tzinfo=TZ)


def ev(
    event_id: str, summary: str | None, day: date, hour: int = 6, minutes: int = 45, **extra: Any
) -> dict[str, Any]:
    s = datetime.combine(day, time(hour), tzinfo=TZ)
    item: dict[str, Any] = {
        "id": event_id,
        "etag": f'"{event_id}"',
        "status": "confirmed",
        "start": {"dateTime": s.isoformat()},
        "end": {"dateTime": (s + timedelta(minutes=minutes)).isoformat()},
    }
    if summary is not None:
        item["summary"] = summary
    item.update(extra)
    return item


class FakeService:
    """Mimics service.events().list(**kw).execute() with paging; records every call."""

    def __init__(self, pages: dict[str, list[list[dict[str, Any]]]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def events(self) -> FakeService:
        return self

    def list(self, **kwargs: Any) -> FakeService:
        self.calls.append(kwargs)
        self._kw = kwargs
        return self

    def execute(self) -> dict[str, Any]:
        pages = self.pages[self._kw["calendarId"]]
        index = int(self._kw["pageToken"] or 0)
        body: dict[str, Any] = {"items": pages[index]}
        if index + 1 < len(pages):
            body["nextPageToken"] = str(index + 1)
        return body


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Easy run 45'", (Sport.RUN, WorkoutType.EASY)),
        ("Tempo 3x10", (Sport.RUN, WorkoutType.TEMPO)),
        ("Track intervals", (Sport.RUN, WorkoutType.INTERVALS)),
        ("Long run - 18 km", (Sport.RUN, WorkoutType.LONG)),
        ("Gym - full body", (Sport.STRENGTH, WorkoutType.STRENGTH_FULL)),
        ("Leg day", (Sport.STRENGTH, WorkoutType.STRENGTH_LOWER)),
        ("Upper body gym", (Sport.STRENGTH, WorkoutType.STRENGTH_UPPER)),
        ("ריצה קלה", (Sport.RUN, WorkoutType.EASY)),
        ("אימון כוח", (Sport.STRENGTH, WorkoutType.STRENGTH_FULL)),
        ("מכון", (Sport.STRENGTH, WorkoutType.STRENGTH_FULL)),
        ("ריצה", (Sport.RUN, WorkoutType.EASY)),
        ("Brunch with Dana", None),  # 'run' inside a word must not match
        ("Dentist", None),
        (None, None),
    ],
)
def test_classify(title: str | None, expected: tuple[Sport, WorkoutType] | None) -> None:
    assert classify(title) == expected


def test_request_is_read_only_and_minimal() -> None:
    service = FakeService({"primary": [[ev("a", "Run", TODAY)]]})
    sync.fetch_events(service, "primary", START, END)
    (call,) = service.calls
    assert call["singleEvents"] is True and call["orderBy"] == "startTime"
    assert call["timeMin"].endswith("+02:00") and call["timeMax"].endswith("+02:00")
    for forbidden in (
        "description",
        "attendees",
        "location",
        "conferenceData",
        "creator",
        "organizer",
    ):
        assert forbidden not in call["fields"]
    assert auth.SCOPES == ["https://www.googleapis.com/auth/calendar.events.readonly"]


def test_pagination() -> None:
    service = FakeService({"primary": [[ev("a", "Run", TODAY)], [ev("b", "Gym", TODAY)]]})
    assert [e["id"] for e in sync.fetch_events(service, "primary", START, END)] == ["a", "b"]
    assert [c["pageToken"] for c in service.calls] == [None, "1"]


def test_sync_stores_busy_blocks_and_training_plans(conn: sqlite3.Connection) -> None:
    events = [
        ev("run1", "Tempo run", TODAY + timedelta(days=1), minutes=50),
        ev("gym1", "Gym", TODAY + timedelta(days=2), hour=18, minutes=60),
        ev("doc", "Doctor - private matter", TODAY + timedelta(days=1), hour=10),
        ev(
            "allday",
            "Run club trip",
            TODAY + timedelta(days=3),
            start={"date": "2026-11-20"},
            end={"date": "2026-11-21"},
        ),
        ev("gone", "Easy run", TODAY, status="cancelled"),
        ev("ooo", "Running late (OOO)", TODAY, eventType="outOfOffice"),
        ev("free", "Lunch", TODAY, transparency="transparent"),
    ]
    result = sync.sync_calendar(
        conn, FakeService({"primary": [events]}), ["primary"], START, END, TZ
    )
    assert (result.events, result.training, result.skipped) == (6, 3, 1)

    rows = {r["event_id"]: dict(r) for r in conn.execute("SELECT * FROM calendar_events")}
    assert (
        rows["doc"]["title"] is None and rows["doc"]["is_training"] == 0
    )  # private title not kept
    assert rows["ooo"]["is_training"] == 0  # out-of-office never counts as training
    assert rows["free"]["busy"] == 0
    assert rows["allday"]["all_day"] == 1

    plans = PlannedWorkoutRepository(conn).list_between(TODAY, TODAY + timedelta(days=21))
    by_type = {p.workout_type: p for p in plans}
    assert set(by_type) == {WorkoutType.TEMPO, WorkoutType.STRENGTH_FULL, WorkoutType.EASY}
    assert by_type[WorkoutType.TEMPO].target_duration_s == 50 * 60
    assert by_type[WorkoutType.EASY].target_duration_s is None  # all-day: no invented duration
    assert all(p.source is PlanSource.CALENDAR for p in plans)


def test_resync_removes_deleted_events_and_plans(conn: sqlite3.Connection) -> None:
    first = [
        ev("run1", "Easy run", TODAY + timedelta(days=1)),
        ev("gym1", "Gym", TODAY + timedelta(days=2)),
    ]
    sync.sync_calendar(conn, FakeService({"primary": [first]}), ["primary"], START, END, TZ)
    sync.sync_calendar(conn, FakeService({"primary": [first[:1]]}), ["primary"], START, END, TZ)
    assert CalendarEventRepository(conn).count() == 1
    plans = PlannedWorkoutRepository(conn).list_between(TODAY, TODAY + timedelta(days=30))
    assert [p.workout_type for p in plans] == [WorkoutType.EASY]


def test_plan_for_last_window_day_is_kept(conn: sqlite3.Connection) -> None:
    last_day = (END - timedelta(days=1)).date()
    events = [ev("late", "Easy run", last_day, hour=20)]
    sync.sync_calendar(conn, FakeService({"primary": [events]}), ["primary"], START, END, TZ)
    sync.sync_calendar(conn, FakeService({"primary": [events]}), ["primary"], START, END, TZ)
    assert len(PlannedWorkoutRepository(conn).list_between(last_day, last_day)) == 1


def test_titles_are_truncated_and_hashed_ids_used(conn: sqlite3.Connection) -> None:
    sync.sync_calendar(
        conn,
        FakeService({"primary": [[ev("x" * 300, "Run " + "y" * 500, TODAY)]]}),
        ["primary"],
        START,
        END,
        TZ,
    )
    (title,) = conn.execute("SELECT title FROM calendar_events").fetchone()
    assert len(title) == 200
    (plan,) = PlannedWorkoutRepository(conn).list_between(TODAY, TODAY)
    assert plan.source_id.startswith("gcal-") and "x" not in plan.source_id


# --- auth ------------------------------------------------------------------------------


def _client_file(tmp_path: Path, kind: str = "installed") -> Path:
    path = tmp_path / "client_secret.json"
    path.write_text(
        json.dumps(
            {
                kind: {
                    "client_id": "fake-id.apps.googleusercontent.com",
                    "client_secret": "fake-secret",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_client_file_must_be_desktop_app(tmp_path: Path) -> None:
    with pytest.raises(auth.GoogleSetupError, match="not found"):
        auth.load_client_config(tmp_path / "missing.json")
    with pytest.raises(auth.GoogleSetupError, match="Desktop app"):
        auth.load_client_config(_client_file(tmp_path, kind="web"))
    assert auth.load_client_config(_client_file(tmp_path))["installed"]["client_id"]


class _FakeCreds:
    def __init__(self, refresh_token: str | None, scopes: list[str]) -> None:
        self.refresh_token = refresh_token
        self.granted_scopes = scopes


class _FakeFlow:
    def __init__(self, creds: _FakeCreds) -> None:
        self.creds = creds
        self.kwargs: dict[str, Any] = {}

    def run_local_server(self, **kwargs: Any) -> _FakeCreds:
        self.kwargs = kwargs
        return self.creds


def test_connect_stores_only_refresh_token(tmp_path: Path) -> None:
    flow = _FakeFlow(_FakeCreds("fake-refresh-token", auth.SCOPES))
    requested: dict[str, Any] = {}

    def factory(path: str, scopes: list[str]) -> _FakeFlow:
        requested["scopes"] = scopes
        return flow

    auth.connect(_client_file(tmp_path), flow_factory=factory)
    assert requested["scopes"] == auth.SCOPES
    assert flow.kwargs["host"] == "localhost" and flow.kwargs["port"] == 0
    assert secrets.get_secret(auth.TOKEN_ITEM) == "fake-refresh-token"


def test_connect_rejects_broader_scopes(tmp_path: Path) -> None:
    flow = _FakeFlow(_FakeCreds("tok", [*auth.SCOPES, "https://www.googleapis.com/auth/calendar"]))
    with pytest.raises(auth.GoogleSetupError, match="unexpected scopes"):
        auth.connect(_client_file(tmp_path), flow_factory=lambda *_a, **_k: flow)
    assert not secrets.has_secret(auth.TOKEN_ITEM)


def test_credentials_built_from_keyring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coach.config import get_settings

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_FILE", str(_client_file(tmp_path)))
    get_settings.cache_clear()
    secrets.set_secret(auth.TOKEN_ITEM, "fake-refresh-token")
    creds = auth.credentials()
    assert creds.refresh_token == "fake-refresh-token"  # noqa: S105
    assert list(creds.scopes) == auth.SCOPES
    get_settings.cache_clear()


def test_sync_cli_requires_connection(tmp_path: Path) -> None:
    assert sync.main(["--db", str(tmp_path / "c.sqlite")]) == 2
    assert sync.main(["--past", "99", "--db", str(tmp_path / "c.sqlite")]) == 2


def test_status_and_disconnect(capsys: pytest.CaptureFixture[str]) -> None:
    secrets.set_secret(auth.TOKEN_ITEM, "fake-refresh-token")
    auth.main(["--status"])
    out = capsys.readouterr().out
    assert "IS stored" in out and "fake-refresh-token" not in out
    assert auth.main(["--disconnect"]) == 0
    assert not secrets.has_secret(auth.TOKEN_ITEM)


def test_utc_storage(conn: sqlite3.Connection) -> None:
    sync.sync_calendar(
        conn,
        FakeService({"primary": [[ev("a", "Run", TODAY, hour=6)]]}),
        ["primary"],
        START,
        END,
        TZ,
    )
    (start,) = conn.execute("SELECT start_utc FROM calendar_events").fetchone()
    assert start == datetime(2026, 11, 17, 4, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def test_write_grant_is_separate_from_read(tmp_path: Path) -> None:
    flow = _FakeFlow(_FakeCreds("fake-write-token", auth.WRITE_SCOPES))
    requested: dict[str, Any] = {}

    def factory(_path: str, scopes: list[str]) -> _FakeFlow:
        requested["scopes"] = scopes
        return flow

    auth.connect(_client_file(tmp_path), write=True, flow_factory=factory)
    assert requested["scopes"] == ["https://www.googleapis.com/auth/calendar.events"]
    assert secrets.get_secret(auth.WRITE_TOKEN_ITEM) == "fake-write-token"
    assert not secrets.has_secret(auth.TOKEN_ITEM)  # the read token is untouched


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Gym – Upper", WorkoutType.STRENGTH_UPPER),
        ("Gym – Legs", WorkoutType.STRENGTH_LOWER),
        ("Gym – Full body", WorkoutType.STRENGTH_FULL),
        ("Quality run", WorkoutType.INTERVALS),
        ("Test run", WorkoutType.INTERVALS),
        ("Easy run", WorkoutType.EASY),
        ("Long run", WorkoutType.LONG),
        ("Race – Tel Aviv Half Marathon", WorkoutType.LONG),
    ],
)
def test_app_written_titles_are_recognised(title: str, expected: WorkoutType) -> None:
    got = classify(title)
    assert got is not None and got[1] is expected
