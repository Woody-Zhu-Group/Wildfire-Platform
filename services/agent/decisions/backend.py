"""Decision backend protocol. SDK types stay inside typesafe_backend.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


QuestionKind = Literal["choice", "score", "noul"]


@dataclass(frozen=True)
class QuestionSpec:
    """Our question shape. Converted to SDK objects only in the TypeSafe backend."""

    kind: QuestionKind
    instructions: str
    criteria: dict[str, str] | list[str] | None = None

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": self.kind,
            "instructions": self.instructions,
        }
        if self.criteria is not None:
            body["criteria"] = self.criteria
        return body


@dataclass
class Answer:
    kind: QuestionKind
    value: str | float | None
    confidence: float | None
    probabilities: dict[str, float] | None


@dataclass
class DecisionResult:
    answers: dict[str, Answer]
    model_version: str | None
    latency_ms: float
    input_tokens: int | None
    raw: dict[str, Any]
    request_id: str
    question_hash: str
    unexpected_option: bool = False
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class DecisionBackend(Protocol):
    name: str

    def evaluate(
        self,
        state: dict | str,
        questions: dict[str, QuestionSpec],
        *,
        request_id: str,
        question_hash: str,
    ) -> DecisionResult | None: ...
