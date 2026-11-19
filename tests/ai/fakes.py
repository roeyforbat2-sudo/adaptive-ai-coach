"""A fake Anthropic client: records requests, returns scripted responses. Never networks."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2


def reply(
    text: str | dict[str, Any],
    *,
    stop: str = "end_turn",
    category: str | None = None,
    model: str = "claude-sonnet-5-5",
) -> SimpleNamespace:
    body = json.dumps(text) if isinstance(text, dict) else text
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=body),
        ],
        stop_reason=stop,
        stop_details=SimpleNamespace(category=category) if stop == "refusal" else None,
        model=model,
        usage=SimpleNamespace(input_tokens=4000, output_tokens=600),
    )


def connection_error() -> anthropic.APIConnectionError:
    return anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )


class _Messages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeAnthropic:
    def __init__(self, *responses: Any) -> None:
        self.messages = _Messages(list(responses))
        self.beta = SimpleNamespace(messages=self.messages)

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.messages.calls


GOOD_DAILY: dict[str, Any] = {
    "headline": "Good recovery; keep the easy run.",
    "today": {
        "planned": "easy (60 min)",
        "recommendation": "keep",
        "session": "Easy run",
        "intensity": "easy",
        "duration_min": 60,
        "why": "HRV in range.",
    },
    "recent_training": "Consistent week.",
    "goal_progress": "On track.",
    "watch_outs": [],
    "data_caveats": [],
}

HARD_DAILY = {
    **GOOD_DAILY,
    "today": {
        **GOOD_DAILY["today"],
        "intensity": "hard",
        "recommendation": "swap",
        "session": "Intervals",
    },
}

GOOD_FEEDBACK = {
    "summary": "Solid easy run.",
    "vs_plan": "As planned.",
    "went_well": ["Even pace"],
    "to_watch": [],
    "next_time": "Same again.",
}
