"""Quiet launchers for Windows scheduled tasks (run with pythonw.exe: no console window).

    pythonw -m coach.launch dashboard       # start the dashboard if it isn't running
    pythonw -m coach.launch weekly-review   # post the Saturday review if it is due
    pythonw -m coach.launch morning-brief   # post today's 05:30 brief if it is due

Output goes to `logs/` in the project (git-ignored), redacted like all app logging. The
dashboard is started as a separate, window-less process on localhost:8501, and only when
nothing is listening there yet, so running it twice never starts a second copy.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
APP = ROOT / "src" / "coach" / "ui" / "app.py"
PORT = 8501
CREATE_NO_WINDOW = 0x08000000  # Windows: start the child without a console


def port_in_use(port: int = PORT, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _python() -> str:
    """The console interpreter next to us (python.exe, not pythonw.exe)."""
    exe = Path(sys.executable)
    console = exe.with_name("python.exe")
    return str(console if console.is_file() else exe)


def _now() -> str:
    return f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S}"


def start_dashboard() -> str:
    if port_in_use():
        return "Dashboard already running on localhost:8501."
    LOGS.mkdir(exist_ok=True)
    with (LOGS / "dashboard.log").open("a", encoding="utf-8") as log:
        log.write(f"\n--- started {_now()} ---\n")
        log.flush()
        # The child gets its own copy of the log handle; ours closes on leaving `with`.
        subprocess.Popen(  # noqa: S603 - fixed arguments, our own interpreter and script
            [_python(), "-m", "streamlit", "run", str(APP)],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    return "Dashboard started on http://localhost:8501."


def post_weekly_review() -> int:
    from coach import review

    return review.main(["--publish", "--if-due"])


def post_morning_brief() -> int:
    from coach import brief

    return brief.main(["--publish", "--if-due"])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in (["dashboard"], ["weekly-review"], ["morning-brief"]):
        print(__doc__)
        return 2
    LOGS.mkdir(exist_ok=True)
    name = {"dashboard": "dashboard-launch"}.get(args[0], args[0])
    with (LOGS / f"{name}.log").open("a", encoding="utf-8") as out:
        # pythonw has no stdout/stderr: send prints and logging to the log file instead.
        sys.stdout = sys.stderr = out
        print(f"\n--- {_now()} ---")
        try:
            if args == ["dashboard"]:
                print(start_dashboard())
                return 0
            if args == ["morning-brief"]:
                return post_morning_brief()
            return post_weekly_review()
        except Exception as exc:
            from coach.logging import redact

            print(redact(f"Failed: {type(exc).__name__}: {exc}"))
            return 1
        finally:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__


if __name__ == "__main__":
    sys.exit(main())
