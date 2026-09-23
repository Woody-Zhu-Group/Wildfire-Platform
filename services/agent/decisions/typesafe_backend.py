"""Jev backend. typesafe_sdk is imported only inside a call, and only in shadow mode."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from services.agent.decisions.backend import Answer, DecisionResult, QuestionSpec
from services.agent.decisions.integrity import parse_raw_answers

logger = logging.getLogger("services.agent.decisions")

_DISABLED = False
_WARNED = False
_WHITESPACE_WARNED = False


def reset_for_tests() -> None:
    """Test hook. Production code does not call this."""
    global _DISABLED, _WARNED, _WHITESPACE_WARNED
    _DISABLED = False
    _WARNED = False
    _WHITESPACE_WARNED = False


def prepared_api_key() -> str | None:
    """Return the key with surrounding whitespace removed. Never log the value."""
    global _WHITESPACE_WARNED
    raw = os.environ.get("TYPESAFE_API_KEY")
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped != raw and not _WHITESPACE_WARNED:
        _WHITESPACE_WARNED = True
        logger.warning(
            "TYPESAFE_API_KEY had leading or trailing whitespace; it was stripped."
        )
    if stripped != raw:
        os.environ["TYPESAFE_API_KEY"] = stripped
    return stripped or None


def _warn_once(message: str) -> None:
    global _DISABLED, _WARNED
    _DISABLED = True
    if not _WARNED:
        _WARNED = True
        logger.warning("%s", message)


def _redact(text: str) -> str:
    key = os.environ.get("TYPESAFE_API_KEY") or ""
    if key and key in text:
        return text.replace(key, "[redacted]")
    return text


def _to_sdk_question(spec: QuestionSpec) -> Any:
    from typesafe_sdk import Choice, Noul, Score

    if spec.kind == "choice":
        criteria = spec.criteria if isinstance(spec.criteria, dict) else {}
        return Choice(instructions=spec.instructions, criteria=criteria)
    if spec.kind == "score":
        levels = list(spec.criteria) if isinstance(spec.criteria, list) else []
        return Score(instructions=spec.instructions, criteria=levels)
    return Noul(instructions=spec.instructions)


class TypeSafeBackend:
    name = "typesafe"

    def __init__(
        self,
        *,
        model: str = "jev-latest",
        timeout_seconds: float = 3.0,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.calls = 0

    def evaluate(
        self,
        state: dict | str,
        questions: dict[str, QuestionSpec],
        *,
        request_id: str,
        question_hash: str,
        capture: list[bytes] | None = None,
    ) -> DecisionResult | None:
        if _DISABLED:
            return None
        if not prepared_api_key():
            _warn_once(
                "Jev shadow disabled: TYPESAFE_API_KEY is not set. "
                "No further Jev calls will be attempted."
            )
            return None
        try:
            from typesafe_sdk import RetryPolicy, TypeSafeClient
        except ImportError as exc:
            _warn_once(
                "Jev shadow disabled: typesafe_sdk is not installed "
                f"({type(exc).__name__}: {_redact(str(exc))})."
            )
            return None

        started = time.perf_counter()
        try:
            # Default RetryPolicy retries for up to 30s, which would blow the
            # per-call timeout. Zero retries keeps one attempt inside the budget.
            retry = RetryPolicy(max_retries=0, timeout=self.timeout_seconds)
            sdk_questions = {
                name: _to_sdk_question(spec) for name, spec in questions.items()
            }
            self.calls += 1
            client_kwargs: dict[str, Any] = {
                "model": self.model,
                "timeout": self.timeout_seconds,
                "retry": retry,
            }
            if capture is not None:
                import httpx2

                class _Capture(httpx2.BaseTransport):
                    def __init__(self) -> None:
                        self._inner = httpx2.HTTPTransport()

                    def handle_request(self, request):  # type: ignore[no-untyped-def]
                        capture.append(bytes(request.content or b""))
                        return self._inner.handle_request(request)

                    def close(self) -> None:
                        self._inner.close()

                client_kwargs["http_client"] = httpx2.Client(transport=_Capture())
            with TypeSafeClient(**client_kwargs) as client:
                response = client.system_one(
                    state=state,
                    questions=sdk_questions,
                    model=self.model,
                    timeout=self.timeout_seconds,
                    retry=retry,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Jev call failed: %s: %s",
                type(exc).__name__,
                _redact(str(exc)),
            )
            return DecisionResult(
                answers={},
                model_version=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                input_tokens=None,
                raw={},
                request_id=request_id,
                question_hash=question_hash,
                error=f"{type(exc).__name__}: {_redact(str(exc))}",
            )

        latency_ms = (time.perf_counter() - started) * 1000
        raw = response.model_dump(mode="json")
        parsed, unexpected = parse_raw_answers(raw, questions)
        usage = raw.get("usage") or {}
        tokens = usage.get("input_tokens")
        return DecisionResult(
            answers=parsed,
            model_version=raw.get("model"),
            latency_ms=latency_ms,
            input_tokens=tokens if isinstance(tokens, int) else None,
            raw=raw,
            request_id=request_id,
            question_hash=question_hash,
            unexpected_option=unexpected,
        )
