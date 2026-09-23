"""Backend used when shadow mode is disabled. Never calls a model."""

from __future__ import annotations

from services.agent.decisions.backend import DecisionResult, QuestionSpec


class NullBackend:
    name = "null"

    def evaluate(
        self,
        state: dict | str,
        questions: dict[str, QuestionSpec],
        *,
        request_id: str,
        question_hash: str,
    ) -> DecisionResult | None:
        return None
