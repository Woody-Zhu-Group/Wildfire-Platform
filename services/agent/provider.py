"""OpenAI-compatible model provider used by Ollama and future hosted APIs."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from services.agent.config import AgentSettings
from services.agent.constrained import call_envelope_schema, parse_envelope_content
from services.agent.pricing import cost_usd
from services.agent.schemas import AgentAnswer


class SynthesisTimeoutError(TimeoutError):
    """Raised when a synthesis completion exceeds the configured budget."""


def agent_answer_format_schema() -> dict[str, Any]:
    """Compact JSON schema for native Ollama constrained synthesis."""
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

    Cancelling the httpx task drops the connection to Ollama so a single-slot
    generation does not keep running after the client disconnects.
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
            headers={
                "Authorization": f"Bearer {settings.model_api_key or 'ollama'}"
            },
            transport=transport,
        )
        # Native Ollama endpoint exposes `format` + timing fields used for
        # constrained tool routing; OpenAI-compatible /v1 does not.
        self._native_base = settings.model_base_url.removesuffix("/v1")
        self._native_client = httpx.AsyncClient(
            base_url=self._native_base,
            timeout=settings.request_timeout_seconds,
            transport=transport,
        )
        self.effective_num_ctx: int | None = None
        self.hosted = settings.hosted_llm

    async def close(self) -> None:
        await self._client.aclose()
        await self._native_client.aclose()

    def _options(self, *, num_predict: int) -> dict[str, Any]:
        return {
            "num_ctx": self.settings.num_ctx,
            "num_predict": num_predict,
            "temperature": self.settings.temperature,
            "seed": self.settings.seed,
        }

    async def ensure_context_loaded(self) -> dict[str, Any]:
        """Unload any stale 4096 load and warm the model with configured num_ctx."""
        model = self.settings.request_model
        if self.hosted:
            # Hosted models have no native Ollama endpoint or num_ctx to pin.
            return {
                "configured_num_ctx": None,
                "effective_num_ctx": None,
                "warmup_latency_ms": 0.0,
                "model": model,
                "provider": self.settings.llm_provider,
            }
        try:
            await self._native_client.post(
                "/api/generate",
                json={"model": model, "keep_alive": 0, "stream": False},
            )
        except httpx.HTTPError:
            pass
        started = time.perf_counter()
        response = await self._native_client.post(
            "/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "OK"}],
                "stream": False,
                "keep_alive": -1,
                "think": False,
                "options": self._options(num_predict=1),
            },
        )
        response.raise_for_status()
        latency_ms = (time.perf_counter() - started) * 1000
        loaded = await self._read_loaded_context(model)
        self.effective_num_ctx = loaded
        info = {
            "configured_num_ctx": self.settings.num_ctx,
            "effective_num_ctx": loaded,
            "warmup_latency_ms": round(latency_ms, 2),
            "model": model,
        }
        print(json.dumps({"event": "model_context", **info}))
        return info

    async def _read_loaded_context(self, model: str) -> int | None:
        try:
            response = await self._native_client.get("/api/ps")
            response.raise_for_status()
            for item in response.json().get("models") or []:
                name = item.get("name") or item.get("model")
                if name == model or (name or "").startswith(model):
                    value = item.get("context_length")
                    return int(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None
        return None

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None = None,
        structured_response: bool = True,
        constrained_tool_routing: bool = False,
        candidate_tools: list[str] | None = None,
        cancel_event: asyncio.Event | None = None,
        phase: str = "model",
        timeout_seconds: float | None = None,
        thinking: bool | None = None,
        model: str | None = None,
    ) -> ModelReply:
        use_thinking = (
            self.settings.thinking == "on" if thinking is None else bool(thinking)
        )
        request_model = model or self.settings.request_model

        if self.effective_num_ctx is None and not self.hosted:
            try:
                await self.ensure_context_loaded()
            except Exception as exc:  # noqa: BLE001
                print(
                    json.dumps(
                        {
                            "event": "model_lazy_warmup_failed",
                            "error": str(exc),
                            "model": request_model,
                        }
                    )
                )

        async def _run() -> ModelReply:
            if self.hosted:
                return await self._complete_hosted_with_fallback(
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    structured_response=structured_response,
                    tool_routing=constrained_tool_routing,
                    candidate_tools=candidate_tools or [],
                    cancel_event=cancel_event,
                    phase=phase,
                    thinking=use_thinking,
                    model=request_model,
                )
            if constrained_tool_routing:
                return await self._complete_native_tool_envelope(
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    candidate_tools=candidate_tools or [],
                    cancel_event=cancel_event,
                    phase=phase,
                    thinking=use_thinking,
                    model=request_model,
                )
            if structured_response and self.settings.structured_mode == "constrained":
                return await self._complete_native_structured(
                    messages=messages,
                    max_tokens=max_tokens,
                    cancel_event=cancel_event,
                    phase=phase,
                    thinking=use_thinking,
                    model=request_model,
                )
            return await self._complete_openai(
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                structured_response=structured_response,
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
                        "model": self.settings.request_model,
                        "num_ctx": self.settings.num_ctx,
                    }
                )
            )
            raise SynthesisTimeoutError(
                f"{phase} exceeded {timeout_seconds:.0f}s"
            ) from exc

    async def _complete_openai(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None,
        structured_response: bool,
        cancel_event: asyncio.Event | None,
        phase: str,
        thinking: bool,
        model: str,
    ) -> ModelReply:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "temperature": self.settings.temperature,
            "seed": self.settings.seed,
            "max_tokens": max_tokens or self.settings.max_completion_tokens,
            "reasoning_effort": ("medium" if thinking else "none"),
            # Ollama OpenAI shim accepts options.num_ctx; without it loads at 4096.
            "options": {"num_ctx": self.settings.num_ctx},
        }
        if structured_response and self.settings.structured_mode == "constrained":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_answer",
                    "strict": True,
                    "schema": AgentAnswer.model_json_schema(),
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
        return ModelReply(
            content=content,
            tool_calls=tool_calls,
            raw=raw,
            latency_ms=latency_ms,
            usage=raw.get("usage") or {},
        )

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
        """OpenRouter chat completion with native tool_choice and strict outputs.

        Replaces two Ollama workarounds: the JSON call envelope used because
        Ollama has no tool_choice, and native /api/chat format for synthesis.
        """
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
            # "required" forces at least one call, as the envelope schema did.
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

    async def _complete_native_structured(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int | None,
        cancel_event: asyncio.Event | None,
        phase: str,
        thinking: bool,
        model: str,
    ) -> ModelReply:
        """Constrained JSON synthesis via native /api/chat (same path as routing)."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": agent_answer_format_schema(),
            "think": thinking,
            "stream": False,
            "keep_alive": -1,
            "options": self._options(
                num_predict=max_tokens or self.settings.max_synthesis_tokens
            ),
        }
        started = time.perf_counter()
        response = await _cancellable_post(
            self._native_client,
            "/api/chat",
            json_payload=payload,
            cancel_event=cancel_event,
            phase=phase,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        raw = response.json()
        message = raw.get("message") or {}
        content = str(message.get("content") or "")
        return ModelReply(
            content=content,
            tool_calls=[],
            raw=raw,
            latency_ms=latency_ms,
            usage={
                "prompt_tokens": int(raw.get("prompt_eval_count") or 0),
                "completion_tokens": int(raw.get("eval_count") or 0),
            },
        )

    async def _complete_native_tool_envelope(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int | None,
        candidate_tools: list[str],
        cancel_event: asyncio.Event | None = None,
        phase: str = "routing",
        thinking: bool = False,
        model: str | None = None,
    ) -> ModelReply:
        options = self._options(
            num_predict=max_tokens or self.settings.max_routing_tokens
        )
        options["stop"] = ["\n\n\n"]
        payload: dict[str, Any] = {
            "model": model or self.settings.request_model,
            "messages": messages,
            "tools": tools,
            "format": call_envelope_schema(candidate_tools, profile="lean_enums"),
            "think": thinking,
            "stream": False,
            "keep_alive": -1,
            "options": options,
        }
        started = time.perf_counter()
        response = await _cancellable_post(
            self._native_client,
            "/api/chat",
            json_payload=payload,
            cancel_event=cancel_event,
            phase=phase,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        raw = response.json()
        message = raw.get("message") or {}
        content = str(message.get("content") or "")
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            tool_calls = parse_envelope_content(content)
        for call in tool_calls:
            function = call.get("function") or {}
            if isinstance(function.get("arguments"), dict):
                function["arguments"] = json.dumps(function["arguments"])
        return ModelReply(
            content=content,
            tool_calls=tool_calls,
            raw=raw,
            latency_ms=latency_ms,
            usage={
                "prompt_tokens": int(raw.get("prompt_eval_count") or 0),
                "completion_tokens": int(raw.get("eval_count") or 0),
            },
        )

    async def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            response = await self._client.get("/models")
            response.raise_for_status()
            models = response.json().get("data") or []
            names = [m.get("id") for m in models]
            return {
                "status": (
                    "ok" if self.settings.request_model in names else "degraded"
                ),
                "model": self.settings.model,
                "runtime_model": self.settings.request_model,
                "available": self.settings.request_model in names,
                "configured_num_ctx": self.settings.num_ctx,
                "effective_num_ctx": self.effective_num_ctx,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "unavailable",
                "model": self.settings.model,
                "available": False,
                "detail": str(exc),
            }
