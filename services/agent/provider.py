"""OpenAI-compatible model provider for hosted APIs (OpenRouter)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from services.agent.config import AgentSettings
from services.agent.pricing import cost_usd


class SynthesisTimeoutError(TimeoutError):
    """Raised when a synthesis completion exceeds the configured budget."""


def agent_answer_format_schema() -> dict[str, Any]:
    """Compact JSON schema for the structured synthesis answer."""
    return {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["answer", "clarification", "unsupported", "error"],
            },
            "answer": {"type": "string"},
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["text", "evidence_ids"],
                },
            },
        },
        "required": ["status", "answer", "claims"],
    }


def strict_agent_answer_schema() -> dict[str, Any]:
    """The same answer schema, closed for OpenAI strict structured outputs.

    Strict mode requires additionalProperties false on every object; the fields
    and required lists (including evidence_ids) are unchanged.
    """
    schema = agent_answer_format_schema()
    schema["additionalProperties"] = False
    schema["properties"]["claims"]["items"]["additionalProperties"] = False
    return schema


def strict_nullable_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Strict function schema where every optional field accepts null.

    Strict mode requires every property to be listed in required. Without null,
    a model must invent a value for each optional filter (circuit_id "000000000",
    lat 0). Null means "not set" and is removed by drop_null_arguments.
    """
    function = dict(tool.get("function") or {})
    params = dict(function.get("parameters") or {})
    required = set(params.get("required") or [])
    properties: dict[str, Any] = {}
    for name, spec in (params.get("properties") or {}).items():
        spec = dict(spec)
        if name not in required:
            kind = spec.get("type")
            if isinstance(kind, str):
                spec["type"] = [kind, "null"]
            elif isinstance(kind, list) and "null" not in kind:
                spec["type"] = [*kind, "null"]
            if "enum" in spec and None not in spec["enum"]:
                spec["enum"] = [*spec["enum"], None]
        properties[name] = spec
    params["properties"] = properties
    params["required"] = list(properties)
    params["additionalProperties"] = False
    function["parameters"] = params
    function["strict"] = True
    return {**tool, "function": function}


def drop_null_arguments(arguments: str) -> str:
    """Remove null fields from a JSON arguments string; leave invalid JSON as is."""
    try:
        parsed = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return arguments
    if not isinstance(parsed, dict):
        return arguments
    return json.dumps({key: value for key, value in parsed.items() if value is not None})


async def _cancellable_post(
    client: httpx.AsyncClient,
    path: str,
    *,
    json_payload: dict[str, Any],
    cancel_event: asyncio.Event | None,
    phase: str = "model",
) -> httpx.Response:
    """POST that aborts the in-flight HTTP request when cancel_event is set.

    Cancelling the httpx task drops the connection so a generation does not
    keep billing after the client disconnects.
    """
    request_task = asyncio.create_task(client.post(path, json=json_payload))
    if cancel_event is None:
        return await request_task

    cancel_task = asyncio.create_task(cancel_event.wait())
    try:
        done, _pending = await asyncio.wait(
            {request_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancel_task in done and cancel_event.is_set():
            print(
                json.dumps(
                    {
                        "event": "model_cancel_abort",
                        "phase": phase,
                        "path": path,
                        "model": json_payload.get("model"),
                    }
                )
            )
            request_task.cancel()
            try:
                await request_task
            except (asyncio.CancelledError, httpx.HTTPError):
                pass
            raise asyncio.CancelledError()
        return await request_task
    finally:
        if not cancel_task.done():
            cancel_task.cancel()
            try:
                await cancel_task
            except asyncio.CancelledError:
                pass


@dataclass
class ModelReply:
    content: str
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any]
    latency_ms: float
    usage: dict[str, Any]


class OpenAICompatibleProvider:
    def __init__(
        self,
        settings: AgentSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.model_base_url,
            timeout=settings.request_timeout_seconds,
            headers={"Authorization": f"Bearer {settings.model_api_key}"},
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        structured_response: bool = True,
        tool_routing: bool = False,
        candidate_tools: list[str] | None = None,
        cancel_event: asyncio.Event | None = None,
        phase: str = "model",
        timeout_seconds: float | None = None,
        thinking: bool | None = None,
        model: str | None = None,
    ) -> ModelReply:
        use_thinking = bool(thinking)
        request_model = model or self.settings.model

        async def _run() -> ModelReply:
            return await self._complete_hosted_with_fallback(
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                structured_response=structured_response,
                tool_routing=tool_routing,
                candidate_tools=candidate_tools or [],
                cancel_event=cancel_event,
                phase=phase,
                thinking=use_thinking,
                model=request_model,
            )

        if timeout_seconds is None:
            return await _run()
        try:
            return await asyncio.wait_for(_run(), timeout=timeout_seconds)
        except TimeoutError as exc:
            print(
                json.dumps(
                    {
                        "event": "model_timeout",
                        "phase": phase,
                        "timeout_seconds": timeout_seconds,
                        "model": request_model,
                    }
                )
            )
            raise SynthesisTimeoutError(
                f"{phase} exceeded {timeout_seconds:.0f}s"
            ) from exc

    async def _complete_hosted_with_fallback(
        self,
        *,
        model: str,
        phase: str,
        **kwargs: Any,
    ) -> ModelReply:
        """One hosted request; a failed primary request retries once on the fallback."""
        try:
            return await self._complete_hosted(model=model, phase=phase, **kwargs)
        except (httpx.HTTPError, ValueError) as exc:
            fallback = self.settings.llm_fallback_model
            if not fallback or fallback == model:
                raise
            print(
                json.dumps(
                    {
                        "event": "llm_fallback",
                        "phase": phase,
                        "from_model": model,
                        "to_model": fallback,
                        "error": type(exc).__name__,
                    }
                )
            )
            return await self._complete_hosted(model=fallback, phase=phase, **kwargs)

    async def _complete_hosted(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None,
        structured_response: bool,
        tool_routing: bool,
        candidate_tools: list[str],
        cancel_event: asyncio.Event | None,
        phase: str,
        thinking: bool,
        model: str,
    ) -> ModelReply:
        """OpenRouter chat completion with native tool_choice and strict outputs."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "seed": self.settings.seed,
            "reasoning": {"effort": "medium" if thinking else "none"},
            # Route only to hosts that honor tool_choice and response_format.
            "provider": {"require_parameters": True},
        }
        if tool_routing:
            allowed = set(candidate_tools)
            payload["tools"] = [
                strict_nullable_tool(tool)
                for tool in tools
                if not allowed or tool.get("function", {}).get("name") in allowed
            ]
            # "required" forces at least one call.
            payload["tool_choice"] = "required"
            # Several calls per turn are allowed by default. Do not send
            # parallel_tool_calls: OpenRouter does not list it for GPT-6 Luna, and
            # with require_parameters the request then 404s (no endpoint).
            payload["max_tokens"] = max_tokens or self.settings.max_routing_tokens
        else:
            payload["max_tokens"] = max_tokens or self.settings.max_synthesis_tokens
            if tools:
                payload["tools"] = tools
        if structured_response and not tool_routing:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_answer",
                    "strict": True,
                    "schema": strict_agent_answer_schema(),
                },
            }

        started = time.perf_counter()
        response = await _cancellable_post(
            self._client,
            "/chat/completions",
            json_payload=payload,
            cancel_event=cancel_event,
            phase=phase,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        raw = response.json()
        choices = raw.get("choices") or []
        if not choices:
            raise ValueError("model response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        for call in tool_calls:
            function = call.get("function") or {}
            if isinstance(function.get("arguments"), dict):
                function["arguments"] = json.dumps(function["arguments"])
            if tool_routing and isinstance(function.get("arguments"), str):
                function["arguments"] = drop_null_arguments(function["arguments"])
        usage = dict(raw.get("usage") or {})
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        served_model = str(raw.get("model") or model)
        computed = cost_usd(model, input_tokens, output_tokens)
        usage["computed_cost_usd"] = computed
        usage["model"] = served_model
        print(
            json.dumps(
                {
                    "event": "llm_usage",
                    "provider": self.settings.llm_provider,
                    "phase": phase,
                    "model": served_model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": computed,
                    "reported_cost_usd": usage.get("cost"),
                    "latency_ms": round(latency_ms, 2),
                }
            )
        )
        return ModelReply(
            content=content,
            tool_calls=tool_calls,
            raw=raw,
            latency_ms=latency_ms,
            usage=usage,
        )

    async def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = await self._client.get("/models")
            response.raise_for_status()
            models = response.json().get("data") or []
            names = [m.get("id") for m in models]
            available = self.settings.model in names
            return {
                "status": "ok" if available else "degraded",
                "provider": self.settings.llm_provider,
                "model": self.settings.model,
                "fallback_model": self.settings.llm_fallback_model,
                "available": available,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "unavailable",
                "provider": self.settings.llm_provider,
                "model": self.settings.model,
                "fallback_model": self.settings.llm_fallback_model,
                "available": False,
                "detail": str(exc),
            }
