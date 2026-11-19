"""Logging setup with secret/PII redaction.

All application logging must go through handlers configured by `setup_logging`, which
redacts tokens, API keys, auth headers, OAuth query parameters and email addresses from
the fully formatted output (including tracebacks).
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TextIO

REDACTED = "[REDACTED]"

# Key *names* whose values get redacted (not secrets themselves).
_REDACT_KEY_NAMES = (
    "access_token|refresh_token|id_token|client_secret|consumer_secret|consumer_key|"
    "api_key|apikey|password|passwd|secret|token|oauth_token|oauth_signature|"
    "oauth_verifier|code_verifier|authorization_code"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Authorization header values: Bearer/Basic, and Garmin's "ApiKey <key>" scheme
    # (16+ chars so prose like "basic training" survives).
    (re.compile(r"(?i)\b(bearer|basic|apikey)\s+[A-Za-z0-9\-._~+/]{16,}=*"), rf"\1 {REDACTED}"),
    # After an explicit "Authorization:" header the value is always secret, whatever its length.
    (re.compile(r"(?i)(authorization:\s*(?:bearer|basic|apikey)?\s*)\S+"), rf"\1{REDACTED}"),
    # Database URLs and connection strings: postgresql://user:PASSWORD@host, password=...
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@"), rf"\1{REDACTED}@"),
    (re.compile(r"(?i)\b(password\s*=\s*)[^\s&]+"), rf"\1{REDACTED}"),
    # key=value / "key": "value" pairs for secret names, incl. prefixed env vars such as
    # GARMIN_PERSONAL_API_KEY=... (the [\w-]* prefix).
    (
        re.compile(
            rf"""(?i)(["']?[\w\-]*(?:{_REDACT_KEY_NAMES})\b["']?\s*[:=]\s*["']?)([^"'\s,&;}}]+)"""
        ),
        rf"\1{REDACTED}",
    ),
    # OAuth parameters in URLs (?code=..., &key=..., &signature=...).
    (re.compile(r"(?i)([?&](?:code|key|signature|sig)=)[^&\s#]+"), rf"\1{REDACTED}"),
    # Provider-specific token shapes.
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]+"), REDACTED),  # Anthropic API keys
    (re.compile(r"\bya29\.[A-Za-z0-9_\-.]+"), REDACTED),  # Google access tokens
    (re.compile(r"\b1//[A-Za-z0-9_\-]{10,}"), REDACTED),  # Google refresh tokens
    # Email addresses (calendar attendees, account ids).
    (
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"),
        "[EMAIL]",
    ),
)


def redact(text: str) -> str:
    """Return `text` with secrets and email addresses masked."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the final rendered record, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class RedactingFilter(logging.Filter):
    """Redacts the message itself, so handlers with other formatters are also safe."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


_HANDLER_MARK = "_coach_handler"
_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "googleapiclient", "anthropic")
_base_record_factory = logging.getLogRecordFactory()


def _redacting_record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
    record = _base_record_factory(*args, **kwargs)
    try:
        record.msg = redact(record.getMessage())
        record.args = None
    except Exception:  # noqa: S110 - a bad format string must not break logging
        pass
    return record


def setup_logging(level: str = "INFO", *, stream: TextIO | None = None) -> logging.Handler:
    """Configure the root logger once; calling again replaces the previous coach handler.

    Messages are redacted when the record is created (global record factory), so every
    handler, including third-party ones, sees the redacted message. Tracebacks are
    rendered per handler; ours redacts them via RedactingFormatter.
    """
    logging.setLogRecordFactory(_redacting_record_factory)
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level)

    # HTTP client debug logs can include headers; keep them quiet regardless of app level.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return handler
