"""Connect Google Calendar (OAuth installed-app flow).

    python -m coach.integrations.gcal.auth              # read-only access (syncing)
    python -m coach.integrations.gcal.auth --write      # also allow writing your plan
    python -m coach.integrations.gcal.auth --status     # which tokens are stored (never shown)
    python -m coach.integrations.gcal.auth --disconnect [--write]   # delete a stored token

Two separate grants, each with its own refresh token in Windows Credential Manager:
- read:  https://www.googleapis.com/auth/calendar.events.readonly (used by every sync);
- write: https://www.googleapis.com/auth/calendar.events (used ONLY by the calendar writer,
  after you approve a specific list of changes in the app).
The OAuth client file (Desktop app) stays in secrets/ which git ignores.
To revoke access entirely: https://myaccount.google.com/permissions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from coach import secrets
from coach.config import get_settings

SCOPES = ["https://www.googleapis.com/auth/calendar.events.readonly"]
TOKEN_ITEM = secrets.GOOGLE_CALENDAR_READONLY_TOKEN
WRITE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
WRITE_TOKEN_ITEM = secrets.GOOGLE_CALENDAR_WRITE_TOKEN


class GoogleSetupError(RuntimeError):
    pass


def load_client_config(path: Path) -> dict[str, Any]:
    """Validate the OAuth client JSON: must exist and be a *Desktop app* ("installed") client."""
    if not path.is_file():
        raise GoogleSetupError(
            f"OAuth client file not found at {path}. Download it from Google Cloud Console "
            "(APIs & Services > Credentials > your Desktop OAuth client > Download JSON)."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GoogleSetupError(f"{path} is not valid JSON") from exc
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, dict) or not installed.get("client_id"):
        raise GoogleSetupError(
            f"{path} is not a Desktop-app OAuth client. Create an OAuth client ID of type "
            "'Desktop app' (not 'Web application')."
        )
    return dict(data)


def credentials(*, write: bool = False) -> Any:
    """Refreshable credentials: read-only by default, the write grant only when asked.

    Cloud (`COACH_GOOGLE_CALENDAR_AUTH=service_account`): the app's own Google identity
    (Cloud Run's service account, no key and no refresh token). It sees only calendars the
    athlete shared with it, so access ends the moment the calendar is unshared.
    """
    from google.oauth2.credentials import Credentials

    settings = get_settings()
    if settings.google_calendar_auth == "service_account":
        import google.auth

        creds, _ = google.auth.default(scopes=WRITE_SCOPES if write else SCOPES)
        return creds
    installed = load_client_config(settings.google_oauth_client_file)["installed"]
    refresh_token = secrets.get_secret(WRITE_TOKEN_ITEM if write else TOKEN_ITEM)
    return Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=refresh_token,
        token_uri=installed.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=installed["client_id"],
        client_secret=installed.get("client_secret"),
        scopes=WRITE_SCOPES if write else SCOPES,
    )


def calendar_access(*, write: bool = False) -> bool:
    """Is calendar access set up? (A token in the keyring, or the cloud's own identity.)"""
    if get_settings().google_calendar_auth == "service_account":
        return True
    return secrets.has_secret(WRITE_TOKEN_ITEM if write else TOKEN_ITEM)


class _AliasedEvents:
    """events() wrapper: rewrites calendarId="primary" to the shared calendar's address."""

    def __init__(self, events: Any, owner: str) -> None:
        self._events, self._owner = events, owner

    def __getattr__(self, name: str) -> Any:
        method = getattr(self._events, name)

        def call(*args: Any, **kwargs: Any) -> Any:
            if kwargs.get("calendarId") == "primary":
                kwargs["calendarId"] = self._owner
            return method(*args, **kwargs)

        return call


class _AliasedService:
    def __init__(self, service: Any, owner: str) -> None:
        self._service, self._owner = service, owner

    def events(self) -> _AliasedEvents:
        return _AliasedEvents(self._service.events(), self._owner)


def calendar_service(*, write: bool = False) -> Any:
    """A Calendar API client with the right credentials for this environment.

    With the app's own identity, "primary" would mean the service account's own (empty)
    calendar, so it is mapped to the athlete's shared calendar (COACH_GOOGLE_CALENDAR_OWNER).
    The database keeps the label "primary", so local and cloud data stay comparable.
    """
    from googleapiclient.discovery import build

    settings = get_settings()
    shared = settings.google_calendar_auth == "service_account"
    if shared and not settings.google_calendar_owner:
        raise GoogleSetupError("COACH_GOOGLE_CALENDAR_OWNER (the shared calendar) is not set")
    service = build("calendar", "v3", credentials=credentials(write=write), cache_discovery=False)
    if shared:
        assert settings.google_calendar_owner is not None
        return _AliasedService(service, settings.google_calendar_owner)
    return service


def connect(client_file: Path, *, write: bool = False, flow_factory: Any = None) -> None:
    """Run the browser consent flow and store ONLY the refresh token in the keyring."""
    load_client_config(client_file)
    scopes, item = (WRITE_SCOPES, WRITE_TOKEN_ITEM) if write else (SCOPES, TOKEN_ITEM)
    what = "permission to add and update your training events" if write else "READ-ONLY access"
    if flow_factory is None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow_factory = InstalledAppFlow.from_client_secrets_file
    flow = flow_factory(str(client_file), scopes=scopes)
    creds = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=True,
        authorization_prompt_message=f"Opening your browser to grant calendar {what}...",
        success_message="Calendar access granted. You can close this tab.",
    )
    granted = set(getattr(creds, "granted_scopes", None) or getattr(creds, "scopes", None) or [])
    if granted and not granted <= set(scopes):
        raise GoogleSetupError(f"unexpected scopes granted: {sorted(granted - set(scopes))}")
    if not getattr(creds, "refresh_token", None):
        raise GoogleSetupError("Google did not return a refresh token; run the command again.")
    secrets.set_secret(item, creds.refresh_token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.integrations.gcal.auth",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--disconnect", action="store_true")
    parser.add_argument("--write", action="store_true", help="the write grant (plan -> calendar)")
    args = parser.parse_args(argv)
    item = WRITE_TOKEN_ITEM if args.write else TOKEN_ITEM
    settings = get_settings()

    if args.status:
        for label, token in (("read-only", TOKEN_ITEM), ("write", WRITE_TOKEN_ITEM)):
            stored = secrets.has_secret(token)
            print(f"Google Calendar {label} token {'IS' if stored else 'is NOT'} stored.")
        print(
            f"OAuth client file: {settings.google_oauth_client_file} "
            f"({'found' if settings.google_oauth_client_file.is_file() else 'missing'})"
        )
        return 0
    if args.disconnect:
        removed = secrets.delete_secret(item)
        print("Token removed." if removed else "No token was stored.")
        print("To revoke the grant at Google too: https://myaccount.google.com/permissions")
        return 0
    try:
        connect(settings.google_oauth_client_file, write=args.write)
    except GoogleSetupError as exc:
        print(f"Google Calendar setup problem: {exc}")
        return 2
    kind = "Write" if args.write else "Read-only"
    print(f"Connected. {kind} refresh token stored in Windows Credential Manager.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
