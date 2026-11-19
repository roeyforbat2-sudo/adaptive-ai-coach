"""Store a Postgres connection URL in Windows Credential Manager (never in files or chat).

    python -m coach.set_db_url            # the main cloud database (Neon "coach")
    python -m coach.set_db_url --test     # the DISPOSABLE test database (Neon "coach_test")
    python -m coach.set_db_url --status   # which ones are stored (never shows them)

Paste the URL at the hidden prompt. Tools that need it read it from the keyring:
`coach.db.copy --to-keyring`, and `scripts/pg_tests.ps1` for the Postgres tests.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable
from urllib.parse import urlparse

from coach import secrets

MAIN = "database-url"
TEST = "test-database-url"


def check(url: str, *, test: bool) -> str | None:
    """Why this URL is not acceptable, or None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname:
        return "That is not a postgresql:// URL."
    if not parsed.password:
        return "The URL has no password in it; copy the full connection string."
    name = parsed.path.lstrip("/")
    if test and "test" not in name.lower():
        return f"The test database's name must contain 'test' (got {name!r}): tests wipe it."
    if not test and "test" in name.lower():
        return "That looks like the test database; use --test for it."
    return None


def main(argv: list[str] | None = None, *, prompt: Callable[[str], str] = getpass.getpass) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.set_db_url",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--test", action="store_true", help="the disposable test database")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    if args.status:
        for label, item in (("main", MAIN), ("test", TEST)):
            stored = secrets.has_secret(item)
            print(f"{label} database URL {'IS' if stored else 'is NOT'} stored.")
        return 0
    if not sys.stdin.isatty():
        print("Refusing to read a URL from a non-interactive input. Run this in a terminal.")
        return 2
    which = "TEST" if args.test else "MAIN"
    url = prompt(f"Paste the {which} Postgres connection URL (hidden): ").strip()
    problem = check(url, test=args.test)
    if problem:
        print(f"{problem} Nothing stored.")
        return 2
    secrets.set_secret(TEST if args.test else MAIN, url)
    host = urlparse(url).hostname
    print(f"Stored the {which.lower()} database URL ({host}) in Windows Credential Manager.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
