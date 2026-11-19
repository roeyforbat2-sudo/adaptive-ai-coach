"""Runs the real Streamlit script headlessly (no server, no browser, no network)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from streamlit.testing.v1 import AppTest

from coach import access
from coach.config import get_settings
from coach.db import connect, migrate
from coach.synthetic import generate, seed_database

APP = str(Path(__file__).resolve().parents[2] / "src" / "coach" / "ui" / "app.py")


@pytest.fixture(autouse=True)
def _local_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # AppTest sends no HTTP headers; treat it as a request from this PC unless a test says not.
    monkeypatch.setattr(access, "is_remote", lambda _headers: False)


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def demo_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tz = ZoneInfo("Asia/Jerusalem")
    db = tmp_path / "demo.sqlite"
    conn = connect(db)
    migrate(conn)
    seed_database(
        conn, generate(70, end_date=datetime.now(tz).date() - timedelta(days=1), seed=3, tz=tz)
    )
    conn.close()
    monkeypatch.setenv("COACH_DB_PATH", str(db))
    monkeypatch.setenv("COACH_TIMEZONE", "Asia/Jerusalem")
    return db


def test_dashboard_renders_all_sections_with_demo_label(demo_db: Path) -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == [
        "🏠 Today",
        "📊 Overview",
        "🏃 Running",
        "🏋️ Strength",
        "💤 Recovery",
        "📅 Training Calendar",
    ]
    assert any("DEMO DATA" in w.value for w in at.warning)
    assert len(at.metric) >= 20
    # Free mode: no AI buttons and no chat input anywhere; the copyable summary is there.
    assert not any(b.label in ("Get today's advice", "Get feedback") for b in at.button)
    assert len(at.chat_input) == 0
    assert any("numbers-only summary" in c.value for c in at.code)
    page = " ".join(m.value for m in at.markdown)
    assert "ac-hero" in page and "Recent workouts" in page
    assert "ac-analysis" in page  # Coach's take on the latest workout


def test_period_selector_rerenders(demo_db: Path) -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.button_group[0].set_value("4 weeks").run()
    assert not at.exception, at.exception


def test_missing_database_shows_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COACH_DB_PATH", str(tmp_path / "nope.sqlite"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("coach.synthetic" in e.value for e in at.error)


def test_remote_visit_without_passcode_is_refused(
    demo_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access, "is_remote", lambda _headers: True)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("set_passcode" in e.value for e in at.error)
    assert len(at.tabs) == 0


def test_remote_visit_needs_the_passcode(demo_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "is_remote", lambda _headers: True)
    access.set_passcode("tlv2027")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert len(at.tabs) == 0
    at.text_input[0].input("wrong!!")
    at.button[0].click().run()
    assert any("Wrong passcode" in e.value for e in at.error) and len(at.tabs) == 0
    at.text_input[0].input("tlv2027")
    at.button[0].click().run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 6


def test_calendar_writer_needs_real_data(demo_db: Path) -> None:
    # With demo data the writer section is hidden: nothing can ever be written from it.
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "Write my plan to Google Calendar" not in page


def test_calendar_writer_shows_on_real_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "coach.sqlite"
    conn = connect(db)
    migrate(conn)
    conn.close()
    program_file = tmp_path / "program.json"
    from tests.test_program import PROGRAM

    program_file.write_text(json.dumps(PROGRAM), encoding="utf-8")
    monkeypatch.setenv("COACH_DB_PATH", str(db))
    monkeypatch.setenv("COACH_PROGRAM_FILE", str(program_file))
    monkeypatch.setenv("COACH_TIMEZONE", "Asia/Jerusalem")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    page = " ".join(m.value for m in at.markdown)
    assert "Write my plan to Google Calendar" in page
    # Not connected in tests: the section explains how, and offers no write button.
    assert any("gcal.auth" in i.value for i in at.info)
    assert not any("changes to my calendar" in b.label for b in at.button)


def test_daily_checkin_form_saves(demo_db: Path) -> None:
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    save = next(b for b in at.button if b.label == "Save check-in")
    at.slider[0].set_value(4)  # soreness
    save.click().run()
    assert not at.exception, at.exception
    conn = connect(demo_db, read_only=True)
    try:
        rows = conn.execute(
            "SELECT soreness, pain_flag FROM feedback WHERE source_id LIKE 'checkin-%'"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in rows] == [(4, 0)]
    page = " ".join(m.value for m in at.markdown)
    assert "Checked in today." in page


def test_cloud_mode_refuses_visitors_without_iap(
    demo_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COACH_AUTH_MODE", "iap")
    monkeypatch.setenv("COACH_IAP_AUDIENCE", "/projects/1/locations/x/services/coach")
    monkeypatch.setenv("COACH_ALLOWED_EMAILS", "me@example.com")
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    assert any("Access denied" in e.value for e in at.error)
    assert len(at.tabs) == 0
