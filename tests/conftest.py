from __future__ import annotations

import socket
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from coach.db import connect, migrate
from coach.domain import Activity, Source, Sport

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# Read before _isolated_env clears COACH_* variables. Postgres tests run only when this
# points at a DISPOSABLE test database (its name must contain "test": tests wipe it).
PG_TEST_URL = __import__("os").environ.get("COACH_TEST_DATABASE_URL", "")
if not PG_TEST_URL and __import__("os").environ.get("COACH_PG_TESTS") == "1":
    # Read straight from Credential Manager (python -m coach.set_db_url --test), so the URL
    # never passes through a command line or the shell.
    PG_TEST_URL = (
        keyring.get_password("adaptive-ai-coach", "test-database-url")
        or keyring.get_password("adaptive-ai-coach", "database-url")
        or ""
    )
_real_connect = socket.socket.connect


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must never touch the network. Loopback is allowed (asyncio on Windows uses a
    loopback socket pair internally); every other address raises."""

    def guarded_connect(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOOPBACK:
            raise RuntimeError(f"network access is disabled in tests (tried {host!r})")
        _real_connect(self, address)  # type: ignore[arg-type]

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", blocked)


class MemoryKeyring(KeyringBackend):
    """In-memory keyring so tests never read or write the real Credential Manager."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise PasswordDeleteError("not found")
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def memory_keyring() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _isolated_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Never read the developer's real .env or COACH_* variables during tests."""
    import os

    for key in list(os.environ):
        if key.startswith(("COACH_", "GARMIN_", "GOOGLE_")):
            monkeypatch.delenv(key)
    # Backups default to the real OneDrive folder: never let a test write there.
    monkeypatch.delenv("ONEDRIVE", raising=False)
    monkeypatch.delenv("ONEDRIVECONSUMER", raising=False)
    monkeypatch.setenv("COACH_BACKUP_DIR", str(tmp_path_factory.mktemp("backups")))
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = connect(":memory:")
    migrate(connection)
    yield connection
    connection.close()


def make_activity(**overrides: object) -> Activity:
    data: dict[str, object] = {
        "source": Source.SYNTHETIC,
        "source_id": "syn-1",
        "sport": Sport.RUN,
        "start_utc": datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
        "duration_s": 2700,
        "distance_m": 8000.0,
        "avg_hr": 142,
        "max_hr": 165,
        "training_load": 48.0,
        "name": "Synthetic easy run",
    }
    data.update(overrides)
    return Activity.model_validate(data)


DAY = date(2026, 11, 1)


PG_SCHEMA = "coach_pytest"


@pytest.fixture
def pg_url() -> Iterator[str]:
    """A Postgres URL whose session works ONLY in a throw-away schema, or skip.

    The tests create the schema `coach_pytest`, point every connection at it
    (search_path), and drop just that schema afterwards. Real tables in `public`
    are never read or written, so this is safe even on the main database.
    """
    from urllib.parse import quote

    if not PG_TEST_URL:
        pytest.skip("run with COACH_PG_TESTS=1 (or COACH_TEST_DATABASE_URL) for Postgres tests")
    import psycopg

    def reset(create: bool) -> None:
        with psycopg.connect(PG_TEST_URL, sslmode="require", autocommit=True) as c:
            c.execute(f"DROP SCHEMA IF EXISTS {PG_SCHEMA} CASCADE")
            if create:
                c.execute(f"CREATE SCHEMA {PG_SCHEMA}")

    reset(create=True)
    sep = "&" if "?" in PG_TEST_URL else "?"
    yield f"{PG_TEST_URL}{sep}options={quote(f'-csearch_path={PG_SCHEMA}')}"
    reset(create=False)
