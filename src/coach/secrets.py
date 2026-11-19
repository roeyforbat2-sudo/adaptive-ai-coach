"""Secret storage: the OS keyring locally, environment variables in the cloud.

Locally (Windows) secrets live in Credential Manager via `keyring`. In the cloud there is no
keyring: Cloud Run injects each secret from Google Secret Manager as an environment
variable named `COACH_SECRET_<ITEM>` (item upper-cased, `-` -> `_`, e.g.
`COACH_SECRET_GARMIN_PERSONAL_API_KEY`). An environment value takes precedence.

Secrets never go to files, `.env`, logs, the database, or CLI arguments. Values are only
returned to the code that needs them (e.g., the Garmin token exchange) and never printed.
"""

from __future__ import annotations

import os

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE = "adaptive-ai-coach"

GARMIN_API_KEY = "garmin-personal-api-key"
GOOGLE_CALENDAR_READONLY_TOKEN = "google-calendar-readonly-token"  # noqa: S105 - item name
GOOGLE_CALENDAR_WRITE_TOKEN = "google-calendar-write-token"  # noqa: S105 - item name
ANTHROPIC_API_KEY = "anthropic-api-key"
DASHBOARD_PASSCODE_HASH = "dashboard-passcode-hash"


class SecretNotFoundError(LookupError):
    pass


def env_name(item: str) -> str:
    return "COACH_SECRET_" + item.upper().replace("-", "_")


def _from_env(item: str) -> str | None:
    value = os.environ.get(env_name(item), "").strip()
    return value or None


def _from_keyring(item: str) -> str | None:
    try:
        return keyring.get_password(SERVICE, item) or None
    except KeyringError:  # no keyring backend (e.g. in a container): treat as not stored
        return None


def set_secret(item: str, value: str) -> None:
    if not value:
        raise ValueError("refusing to store an empty secret")
    keyring.set_password(SERVICE, item, value)


def get_secret(item: str) -> str:
    value = _from_env(item) or _from_keyring(item)
    if not value:
        raise SecretNotFoundError(f"no secret stored for {item!r} (service {SERVICE!r})")
    return value


def has_secret(item: str) -> bool:
    return bool(_from_env(item) or _from_keyring(item))


def delete_secret(item: str) -> bool:
    """Delete a stored secret from the keyring. Returns False if nothing was stored."""
    try:
        keyring.delete_password(SERVICE, item)
    except (PasswordDeleteError, KeyringError):
        return False
    return True
