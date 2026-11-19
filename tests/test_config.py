from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from coach.config import Settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.env == "development"
    assert settings.db_path == Path("data/coach.sqlite")
    assert settings.timezone == "UTC"
    assert settings.store_raw is False


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COACH_TIMEZONE", "Asia/Jerusalem")
    monkeypatch.setenv("COACH_LOG_LEVEL", "DEBUG")
    settings = Settings(_env_file=None)
    assert settings.timezone == "Asia/Jerusalem"
    assert settings.tz.key == "Asia/Jerusalem"
    assert settings.log_level == "DEBUG"


def test_env_file_is_read_and_unrelated_keys_ignored(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "COACH_TIMEZONE=Asia/Jerusalem\nGOOGLE_CALENDAR_READ_IDS=primary\n", encoding="utf-8"
    )
    assert Settings(_env_file=env).timezone == "Asia/Jerusalem"


def test_invalid_timezone_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        Settings(_env_file=None, timezone="Mars/Olympus_Mons")


def test_settings_are_immutable() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ValidationError):
        settings.timezone = "UTC"  # type: ignore[misc]
