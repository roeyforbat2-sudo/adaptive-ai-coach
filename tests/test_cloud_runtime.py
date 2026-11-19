"""Cloud runtime: secrets from the environment, IAP login check, settings, jobs, backups."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from coach import access, backup, jobs, secrets
from coach.config import Settings

ALLOWED = {"me@example.com"}
GOOD = {"iss": access.IAP_ISSUER, "email": "Me@Example.com", "aud": "/projects/1/x"}


# --- secrets ----------------------------------------------------------------------------------


def test_environment_secret_wins_over_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    item = secrets.GARMIN_API_KEY
    assert secrets.env_name(item) == "COACH_SECRET_GARMIN_PERSONAL_API_KEY"
    assert not secrets.has_secret(item)
    secrets.set_secret(item, "from-keyring")
    assert secrets.get_secret(item) == "from-keyring"
    monkeypatch.setenv(secrets.env_name(item), "from-secret-manager")
    assert secrets.get_secret(item) == "from-secret-manager"
    monkeypatch.setenv(secrets.env_name(item), "   ")
    assert secrets.get_secret(item) == "from-keyring"  # blank env value is ignored


def test_missing_keyring_backend_counts_as_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    from keyring.errors import NoKeyringError

    def no_backend(*_: object) -> None:
        raise NoKeyringError("no backend in this container")

    monkeypatch.setattr(secrets.keyring, "get_password", no_backend)
    assert not secrets.has_secret(secrets.GOOGLE_CALENDAR_READONLY_TOKEN)
    with pytest.raises(secrets.SecretNotFoundError):
        secrets.get_secret(secrets.GOOGLE_CALENDAR_READONLY_TOKEN)


# --- IAP login check ---------------------------------------------------------------------------


def _verify(claims: Mapping[str, object]) -> Any:
    return lambda _token, _aud: claims


def test_iap_allows_only_verified_allowed_users() -> None:
    headers = {"X-Goog-IAP-JWT-Assertion": "token"}
    assert access.iap_user(headers, "/projects/1/x", ALLOWED, _verify(GOOD)) == "me@example.com"
    stranger = {**GOOD, "email": "someone@else.com"}
    assert access.iap_user(headers, "/projects/1/x", ALLOWED, _verify(stranger)) is None
    wrong_issuer = {**GOOD, "iss": "https://evil.example"}
    assert access.iap_user(headers, "/projects/1/x", ALLOWED, _verify(wrong_issuer)) is None


def test_iap_fails_closed() -> None:
    def broken(_token: str, _aud: str) -> Mapping[str, object]:
        raise ValueError("bad signature / expired / wrong audience")

    headers = {"x-goog-iap-jwt-assertion": "token"}
    assert access.iap_user({}, "/aud", ALLOWED, _verify(GOOD)) is None  # no header
    assert access.iap_user(headers, "/aud", ALLOWED, broken) is None
    assert access.iap_user(headers, None, ALLOWED, _verify(GOOD)) is None  # no audience set
    assert access.iap_user(headers, "/aud", set(), _verify(GOOD)) is None  # nobody allowed


# --- settings ----------------------------------------------------------------------------------


def test_cloud_settings() -> None:
    s = Settings(
        _env_file=None,
        database_url="postgresql://u:pw@host/db",
        auth_mode="iap",
        allowed_emails=" Me@Example.com , other@x.org ",
    )
    assert s.db_target == "postgresql://u:pw@host/db"
    assert s.allowed_email_set == {"me@example.com", "other@x.org"}
    assert "pw@" not in repr(s)
    with pytest.raises(ValueError, match="postgresql"):
        Settings(_env_file=None, database_url="sqlite:///x")
    assert Settings(_env_file=None).db_target == Path("data/coach.sqlite")


# --- jobs --------------------------------------------------------------------------------------


def test_jobs_dispatch(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(jobs.JOBS, "morning-brief", lambda: 0)
    assert jobs.main(["morning-brief"]) == 0
    assert jobs.main(["unknown"]) == 2

    def boom() -> int:
        raise RuntimeError("Authorization: Bearer abc.def.ghi")

    monkeypatch.setitem(jobs.JOBS, "nightly", boom)
    assert jobs.main(["nightly"]) == 1
    out = capsys.readouterr().out
    assert "nightly failed: RuntimeError" in out and "abc.def.ghi" not in out


def test_nightly_job_runs_sync_reminder_and_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from coach.services import daily_messages
    from coach.services import sync as live_sync
    from coach.services.sync import SyncOutcome

    monkeypatch.setattr(
        live_sync, "sync_garmin", lambda *a, **k: SyncOutcome("garmin", True, "3 activities")
    )
    monkeypatch.setattr(
        live_sync,
        "sync_calendar",
        lambda *a, **k: SyncOutcome("gcal", False, "Google Calendar is not connected."),
    )
    monkeypatch.setattr(daily_messages, "ensure_checkin_reminder", lambda *a, **k: None)
    monkeypatch.setattr(backup, "backup_if_due", lambda *a, **k: None)
    assert jobs.nightly() == 0  # "not connected" is not a failure
    out = capsys.readouterr().out
    assert "garmin: 3 activities" in out and "backup: not due" in out


# --- cloud backup ------------------------------------------------------------------------------


def test_cloud_backup_writes_export_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_export(_url: str, target: Path) -> dict[str, int]:
        target.write_text('{"format": 1, "tables": {}}', encoding="utf-8")
        return {"activities": 72}

    monkeypatch.setattr(backup, "_export_postgres", fake_export)
    program = tmp_path / "program.json"
    program.write_text("{}", encoding="utf-8")
    now = datetime(2026, 11, 18, 3, 0, tzinfo=ZoneInfo("Asia/Jerusalem"))
    made = backup.make_backup("postgresql://u:p@h/db", program, tmp_path / "b", now)
    assert made.path.name == "coach-2026-11-18_0300"
    assert sorted(p.name for p in made.path.iterdir()) == [
        "coach-export.json",
        "manifest.json",
        "program.json",
    ]
    assert backup.list_backups(tmp_path / "b")[0].path == made.path


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("connect to postgresql://coach:hunter2@ep-x.neon.tech/db failed", "hunter2"),
        ("host=ep-x user=coach password=hunter2 dbname=db", "hunter2"),
    ],
)
def test_redaction_covers_cloud_secrets(text: str, secret: str) -> None:
    from coach.logging import redact

    assert secret not in redact(text)


def test_set_db_url_checks_and_stores(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from coach import set_db_url as cli

    assert cli.check("mysql://x", test=False) == "That is not a postgresql:// URL."
    assert "no password" in (cli.check("postgresql://u@h/coach", test=False) or "")
    assert "must contain 'test'" in (cli.check("postgresql://u:p@h/coach", test=True) or "")
    assert "use --test" in (cli.check("postgresql://u:p@h/coach_test", test=False) or "")
    assert cli.check("postgresql://u:p@h/coach_test", test=True) is None
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert cli.main([], prompt=lambda _: "postgresql://u:pw@ep-x.neon.tech/coach") == 0
    out = capsys.readouterr().out
    assert "ep-x.neon.tech" in out and "pw" not in out
    assert secrets.get_secret(cli.MAIN) == "postgresql://u:pw@ep-x.neon.tech/coach"
    assert cli.main(["--status"]) == 0
    assert "main database URL IS stored" in capsys.readouterr().out


# --- calendar via the app's own identity (cloud) -----------------------------------------------


def test_service_account_calendar_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from coach.config import get_settings
    from coach.integrations.gcal import auth

    monkeypatch.setenv("COACH_GOOGLE_CALENDAR_AUTH", "service_account")
    monkeypatch.setenv("COACH_GOOGLE_CALENDAR_OWNER", "me@example.com")
    get_settings.cache_clear()
    try:
        # No tokens stored at all, yet access counts as set up.
        assert auth.calendar_access() and auth.calendar_access(write=True)
        asked: dict[str, Any] = {}

        def fake_default(scopes: list[str]) -> tuple[str, str]:
            asked["scopes"] = scopes
            return "app-identity", "proj"

        monkeypatch.setattr("google.auth.default", fake_default)
        assert auth.credentials(write=True) == "app-identity"
        assert asked["scopes"] == auth.WRITE_SCOPES

        calls: list[dict[str, Any]] = []

        class Events:
            def list(self, **kw: Any) -> str:
                calls.append(kw)
                return "ok"

        class Service:
            def events(self) -> Events:
                return Events()

        aliased = auth._AliasedService(Service(), "me@example.com")
        aliased.events().list(calendarId="primary", maxResults=5)
        aliased.events().list(calendarId="team@example.com")
        assert calls[0]["calendarId"] == "me@example.com" and calls[0]["maxResults"] == 5
        assert calls[1]["calendarId"] == "team@example.com"  # only "primary" is mapped
    finally:
        get_settings.cache_clear()


def test_service_account_mode_needs_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    from coach.config import get_settings
    from coach.integrations.gcal import auth

    monkeypatch.setenv("COACH_GOOGLE_CALENDAR_AUTH", "service_account")
    get_settings.cache_clear()
    monkeypatch.setattr("google.auth.default", lambda scopes: ("app-identity", "proj"))
    try:
        with pytest.raises(auth.GoogleSetupError, match="COACH_GOOGLE_CALENDAR_OWNER"):
            auth.calendar_service()
    finally:
        get_settings.cache_clear()
