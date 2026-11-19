"""Redaction tests. Every 'secret' below is fake and generated for the test."""

from __future__ import annotations

import io
import logging

import pytest

from coach.logging import REDACTED, redact, setup_logging

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.c2lnbmF0dXJlLWZha2U"


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        (f"Authorization: Bearer {FAKE_JWT}", FAKE_JWT),
        ("Authorization: ApiKey fakeKEY1234567890abcdef", "fakeKEY1234567890abcdef"),
        ('{"access_token": "abc.def-ghi", "expires_in": 3600}', "abc.def-ghi"),
        ("refresh_token=1//fakeRefreshToken123&x=1", "fakeRefreshToken123"),
        ("api_key = 'not-a-real-key-42'", "not-a-real-key-42"),
        ("GARMIN_PERSONAL_API_KEY=fakePersonalKey0001", "fakePersonalKey0001"),
        ("using sk-ant-api03-FAKEFAKEFAKE_123", "FAKEFAKEFAKE_123"),
        ("token ya29.a0FakeGoogleAccessToken", "a0FakeGoogleAccessToken"),
        ("GET /callback?code=4/0FakeAuthCode&state=xyz", "4/0FakeAuthCode"),
        ("invite from runner.friend@example.com", "runner.friend@example.com"),
    ],
)
def test_redact_masks_secrets(raw: str, leaked: str) -> None:
    out = redact(raw)
    assert leaked not in out
    assert REDACTED in out or "[EMAIL]" in out


@pytest.mark.parametrize(
    "benign",
    [
        "status code=200 after 3 retries",
        "basic training block, week 4",
        "token_type=Bearer",
        "easy run 8.0 km at 5:45/km, avg HR 142",
    ],
)
def test_redact_leaves_ordinary_text(benign: str) -> None:
    assert redact(benign) == benign


def test_logger_redacts_messages_args_and_tracebacks() -> None:
    stream = io.StringIO()
    setup_logging("DEBUG", stream=stream)
    log = logging.getLogger("coach.test")

    log.info("calling with header Authorization: Bearer %s", FAKE_JWT)
    try:
        raise RuntimeError(f"bad response, access_token={FAKE_JWT}")
    except RuntimeError:
        log.exception("sync failed")

    output = stream.getvalue()
    assert FAKE_JWT not in output
    assert output.count(REDACTED) >= 2


def test_other_handlers_receive_redacted_messages(caplog: pytest.LogCaptureFixture) -> None:
    setup_logging("DEBUG", stream=io.StringIO())
    with caplog.at_level(logging.INFO):
        logging.getLogger("coach.test").info("token exchange used ApiKey %s", "x" * 20)
    assert "x" * 20 not in caplog.text
    assert REDACTED in caplog.text


def test_setup_logging_is_idempotent() -> None:
    first = setup_logging("INFO", stream=io.StringIO())
    second = setup_logging("INFO", stream=io.StringIO())
    root = logging.getLogger()
    assert first not in root.handlers
    assert second in root.handlers
    assert logging.getLogger("httpx").level == logging.WARNING
