"""Minimal client for the official Garmin Connect Personal API.

Verified against the official OpenAPI spec ("Garmin Connect Personal API" 1.0.0, read from
developer.garmin.com/gc-developer-program/personal/swagger-docs on 2026-11-17) and the FAQ:
- Auth: POST {base}/personalapitoken-service/personal-api/personal-api-key/token with header
  `Authorization: ApiKey <key>`; the response body is a JWT (text). Tokens last 24 h; there
  are no refresh tokens, so a new one is requested with the key.
- Data calls: GET with `Authorization: Bearer <token>`.
- Limits: sleep summaries <= 31 days per request; daily summaries <= 1 year; activity search
  is paginated with start/limit (default 99). Rate limits are undocumented, so requests are
  paced and 429/5xx are retried with backoff.

Security: the key is read from the keyring only for the exchange; tokens stay in memory; only
official Garmin hosts are allowed; nothing secret is logged or put into exception messages.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://personal.connectapi.garmin.com"
ALLOWED_HOSTS = frozenset({"personal.connectapi.garmin.com", "personal.connectapi.garmin.cn"})
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"  # noqa: S105

ACTIVITIES_PATH = "/activitylist-service/activities/search/activities"
SLEEP_PATH = "/sleep-service/sleep/dailySleepSummariesByDate"
HRV_PATH = "/hrv-service/hrv/daily/{from_date}/{to_date}"
DAILY_SUMMARIES_PATH = "/usersummary-service/v2/dailysummaries"

MAX_SLEEP_DAYS = 31  # documented limit
CHUNK_DAYS = 31  # used for every ranged endpoint (HRV has no documented limit)
PAGE_SIZE = 99  # documented default/maximum page size for activity search
TOKEN_REFRESH_MARGIN_S = 3600  # renew if less than 1 h of validity is left
TOKEN_FALLBACK_LIFETIME_S = 23 * 3600  # if the JWT has no readable exp (FAQ: 24 h)


class GarminError(RuntimeError):
    """Base error. Messages never contain secrets or response bodies."""


class GarminAuthError(GarminError):
    """401/403: key expired, revoked, or not authorized. Not retried."""


class GarminRequestError(GarminError):
    pass


def _validate_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    if (
        parts.scheme != "https"
        or parts.hostname not in ALLOWED_HOSTS
        or parts.path not in ("", "/")
    ):
        raise ValueError(
            "Garmin base URL must be https://personal.connectapi.garmin.com (or .cn); "
            "refusing to send the API key anywhere else"
        )
    return f"https://{parts.hostname}"


def _jwt_expiry(token: str, now: float) -> float:
    """Read `exp` from the JWT payload (no signature check: only used for cache timing)."""
    try:
        payload = token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(padded))["exp"]
        return float(exp)
    except (IndexError, ValueError, KeyError, TypeError):
        return now + TOKEN_FALLBACK_LIFETIME_S


def date_chunks(start: date, end: date, days: int = CHUNK_DAYS) -> Iterator[tuple[date, date]]:
    """Inclusive [start, end] split into consecutive windows of at most `days` days."""
    if end < start:
        return
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


class GarminClient:
    def __init__(
        self,
        api_key_provider: Callable[[], str],
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 30.0,
        min_interval_s: float = 1.0,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._api_key_provider = api_key_provider
        self._base_url = _validate_base_url(base_url)
        self._http = httpx.Client(
            base_url=self._base_url,
            timeout=timeout_s,
            transport=transport,
            follow_redirects=False,  # never forward credentials to a redirect target
            headers={"User-Agent": "adaptive-ai-coach/0.1 (personal use)"},
        )
        self._min_interval_s = min_interval_s
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._last_request_at: float | None = None
        self.request_count = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GarminClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- auth -------------------------------------------------------------------------

    def _bearer(self) -> str:
        now = self._clock()
        if self._token and self._token_expires_at - now > TOKEN_REFRESH_MARGIN_S:
            return self._token
        api_key = self._api_key_provider()
        response = self._send(
            "POST", TOKEN_PATH, headers={"Authorization": f"ApiKey {api_key}", "Accept": "*/*"}
        )
        token = response.text.strip()
        if not token or token.count(".") != 2:
            raise GarminAuthError("token exchange returned an unexpected response")
        self._token = token
        self._token_expires_at = _jwt_expiry(token, now)
        log.info(
            "obtained Garmin access token (valid until %s)", time.ctime(self._token_expires_at)
        )
        return token

    # --- transport --------------------------------------------------------------------

    def _pace(self) -> None:
        if self._last_request_at is not None:
            wait = self._min_interval_s - (self._clock() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            self._pace()
            self.request_count += 1
            try:
                response = self._http.request(method, path, headers=headers, params=params)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise GarminRequestError(
                        f"{method} {path}: network error ({type(exc).__name__})"
                    ) from None
                self._sleep(2**attempt)
                continue
            status = response.status_code
            if status in (401, 403):
                self._token = None
                raise GarminAuthError(
                    f"{method} {path}: HTTP {status}. The Personal API key may be expired, "
                    "revoked, or not authorized. Create a new key in Garmin Connect and store "
                    "it with `python -m coach.integrations.garmin.set_key`."
                )
            if status == 429 or status >= 500:
                if attempt == self._max_retries:
                    raise GarminRequestError(
                        f"{method} {path}: HTTP {status} after {attempt + 1} attempts"
                    )
                retry_after = response.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else float(2 ** (attempt + 1))
                log.warning(
                    "Garmin %s %s returned %s; retrying in %.0f s", method, path, status, delay
                )
                self._sleep(min(delay, 120.0))
                continue
            if status >= 300:
                # Redirects are never followed (they could leak the Authorization header).
                raise GarminRequestError(f"{method} {path}: HTTP {status}")
            return response
        raise GarminRequestError(f"{method} {path}: retries exhausted")  # pragma: no cover

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self._bearer()}", "Accept": "application/json"}
        response = self._send("GET", path, headers=headers, params=params)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise GarminRequestError(f"GET {path}: response was not JSON") from None

    # --- endpoints --------------------------------------------------------------------

    def iter_activities(self, start: date, end: date) -> Iterator[dict[str, Any]]:
        """All activities whose LOCAL start date is in [start, end], oldest first."""
        offset = 0
        while True:
            page = self.get_json(
                ACTIVITIES_PATH,
                {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "start": offset,
                    "limit": PAGE_SIZE,
                    "sortOrder": "asc",
                },
            )
            items = page if isinstance(page, list) else []
            yield from (i for i in items if isinstance(i, dict))
            if len(items) < PAGE_SIZE:
                return
            offset += PAGE_SIZE

    def sleep_summaries(self, start: date, end: date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for lo, hi in date_chunks(start, end, MAX_SLEEP_DAYS):
            data = self.get_json(
                SLEEP_PATH, {"startDate": lo.isoformat(), "endDate": hi.isoformat()}
            )
            out.extend(i for i in (data if isinstance(data, list) else []) if isinstance(i, dict))
        return out

    def hrv_summaries(self, start: date, end: date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for lo, hi in date_chunks(start, end):
            data = self.get_json(HRV_PATH.format(from_date=lo.isoformat(), to_date=hi.isoformat()))
            items = data.get("hrvSummaries") if isinstance(data, dict) else None
            out.extend(i for i in (items or []) if isinstance(i, dict))
        return out

    def daily_summaries(self, start: date, end: date) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for lo, hi in date_chunks(start, end):
            data = self.get_json(
                DAILY_SUMMARIES_PATH, {"startDate": lo.isoformat(), "endDate": hi.isoformat()}
            )
            items = data.get("data") if isinstance(data, dict) else None
            out.extend(i for i in (items or []) if isinstance(i, dict))
        return out
