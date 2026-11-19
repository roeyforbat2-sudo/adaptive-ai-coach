from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
import pytest

from coach.integrations.garmin.client import (
    PAGE_SIZE,
    GarminAuthError,
    GarminClient,
    GarminRequestError,
    date_chunks,
)
from tests.integrations.garmin.fakes import FAKE_KEY, FakeClock, FakeGarmin, activity, fake_jwt

START = date(2026, 10, 1)


def _client(fake: FakeGarmin, clock: FakeClock | None = None, **kw: Any) -> GarminClient:
    clock = clock or FakeClock()
    return GarminClient(
        lambda: FAKE_KEY, transport=fake.transport(), sleep=clock.sleep, clock=clock.time, **kw
    )


def test_token_exchange_uses_apikey_header_then_bearer() -> None:
    fake = FakeGarmin(START, 3)
    with _client(fake) as client:
        client.sleep_summaries(START, START)
    token_req, data_req = fake.requests
    assert token_req.method == "POST"
    assert token_req.url.path == "/personalapitoken-service/personal-api/personal-api-key/token"
    assert token_req.headers["Authorization"] == f"ApiKey {FAKE_KEY}"
    assert data_req.headers["Authorization"].startswith("Bearer ")
    assert FAKE_KEY not in data_req.headers["Authorization"]
    assert str(data_req.url).startswith("https://personal.connectapi.garmin.com/")


def test_token_is_reused_until_close_to_expiry() -> None:
    clock = FakeClock(now=1_000_000.0)
    fake = FakeGarmin(START, 3, token_exp=1_000_000.0 + 24 * 3600)
    with _client(fake, clock) as client:
        client.sleep_summaries(START, START)
        client.sleep_summaries(START, START)
        assert sum(r.method == "POST" for r in fake.requests) == 1
        clock.now += 23.5 * 3600  # less than 1 h of validity left -> exchange again
        client.sleep_summaries(START, START)
    assert sum(r.method == "POST" for r in fake.requests) == 2


def test_unreadable_jwt_falls_back_to_23h() -> None:
    fake = FakeGarmin(START, 1)
    fake.override = lambda r: httpx.Response(200, text="a.b.c") if r.method == "POST" else None
    clock = FakeClock()
    with _client(fake, clock) as client:
        client.sleep_summaries(START, START)
        clock.now += 21 * 3600
        client.sleep_summaries(START, START)
    assert sum(r.method == "POST" for r in fake.requests) == 1


def test_401_raises_auth_error_without_retry_or_secret_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeGarmin(START, 1)
    fake.override = lambda r: httpx.Response(401, text=f"bad key {FAKE_KEY}")
    with (
        caplog.at_level(logging.DEBUG),
        _client(fake) as client,
        pytest.raises(GarminAuthError) as exc,
    ):
        client.sleep_summaries(START, START)
    assert len(fake.requests) == 1
    assert FAKE_KEY not in str(exc.value)
    assert FAKE_KEY not in caplog.text
    assert "set_key" in str(exc.value)


def test_429_is_retried_honouring_retry_after() -> None:
    fake = FakeGarmin(START, 1)
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response | None:
        if request.method == "GET" and calls["n"] == 0:
            calls["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "7"})
        return None

    fake.override = flaky
    clock = FakeClock()
    with _client(fake, clock) as client:
        assert client.sleep_summaries(START, START)
    assert 7.0 in clock.sleeps


def test_persistent_5xx_gives_up() -> None:
    fake = FakeGarmin(START, 1)
    fake.override = lambda r: httpx.Response(503) if r.method == "GET" else None
    with _client(fake, max_retries=2) as client, pytest.raises(GarminRequestError, match="503"):
        client.sleep_summaries(START, START)
    assert sum(r.method == "GET" for r in fake.requests) == 3


def test_redirects_are_not_followed() -> None:
    fake = FakeGarmin(START, 1)
    fake.override = lambda r: (
        httpx.Response(302, headers={"Location": "https://evil.example/"})
        if r.method == "GET"
        else None
    )
    with _client(fake) as client, pytest.raises(GarminRequestError, match="302"):
        client.sleep_summaries(START, START)
    assert all(r.url.host == "personal.connectapi.garmin.com" for r in fake.requests)


def test_requests_are_paced() -> None:
    fake = FakeGarmin(START, 1)
    clock = FakeClock()
    with _client(fake, clock, min_interval_s=1.0) as client:
        client.sleep_summaries(START, START)
        client.sleep_summaries(START, START)
    assert clock.sleeps and all(s <= 1.0 for s in clock.sleeps)


@pytest.mark.parametrize(
    "url",
    [
        "http://personal.connectapi.garmin.com",
        "https://evil.example.com",
        "https://personal.connectapi.garmin.com.evil.com",
        "https://personal.connectapi.garmin.com/x",
    ],
)
def test_only_official_hosts_allowed(url: str) -> None:
    with pytest.raises(ValueError, match="refusing"):
        GarminClient(lambda: FAKE_KEY, base_url=url)


def test_activity_pagination() -> None:
    fake = FakeGarmin(START, 1)
    fake.activities = [activity(i, START) for i in range(PAGE_SIZE + 3)]
    with _client(fake) as client:
        got = list(client.iter_activities(START, START))
    assert len(got) == PAGE_SIZE + 3
    gets = [r for r in fake.requests if r.method == "GET"]
    assert [r.url.params["start"] for r in gets] == ["0", str(PAGE_SIZE)]
    assert gets[0].url.params["sortOrder"] == "asc"
    assert gets[0].url.params["startDate"] == START.isoformat()


def test_sleep_requests_respect_31_day_limit() -> None:
    fake = FakeGarmin(START, 40)
    with _client(fake) as client:
        items = client.sleep_summaries(START, date(2026, 11, 9))
    assert len(items) == 40
    windows = [
        (r.url.params["startDate"], r.url.params["endDate"])
        for r in fake.requests
        if r.method == "GET"
    ]
    assert windows == [("2026-10-01", "2026-10-31"), ("2026-11-01", "2026-11-09")]


def test_hrv_204_means_no_data() -> None:
    fake = FakeGarmin(START, 1)
    with _client(fake) as client:
        assert client.hrv_summaries(date(2025, 1, 1), date(2025, 1, 5)) == []


def test_date_chunks() -> None:
    assert list(date_chunks(date(2026, 1, 1), date(2026, 1, 3), 2)) == [
        (date(2026, 1, 1), date(2026, 1, 2)),
        (date(2026, 1, 3), date(2026, 1, 3)),
    ]
    assert list(date_chunks(date(2026, 1, 2), date(2026, 1, 1))) == []


def test_fake_jwt_roundtrip() -> None:
    assert fake_jwt(123).count(".") == 2
