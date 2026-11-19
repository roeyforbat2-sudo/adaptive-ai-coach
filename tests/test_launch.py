"""The quiet launchers used by the Windows scheduled tasks (no real dashboard is started)."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from coach import launch


def test_port_check_sees_a_listener() -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        assert launch.port_in_use(port)
    assert not launch.port_in_use(port)


def test_dashboard_not_started_twice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    started: list[Any] = []
    monkeypatch.setattr(launch, "LOGS", tmp_path)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: started.append((a, k)))
    monkeypatch.setattr(launch, "port_in_use", lambda *_: True)
    assert "already running" in launch.start_dashboard()
    assert started == []
    monkeypatch.setattr(launch, "port_in_use", lambda *_: False)
    assert "started" in launch.start_dashboard()
    (args, kwargs) = started[0]
    assert args[0][1:4] == ["-m", "streamlit", "run"] and kwargs["cwd"] == launch.ROOT


def test_main_logs_to_file_and_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(launch, "LOGS", tmp_path)
    monkeypatch.setattr(launch, "start_dashboard", lambda: "Dashboard already running.")
    assert launch.main(["dashboard"]) == 0
    assert "already running" in (tmp_path / "dashboard-launch.log").read_text(encoding="utf-8")
    monkeypatch.setattr(launch, "post_weekly_review", lambda: 1 / 0)
    assert launch.main(["weekly-review"]) == 1
    assert "ZeroDivisionError" in (tmp_path / "weekly-review.log").read_text(encoding="utf-8")
    assert launch.main(["nope"]) == 2
