"""Store your Garmin Connect Personal API key in Windows Credential Manager.

    python -m coach.integrations.garmin.set_key            # prompt (input is hidden)
    python -m coach.integrations.garmin.set_key --status   # is a key stored? (never shows it)
    python -m coach.integrations.garmin.set_key --delete   # remove the stored key

Run it in your own terminal. Never paste the key into chat, files, or command arguments.
Create or revoke keys at https://connect.garmin.com/app/settings/personalApiToken
"""

from __future__ import annotations

import argparse
import getpass
import sys

from coach import secrets

ITEM = secrets.GARMIN_API_KEY


def main(argv: list[str] | None = None, *, prompt: object = getpass.getpass) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.integrations.garmin.set_key",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--delete", action="store_true")
    args = parser.parse_args(argv)

    where = f"Windows Credential Manager (service '{secrets.SERVICE}', item '{ITEM}')"
    if args.status:
        print(f"Garmin key {'IS' if secrets.has_secret(ITEM) else 'is NOT'} stored in {where}.")
        return 0
    if args.delete:
        removed = secrets.delete_secret(ITEM)
        print("Garmin key removed." if removed else "No Garmin key was stored.")
        return 0

    if not sys.stdin.isatty():
        print("Refusing to read the key from a non-interactive input. Run this in a terminal.")
        return 2
    assert callable(prompt)
    key = str(prompt("Garmin Personal API key (input hidden): ")).strip()
    if len(key) < 16 or any(c.isspace() for c in key):
        print(
            "That does not look like a Personal API key (too short or contains spaces). "
            "Nothing stored."
        )
        return 2
    secrets.set_secret(ITEM, key)
    print(f"Stored in {where}. The key will not be shown again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
