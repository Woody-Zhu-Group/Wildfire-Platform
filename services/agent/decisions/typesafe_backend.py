"""Jev backends. typesafe_sdk is imported only inside a call, and only in shadow mode.

TypeSafeBackend calls api.typesafe.ai. OpenRouterJevBackend sends the same SDK
request to OpenRouter, which serves Jev at POST https://openrouter.ai/api/v1/systemone
with the TypeSafe request body (model, state, questions). Source:
https://openrouter.ai/docs/guides/community/typesafe-sdk
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from services.agent.decisions.backend import Answer, DecisionResult, QuestionSpec
from services.agent.decisions.integrity import parse_raw_answers
from services.agent.pricing import cost_usd

logger = logging.getLogger("services.agent.decisions")

_DISABLED = False
_WARNED = False
_WHITESPACE_WARNED = False

OPENROUTER_JEV_BASE_URL = "https://openrouter.ai/api"
_KEY_ENVS = ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY")


def reset_for_tests() -> None:
    """Test hook. Production code does not call this."""
    global _DISABLED, _WARNED, _WHITESPACE_WARNED
    _DISABLED = False
    _WARNED = False
    _WHITESPACE_WARNED = False


def prepared_api_key(env: str = "TYPESAFE_API_KEY") -> str | None:
    """Return the key with surrounding whitespace removed. Never log the value."""
    global _WHITESPACE_WARNED
    raw = os.environ.get(env)
    if raw is None:
        return None
    stripped = raw.strip()
    if stripped != raw and not _WHITESPACE_WARNED:
        _WHITESPACE_WARNED = True
        logger.warning("%s had leading or trailing whitespace; it was stripped.", env)
    if stripped != raw:
        os.environ[env] = stripped
    return stripped or None


def _warn_once(message: str) -> None:
    global _DISABLED, _WARNED
    _DISABLED = True
    if not _WARNED:
        _WARNED = True
        logger.warning("%s", message)


def _redact(text: str) -> str:
    for env in _KEY_ENVS:
        key = (os.environ.get(env) or "").strip()
        if key and key in text:
            text = text.replace(key, "[redacted]")
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
    key_env = "TYPESAFE_API_KEY"
    # None keeps the SDK default (api.typesafe.ai or TYPESAFE_BASE_URL).
    base_url: str | None = None

    def __init__(
        self,
        *,
        model: str = "jev-latest",
        timeout_seconds: float = 3.0,
        transport: Any | None = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.calls = 0
        # Test hook: an httpx2 transport used in place of the network.
        self._transport = transport

    def _client_auth(self, key: str) -> dict[str, Any]:
        # TypeSafe reads TYPESAFE_API_KEY from the environment, as before.
        return {}

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
        key = prepared_api_key(self.key_env)
        if not key:
            _warn_once(
                f"Jev shadow disabled: {self.key_env} is not set. "
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
                **self._client_auth(key),
            }
            if capture is not None or self._transport is not None:
                import httpx2

                inner_transport = self._transport

                class _Capture(httpx2.BaseTransport):
                    def __init__(self) -> None:
                        self._inner = inner_transport or httpx2.HTTPTransport()

                    def handle_request(self, request):  # type: ignore[no-untyped-def]
                        if capture is not None:
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
        output_tokens = usage.get("output_tokens")
        # The SDK drops OpenRouter's usage.cost, so cost is computed from the
        # price table in services/agent/pricing.py (source URLs there).
        cost = cost_usd(
            raw.get("model") or self.model,
            tokens if isinstance(tokens, int) else None,
            output_tokens if isinstance(output_tokens, int) else None,
        )
        logger.info(
            "jev_usage backend=%s model=%s input_tokens=%s output_tokens=%s "
            "cost_usd=%s latency_ms=%.1f",
            self.name,
            raw.get("model"),
            tokens,
            output_tokens,
            cost,
            latency_ms,
        )
        return DecisionResult(
            answers=parsed,
            model_version=raw.get("model"),
            latency_ms=latency_ms,
            input_tokens=tokens if isinstance(tokens, int) else None,
            raw=raw,
            request_id=request_id,
            question_hash=question_hash,
            unexpected_option=unexpected,
            extra={
                "backend": self.name,
                "output_tokens": output_tokens if isinstance(output_tokens, int) else None,
                "cost_usd": cost,
            },
        )


class OpenRouterJevBackend(TypeSafeBackend):
    """Same SDK request and parsing, sent to OpenRouter with OPENROUTER_API_KEY."""

    name = "openrouter"
    key_env = "OPENROUTER_API_KEY"
    base_url = OPENROUTER_JEV_BASE_URL

    def __init__(
        self,
        *,
        model: str = "jev-1.13",
        timeout_seconds: float = 3.0,
        transport: Any | None = None,
    ) -> None:
        super().__init__(model=model, timeout_seconds=timeout_seconds, transport=transport)

    def _client_auth(self, key: str) -> dict[str, Any]:
        return {"api_key": key, "base_url": self.base_url}


def make_backend(
    backend: str,
    *,
    model: str,
    timeout_seconds: float,
) -> TypeSafeBackend:
    """Build the configured Jev backend (AGENT_JEV_BACKEND)."""
    if backend == "openrouter":
        return OpenRouterJevBackend(model=model, timeout_seconds=timeout_seconds)
    if backend == "typesafe":
        return TypeSafeBackend(model=model, timeout_seconds=timeout_seconds)
    raise ValueError("AGENT_JEV_BACKEND must be typesafe or openrouter")
