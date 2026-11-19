"""Passcode hashing, remote detection, lockout and the set_passcode CLI (in-memory keyring)."""

from __future__ import annotations

import pytest

from coach import access, secrets
from coach import set_passcode as cli


def test_hash_roundtrip_and_salt() -> None:
    a, b = access.hash_passcode("run-fast-7"), access.hash_passcode("run-fast-7")
    assert a != b and "run-fast-7" not in a
    assert access.verify_passcode("run-fast-7", a)
    assert not access.verify_passcode("run-fast-8", a)
    assert not access.verify_passcode("x", "garbage") and not access.verify_passcode("x", "md5$1$2")


@pytest.mark.parametrize(
    ("code", "ok"), [("12345", False), (" abcdef", False), ("aaaaaa", False), ("tlv2027", True)]
)
def test_new_passcode_rules(code: str, ok: bool) -> None:
    assert (access.validate_new_passcode(code) is None) is ok


@pytest.mark.parametrize(
    ("headers", "remote"),
    [
        ({"Host": "localhost:8501"}, False),
        ({"Host": "127.0.0.1:8501"}, False),
        ({"host": "[::1]:8501"}, False),
        ({"Host": "localhost:8501", "X-Forwarded-For": "100.64.0.2"}, True),
        ({"Host": "localhost:8501", "Tailscale-User-Login": "someone"}, True),
        ({"Host": "my-pc.tail1234.ts.net"}, True),
        ({"Host": "192.168.1.20:8501"}, True),
        ({}, True),
    ],
)
def test_is_remote(headers: dict[str, str], remote: bool) -> None:
    assert access.is_remote(headers) is remote


def test_limiter_locks_after_repeated_failures() -> None:
    access.set_passcode("tlv2027")
    lim = access.Limiter()
    for _ in range(access.MAX_FAILURES):
        assert not lim.check("wrong!", now=100.0)
    assert lim.wait_s(now=100.0) == access.LOCKOUT_S
    assert not lim.check("tlv2027", now=101.0)  # right code is refused while locked
    assert lim.check("tlv2027", now=100.0 + access.LOCKOUT_S + 1)


def test_limiter_without_passcode_refuses() -> None:
    assert not access.Limiter().check("anything")


def test_cli_sets_status_and_deletes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["tlv2027", "nope000"])
    assert cli.main([], prompt=lambda _: next(answers)) == 2
    assert not access.passcode_is_set()
    answers = iter(["tlv2027", "tlv2027"])
    assert cli.main([], prompt=lambda _: next(answers)) == 0
    stored = secrets.get_secret(access.ITEM)
    assert stored.startswith("scrypt$") and "tlv2027" not in stored
    assert cli.main(["--status"]) == 0 and "IS set" in capsys.readouterr().out
    assert cli.main(["--delete"]) == 0 and not access.passcode_is_set()
