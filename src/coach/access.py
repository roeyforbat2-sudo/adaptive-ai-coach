"""Passcode protection for opening the dashboard from another device (e.g. your phone).

Only a salted scrypt hash of the passcode is stored, in Windows Credential Manager; the
passcode itself is never written anywhere. Requests from this PC (localhost) do not need it.
Requests that arrive through a proxy such as `tailscale serve` (a non-local Host header, or
forwarding headers) always do, and are refused outright when no passcode has been set.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from coach import secrets

ITEM = secrets.DASHBOARD_PASSCODE_HASH
MIN_LENGTH = 6
MAX_FAILURES = 5
LOCKOUT_S = 60.0

_N, _R, _P = 2**14, 8, 1  # scrypt cost (~16 MB, a few ms)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "tailscale-user-login", "forwarded")


def hash_passcode(passcode: str, *, salt: bytes | None = None) -> str:
    salt = salt or pysecrets.token_bytes(16)
    digest = hashlib.scrypt(passcode.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_passcode(passcode: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            passcode.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(digest_hex) // 2,
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def validate_new_passcode(passcode: str) -> str | None:
    """An error message, or None when the passcode is acceptable."""
    if len(passcode) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if passcode.strip() != passcode:
        return "No spaces at the start or end."
    if len(set(passcode)) < 3:
        return "Too simple; use a few different characters."
    return None


def passcode_is_set() -> bool:
    return secrets.has_secret(ITEM)


def set_passcode(passcode: str) -> None:
    error = validate_new_passcode(passcode)
    if error:
        raise ValueError(error)
    secrets.set_secret(ITEM, hash_passcode(passcode))


def is_remote(headers: Mapping[str, str]) -> bool:
    """True unless the request clearly came straight from this PC."""
    lower = {k.lower(): v for k, v in headers.items()}
    if any(h in lower for h in _PROXY_HEADERS):
        return True
    try:
        hostname = urlsplit("//" + lower.get("host", "")).hostname or ""
    except ValueError:
        return True
    return hostname not in _LOCAL_HOSTS


@dataclass
class Limiter:
    """Slows down guessing: after MAX_FAILURES wrong tries, wait LOCKOUT_S seconds."""

    failures: int = 0
    locked_until: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait_s(self, now: float | None = None) -> float:
        return max(0.0, self.locked_until - (now if now is not None else time.monotonic()))

    def check(self, passcode: str, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        with self._lock:
            if self.locked_until > now:
                return False
            try:
                ok = verify_passcode(passcode, secrets.get_secret(ITEM))
            except secrets.SecretNotFoundError:
                ok = False
            if ok:
                self.failures = 0
                return True
            self.failures += 1
            if self.failures >= MAX_FAILURES:
                self.failures = 0
                self.locked_until = now + LOCKOUT_S
            return False


# --- Cloud: Google Identity-Aware Proxy (IAP) ------------------------------------------------
#
# In the cloud, IAP (Google sign-in) sits in front of Cloud Run and only lets allowed Google
# accounts through. As a second, independent check the app verifies IAP's signed header on
# every session: signature (Google's IAP keys), issuer, audience, expiry, and that the email
# is on COACH_ALLOWED_EMAILS. Anything else is refused.

IAP_HEADER = "x-goog-iap-jwt-assertion"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
IAP_ISSUER = "https://cloud.google.com/iap"


def _verify_iap_jwt(token: str, audience: str) -> dict[str, object]:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    claims = id_token.verify_token(
        token, google_requests.Request(), audience=audience, certs_url=IAP_CERTS_URL
    )
    return dict(claims)


def iap_user(
    headers: Mapping[str, str],
    audience: str | None,
    allowed: set[str],
    verify: Callable[[str, str], Mapping[str, object]] = _verify_iap_jwt,
) -> str | None:
    """The verified, allowed user's email, or None (missing/invalid/expired/not allowed)."""
    if not audience or not allowed:
        return None  # misconfigured: fail closed
    lower = {k.lower(): v for k, v in headers.items()}
    token = lower.get(IAP_HEADER)
    if not token:
        return None
    try:
        claims = verify(token, audience)
    except Exception:  # bad signature, wrong audience, expired, keys unreachable: deny
        return None
    if claims.get("iss") != IAP_ISSUER:
        return None
    email = str(claims.get("email", "")).lower()
    return email if email in allowed else None
