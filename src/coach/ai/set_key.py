"""Store your Anthropic API key in Windows Credential Manager.

    python -m coach.ai.set_key            # prompt (input is hidden)
    python -m coach.ai.set_key --status   # is a key stored? (never shows it)
    python -m coach.ai.set_key --delete   # remove the stored key

Create a key at https://platform.claude.com/settings/keys . Run this in your own terminal;
never paste the key into chat, files or command arguments.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from coach import secrets

ITEM = secrets.ANTHROPIC_API_KEY


def main(argv: list[str] | None = None, *, prompt: object = getpass.getpass) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coach.ai.set_key",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true")
    group.add_argument("--delete", action="store_true")
    args = parser.parse_args(argv)

    where = f"Windows Credential Manager (service '{secrets.SERVICE}', item '{ITEM}')"
    if args.status:
        print(f"Anthropic key {'IS' if secrets.has_secret(ITEM) else 'is NOT'} stored in {where}.")
        return 0
    if args.delete:
        print("Anthropic key removed." if secrets.delete_secret(ITEM) else "No key was stored.")
        return 0
    if not sys.stdin.isatty():
        print("Refusing to read the key from a non-interactive input. Run this in a terminal.")
        return 2
    assert callable(prompt)
    key = str(prompt("Anthropic API key (input hidden): ")).strip()
    if not key.startswith("sk-ant-") or len(key) < 20 or any(c.isspace() for c in key):
        print(
            "That does not look like an Anthropic API key (should start with 'sk-ant-'). "
            "Nothing stored."
        )
        return 2
    secrets.set_secret(ITEM, key)
    print(f"Stored in {where}. The key will not be shown again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
