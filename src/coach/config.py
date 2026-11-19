"""Typed application settings loaded from environment variables and a git-ignored `.env`.

Secrets (OAuth tokens, API keys) are NOT modelled here yet; from Phase 5/6 they are read
from the OS keyring. Never log a Settings object's secret fields.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COACH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds settings for later phases / other services
        frozen=True,
    )

    env: Literal["development", "test", "production"] = "development"
    db_path: Path = Path("data/coach.sqlite")
    # Who may open the dashboard. "local": this PC (passcode for other devices); "iap": the
    # cloud, behind Google Identity-Aware Proxy, for the emails in allowed_emails only.
    auth_mode: Literal["local", "iap"] = "local"  # COACH_AUTH_MODE
    iap_audience: str | None = None  # COACH_IAP_AUDIENCE (the IAP JWT "aud" for this service)
    allowed_emails: str = ""  # COACH_ALLOWED_EMAILS, comma-separated
    # Cloud: a postgresql:// URL (from Secret Manager). When set, it replaces db_path.
    database_url: SecretStr | None = None  # COACH_DATABASE_URL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    timezone: str = "UTC"
    store_raw: bool = False
    program_file: Path = Path("data/program.json")  # COACH_PROGRAM_FILE (personal, git-ignored)
    # Paid AI calls are OFF unless explicitly enabled (COACH_AI_ENABLED=true). Default off at
    # the user's request: no button, chat or job may call the Anthropic API while this is false.
    ai_enabled: bool = False
    llm_model: str = "claude-sonnet-5-5"  # COACH_LLM_MODEL
    llm_max_tokens: int = Field(default=16_000, ge=1_000, le=64_000)
    google_oauth_client_file: Path = Field(
        default=Path("secrets/client_secret.json"),
        validation_alias=AliasChoices("GOOGLE_OAUTH_CLIENT_FILE", "COACH_GOOGLE_OAUTH_CLIENT_FILE"),
    )
    google_calendar_ids: str = Field(
        default="primary",
        validation_alias=AliasChoices("GOOGLE_CALENDAR_READ_IDS", "COACH_GOOGLE_CALENDAR_IDS"),
    )
    # Daily backups (coach.backup): None = <OneDrive>\Adaptive Coach backups, else backups/.
    backup_dir: Path | None = None  # COACH_BACKUP_DIR
    backup_keep: int = Field(default=30, ge=1, le=365)  # COACH_BACKUP_KEEP
    # How the app reaches Google Calendar: "oauth" = the athlete's sign-in tokens (PC);
    # "service_account" = the cloud app's own identity, on a calendar shared with it.
    google_calendar_auth: Literal["oauth", "service_account"] = (
        "oauth"  # COACH_GOOGLE_CALENDAR_AUTH
    )
    google_calendar_owner: str | None = None  # COACH_GOOGLE_CALENDAR_OWNER: the shared calendar
    # The calendar the plan is written to (only after approval in the app).
    google_calendar_write_id: str = "primary"  # COACH_GOOGLE_CALENDAR_WRITE_ID
    garmin_base_url: str = Field(
        default="https://personal.connectapi.garmin.com",
        validation_alias=AliasChoices("GARMIN_PERSONAL_API_BASE_URL", "COACH_GARMIN_BASE_URL"),
    )

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value

    @field_validator("garmin_base_url")
    @classmethod
    def _official_garmin_host(cls, value: str) -> str:
        from coach.integrations.garmin.client import _validate_base_url

        return _validate_base_url(value)

    @property
    def calendar_ids(self) -> list[str]:
        return [c.strip() for c in self.google_calendar_ids.split(",") if c.strip()]

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def allowed_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}

    @property
    def db_target(self) -> Path | str:
        """Where the data lives: the Postgres URL if configured, else the SQLite file."""
        return self.database_url.get_secret_value() if self.database_url else self.db_path

    @field_validator("database_url")
    @classmethod
    def _postgres_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith(
            ("postgresql://", "postgres://")
        ):
            raise ValueError("COACH_DATABASE_URL must be a postgresql:// URL")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
