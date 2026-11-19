"""Thin wrapper around the Anthropic Messages API for the coach.

- Model from settings (default `claude-sonnet-5-5`, chosen by the user).
- Server-side refusal fallback enabled by default (`fallbacks="default"` with beta
  `server-side-fallback-2026-07-01`); a remaining refusal raises CoachRefusalError.
- Adaptive thinking (the model default); depth via `output_config.effort`.
- Structured output via `output_config.format` (JSON schema) where a schema is given.
- The API key comes from Windows Credential Manager, else the SDK's own resolution
  (ANTHROPIC_API_KEY / `ant auth login` profile). It is never logged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import anthropic

from coach import secrets

log = logging.getLogger(__name__)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class CoachError(RuntimeError):
    """Base error; messages are safe to show (no secrets, no payloads)."""


class CoachUnavailableError(CoachError):
    pass


class CoachRefusalError(CoachError):
    pass


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None

    def json(self) -> Any:
        return json.loads(self.text)


def has_api_key() -> bool:
    import os

    return secrets.has_secret(secrets.ANTHROPIC_API_KEY) or bool(
        os.environ.get("ANTHROPIC_API_KEY")
    )


def default_client() -> anthropic.Anthropic:
    if secrets.has_secret(secrets.ANTHROPIC_API_KEY):
        return anthropic.Anthropic(
            api_key=secrets.get_secret(secrets.ANTHROPIC_API_KEY), timeout=180.0
        )
    return anthropic.Anthropic(timeout=180.0)


class CoachLLM:
    def __init__(
        self,
        model: str,
        *,
        max_tokens: int = 16_000,
        client_factory: Callable[[], Any] = default_client,
        enabled: bool | None = None,
    ) -> None:
        if enabled is None:
            from coach.config import get_settings

            enabled = get_settings().ai_enabled
        self.enabled = enabled
        self.model = model
        self.max_tokens = max_tokens
        self._client_factory = client_factory
        self._client: Any = None

    def _client_or_create(self) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def complete(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        effort: str = "medium",
        schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        if not self.enabled:
            # Hard guard: checked before any client exists, so no request can be made.
            raise CoachUnavailableError(
                "The AI coach is turned off (COACH_AI_ENABLED is not true)."
            )
        output_config: dict[str, Any] = {"effort": effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        try:
            response = self._client_or_create().beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=system,
                messages=messages,
                output_config=output_config,
            )
        except anthropic.AuthenticationError:
            raise CoachUnavailableError(
                "The Anthropic API key was rejected. Store a valid key with "
                "`python -m coach.ai.set_key`."
            ) from None
        except anthropic.RateLimitError:
            raise CoachUnavailableError(
                "Anthropic rate limit reached; try again in a minute."
            ) from None
        except anthropic.APIStatusError as exc:
            raise CoachUnavailableError(f"Anthropic API error (HTTP {exc.status_code}).") from None
        except anthropic.APIConnectionError:
            raise CoachUnavailableError("Could not reach the Anthropic API (network).") from None

        if response.stop_reason == "refusal":
            category = (
                getattr(response.stop_details, "category", None) if response.stop_details else None
            )
            raise CoachRefusalError(f"The model declined this request (category: {category}).")
        if response.stop_reason == "max_tokens":
            raise CoachUnavailableError("The answer was cut off (max_tokens); try again.")
        text = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        if not text:
            raise CoachUnavailableError("The model returned no text.")
        usage = response.usage
        return LLMResult(
            text=text,
            model=str(response.model),
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )
