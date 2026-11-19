"""The test network guard itself: external addresses are blocked, loopback is allowed."""

from __future__ import annotations

import socket

import pytest


def test_external_connect_is_blocked() -> None:
    with socket.socket() as s, pytest.raises(RuntimeError, match="network access is disabled"):
        s.connect(("203.0.113.10", 443))  # TEST-NET-3, never routable


def test_create_connection_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="network access is disabled"):
        socket.create_connection(("example.com", 443))


def test_loopback_socketpair_works() -> None:
    a, b = socket.socketpair()
    a.sendall(b"ok")
    assert b.recv(2) == b"ok"
    a.close()
    b.close()
