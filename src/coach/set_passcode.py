"""Set the passcode that protects the dashboard when you open it from your phone.

    python -m coach.set_passcode            # prompt twice (input is hidden)
    python -m coach.set_passcode --status   # is a passcode set? (never shows it)
    python -m coach.set_passcode --delete   # remove it (remote access is then refused)

Run it in your own terminal. Only a salted hash is stored, in Windows Credential Manager.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable

from coach import access, secrets


def main(argv: list[str] | None = None, *, prompt: Callable[[str], str] = getpass.getpass) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.set_passcode",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--delete", action="store_true")
    args = parser.parse_args(argv)

    if args.status:
        print(f"Dashboard passcode {'IS' if access.passcode_is_set() else 'is NOT'} set.")
        return 0
    if args.delete:
        removed = secrets.delete_secret(access.ITEM)
        print("Passcode removed." if removed else "No passcode was set.")
        return 0
    if not sys.stdin.isatty():
        print("Refusing to read a passcode from a non-interactive input. Run this in a terminal.")
        return 2
    first = prompt(f"New dashboard passcode (at least {access.MIN_LENGTH} characters, hidden): ")
    error = access.validate_new_passcode(first)
    if error:
        print(f"{error} Nothing stored.")
        return 2
    if prompt("Type it again: ") != first:
        print("The two entries do not match. Nothing stored.")
        return 2
    access.set_passcode(first)
    print("Passcode set. You'll be asked for it when you open the coach from your phone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
