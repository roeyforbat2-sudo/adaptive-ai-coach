from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from coach import secrets
from coach.db import connect, migrate
from coach.db.repositories import ActivityRepository, SyncStateRepository, WellnessRepository
from coach.integrations.garmin import set_key, sync
from coach.integrations.garmin.client import GarminAuthError, GarminClient
from coach.synthetic import generate, seed_database
from tests.conftest import MemoryKeyring
from tests.integrations.garmin.fakes import FAKE_KEY, FakeClock, FakeGarmin, activity

TZ = ZoneInfo("Asia/Jerusalem")
START, END = date(2026, 11, 1), date(2026, 11, 10)


def _client(fake: FakeGarmin) -> GarminClient:
    clock = FakeClock()
    return GarminClient(
        lambda: FAKE_KEY, transport=fake.transport(), sleep=clock.sleep, clock=clock.time
    )


@pytest.fixture
def fake() -> FakeGarmin:
    f = FakeGarmin(START, 10)
    f.activities.append(activity(9999, date(2026, 11, 5), "strength_training", distance=None))
    return f


def test_sync_writes_activities_wellness_and_strength(
    conn: sqlite3.Connection, fake: FakeGarmin
) -> None:
    result = sync.sync_garmin(conn, _client(fake), START, END, TZ)
    assert (result.activities, result.strength_sessions, result.wellness_days) == (11, 1, 10)
    assert ActivityRepository(conn).count() == 11
    assert len(WellnessRepository(conn).list_between(START, END)) == 10
    linked = conn.execute(
        "SELECT activity_id FROM strength_sessions WHERE source = 'garmin'"
    ).fetchone()
    assert linked[0] is not None
    state = SyncStateRepository(conn).get("garmin")
    assert (
        state is not None
        and state["cursor"] == END.isoformat()
        and state["consecutive_failures"] == 0
    )
    (audit,) = conn.execute(
        "SELECT details_json FROM audit_log WHERE action = 'garmin_sync'"
    ).fetchall()
    assert json.loads(audit[0])["activities"] == 11


def test_sync_is_idempotent(conn: sqlite3.Connection, fake: FakeGarmin) -> None:
    sync.sync_garmin(conn, _client(fake), START, END, TZ)
    sync.sync_garmin(conn, _client(fake), START, END, TZ)
    assert ActivityRepository(conn).count() == 11
    assert conn.execute("SELECT COUNT(*) FROM strength_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM daily_wellness").fetchone()[0] == 10


def test_failed_fetch_writes_nothing_and_records_redacted_error(
    conn: sqlite3.Connection, fake: FakeGarmin
) -> None:
    fake.override = lambda r: (
        httpx.Response(401) if r.url.path.startswith("/sleep-service") else None
    )
    with pytest.raises(GarminAuthError):
        sync.sync_garmin(conn, _client(fake), START, END, TZ)
    assert ActivityRepository(conn).count() == 0
    state = SyncStateRepository(conn).get("garmin")
    assert state is not None and state["consecutive_failures"] == 1 and "401" in state["last_error"]


def test_cli_requires_stored_key(tmp_path: Path) -> None:
    assert sync.main(["--days", "7", "--db", str(tmp_path / "coach.sqlite")]) == 2


def test_cli_refuses_demo_database(tmp_path: Path) -> None:
    secrets.set_secret(secrets.GARMIN_API_KEY, FAKE_KEY)
    db = tmp_path / "dev.sqlite"
    c = connect(db)
    migrate(c)
    seed_database(c, generate(14, end_date=END, seed=1, tz=TZ))
    c.close()
    assert sync.main(["--days", "7", "--db", str(db)]) == 2


def test_cli_rejects_bad_range(tmp_path: Path) -> None:
    secrets.set_secret(secrets.GARMIN_API_KEY, FAKE_KEY)
    assert sync.main(["--days", "0", "--db", str(tmp_path / "x.sqlite")]) == 2
    assert sync.main(["--days", "400", "--db", str(tmp_path / "x.sqlite")]) == 2


# --- key storage CLI ---------------------------------------------------------------------


def test_set_key_stores_without_printing(
    memory_keyring: MemoryKeyring,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert set_key.main([], prompt=lambda _msg: f"  {FAKE_KEY}  ") == 0
    assert memory_keyring.store[(secrets.SERVICE, secrets.GARMIN_API_KEY)] == FAKE_KEY
    out = capsys.readouterr().out
    assert FAKE_KEY not in out and "Credential Manager" in out


def test_set_key_rejects_implausible_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert set_key.main([], prompt=lambda _msg: "short") == 2
    assert set_key.main([], prompt=lambda _msg: "has a space in the middle of it") == 2
    assert not secrets.has_secret(secrets.GARMIN_API_KEY)


def test_set_key_refuses_non_interactive_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert set_key.main([], prompt=lambda _msg: FAKE_KEY) == 2
    assert not secrets.has_secret(secrets.GARMIN_API_KEY)


def test_set_key_status_and_delete(capsys: pytest.CaptureFixture[str]) -> None:
    assert set_key.main(["--status"]) == 0
    assert "is NOT stored" in capsys.readouterr().out
    secrets.set_secret(secrets.GARMIN_API_KEY, FAKE_KEY)
    set_key.main(["--status"])
    out = capsys.readouterr().out
    assert "IS stored" in out and FAKE_KEY not in out
    assert set_key.main(["--delete"]) == 0
    assert not secrets.has_secret(secrets.GARMIN_API_KEY)


def test_secrets_reject_empty_and_report_missing() -> None:
    with pytest.raises(ValueError, match="empty"):
        secrets.set_secret(secrets.GARMIN_API_KEY, "")
    with pytest.raises(secrets.SecretNotFoundError):
        secrets.get_secret(secrets.GARMIN_API_KEY)
    assert secrets.delete_secret(secrets.GARMIN_API_KEY) is False
