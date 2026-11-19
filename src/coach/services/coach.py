"""Coach service: deterministic snapshot + cached AI outputs + chat history.

Reads use the read-only dashboard loader. Writes touch only the coach tables
(coach_outputs, chat_messages) through a separate connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from coach.ai.coach import PROMPT_VERSION, Coach, DailyAdvice
from coach.ai.context import build_context
from coach.ai.llm import CoachLLM
from coach.analytics.workout import workout_facts
from coach.db import connect, migrate
from coach.db.connection import DbTarget
from coach.db.repositories import ChatRepository, CoachOutputRepository, GoalRepository
from coach.domain import Goal
from coach.engine.safety import SafetyAssessment, assess
from coach.services.dashboard import DashboardData, DashboardView, build_view, load_dashboard_data

MAIN_CONVERSATION = "main"
CHAT_HISTORY_TURNS = 20


@dataclass(frozen=True)
class CoachSnapshot:
    data: DashboardData
    view: DashboardView
    goals: list[Goal]
    safety: SafetyAssessment
    context: dict[str, Any]


class CoachService:
    def __init__(self, db_path: DbTarget, tz: ZoneInfo, llm: CoachLLM) -> None:
        self.db_path = db_path
        self.tz = tz
        self.coach = Coach(llm)
        self.model = llm.model
        conn = connect(db_path)
        try:
            migrate(conn)  # make sure the coach tables exist
        finally:
            conn.close()

    # --- deterministic part (no API call) -------------------------------------------------

    def snapshot(self, today: date) -> CoachSnapshot:
        data = load_dashboard_data(self.db_path, self.tz, today)
        view = build_view(data, 8)
        conn = connect(self.db_path, read_only=True)
        try:
            goals = GoalRepository(conn).list()
        finally:
            conn.close()
        safety = assess(view.recovery, data.activities, data.feedback, data.as_of)
        return CoachSnapshot(data, view, goals, safety, build_context(data, view, goals, safety))

    def facts(self, snap: CoachSnapshot, activity_id: int) -> dict[str, Any] | None:
        return workout_facts(
            activity_id, snap.data.activities, snap.view.plan_status, snap.view.recovery
        )

    def cached(self, kind: str, ref: str) -> dict[str, Any] | None:
        conn = connect(self.db_path)
        try:
            return CoachOutputRepository(conn).get(kind, ref)
        finally:
            conn.close()

    def history(self, conversation_id: str = MAIN_CONVERSATION) -> list[dict[str, Any]]:
        conn = connect(self.db_path)
        try:
            return ChatRepository(conn).history(conversation_id, limit=200)
        finally:
            conn.close()

    # --- AI calls (each one is a billed API request) --------------------------------------

    def daily(self, snap: CoachSnapshot) -> dict[str, Any]:
        advice, result, corrected, error = self.coach.daily_advice(snap.context, snap.safety)
        return self._save(
            "daily",
            snap.data.as_of.isoformat(),
            advice,
            result,
            snap.safety,
            extra={"corrected_by_rules": corrected, "error": error},
        )

    def feedback(self, snap: CoachSnapshot, activity_id: int) -> dict[str, Any]:
        facts = self.facts(snap, activity_id)
        if facts is None:
            raise LookupError(f"no activity {activity_id}")
        feedback, result = self.coach.workout_feedback(snap.context, facts)
        return self._save("workout", str(activity_id), feedback, result, snap.safety, extra={})

    def chat(
        self, snap: CoachSnapshot, question: str, conversation_id: str = MAIN_CONVERSATION
    ) -> str:
        conn = connect(self.db_path)
        try:
            chat = ChatRepository(conn)
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in chat.history(conversation_id, limit=CHAT_HISTORY_TURNS)
            ]
            result = self.coach.chat(snap.context, history, question)
            chat.add(conversation_id, "user", question)
            chat.add(
                conversation_id,
                "assistant",
                result.text,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            return result.text
        finally:
            conn.close()

    def _save(
        self,
        kind: str,
        ref: str,
        output: Any,
        result: Any,
        safety: SafetyAssessment,
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {**output.model_dump(), **extra}
        conn = connect(self.db_path)
        try:
            repo = CoachOutputRepository(conn)
            repo.save(
                kind,
                ref,
                model=result.model if result else "rules",
                prompt_version=PROMPT_VERSION,
                output=payload,
                guardrails=[g.as_dict() for g in safety.guardrails],
                fallback_used=result is None,
                input_tokens=result.input_tokens if result else None,
                output_tokens=result.output_tokens if result else None,
            )
            record = repo.get(kind, ref)
        finally:
            conn.close()
        assert record is not None
        return record


def daily_from_record(record: dict[str, Any]) -> DailyAdvice:
    out = {k: v for k, v in record["output"].items() if k not in ("corrected_by_rules", "error")}
    return DailyAdvice.model_validate(out)
