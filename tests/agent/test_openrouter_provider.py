"""OpenRouter LLM provider and Jev backend, with mocked HTTP clients only."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace

import httpx
import pytest

from services.agent.config import AgentSettings
from services.agent.provider import OpenAICompatibleProvider

FAKE_KEY = "sk-or-test-not-a-real-key"


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("AGENT_") or name in {"OPENROUTER_API_KEY", "TYPESAFE_API_KEY"}:
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _hosted(clean_env, **env: str) -> AgentSettings:
    clean_env.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    clean_env.setenv("AGENT_ALLOW_REMOTE_PROVIDER", "true")
    for name, value in env.items():
        clean_env.setenv(name, value)
    return AgentSettings.from_env()


class _Recorder:
    def __init__(self, replies: list[httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._replies = list(replies)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._replies.pop(0)

    def body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)


def _chat_reply(message: dict, *, model: str = "openai/gpt-6-luna") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0002},
        },
    )


def test_defaults_are_openrouter_with_luna_and_sol(clean_env):
    settings = _hosted(clean_env)
    assert settings.llm_provider == "openrouter"
    assert settings.model_base_url == "https://openrouter.ai/api/v1"
    assert settings.model == "openai/gpt-6-luna"
    assert settings.llm_fallback_model == "openai/gpt-6-sol"
    assert settings.jev_backend == "typesafe"
    assert settings.jev_model == "jev-latest"
    assert settings.jev_mode == "off"
    assert FAKE_KEY not in repr(settings)


def test_model_and_fallback_come_from_the_environment(clean_env):
    settings = _hosted(
        clean_env, AGENT_LLM_MODEL="openai/gpt-6-sol", AGENT_LLM_FALLBACK_MODEL=""
    )
    assert settings.model == "openai/gpt-6-sol"
    assert settings.llm_fallback_model is None


def test_the_remote_provider_gate_fails_loudly_when_off(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    with pytest.raises(ValueError, match="AGENT_ALLOW_REMOTE_PROVIDER"):
        AgentSettings.from_env()


def test_ollama_is_no_longer_a_provider(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    clean_env.setenv("AGENT_ALLOW_REMOTE_PROVIDER", "true")
    clean_env.setenv("AGENT_LLM_PROVIDER", "ollama")
    with pytest.raises(ValueError, match="only LLM provider is openrouter"):
        AgentSettings.from_env()


def test_removed_ollama_settings_are_ignored(clean_env):
    settings = _hosted(
        clean_env,
        AGENT_MODEL="qwen2.5:7b",
        AGENT_MODEL_BASE_URL="http://127.0.0.1:11434/v1",
        AGENT_THINKING="on",
        AGENT_STRUCTURED_MODE="prompt",
        AGENT_NUM_CTX="4096",
    )
    assert settings.model == "openai/gpt-6-luna"
    assert settings.model_base_url == "https://openrouter.ai/api/v1"
    for removed in ("thinking", "structured_mode", "num_ctx", "model_runtime", "provider"):
        assert not hasattr(settings, removed)


@pytest.mark.parametrize("flag", ["AGENT_LLM_PROVIDER", "AGENT_JEV_BACKEND"])
def test_missing_openrouter_key_fails_loudly(clean_env, flag):
    clean_env.setenv(flag, "openrouter")
    clean_env.setenv("AGENT_ALLOW_REMOTE_PROVIDER", "true")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        AgentSettings.from_env()


def test_tool_call_path_uses_native_tool_choice(clean_env, capsys):
    from services.agent.schemas import openai_tools

    settings = _hosted(clean_env)
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "data_query_records", "arguments": '{"dataset":"epss_outages"}'},
    }
    recorder = _Recorder([_chat_reply({"content": None, "tool_calls": [call]})])
    provider = OpenAICompatibleProvider(settings, transport=httpx.MockTransport(recorder))
    catalog = openai_tools(["data_query_records", "visualization_create"], profile="lean_enums")

    async def run():
        reply = await provider.complete(
            messages=[{"role": "user", "content": "q"}],
            tools=catalog,
            structured_response=False,
            tool_routing=True,
            candidate_tools=["data_query_records"],
            model=settings.model,
        )
        await provider.close()
        return reply

    reply = asyncio.run(run())
    request = recorder.requests[0]
    body = recorder.body()
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert body["model"] == "openai/gpt-6-luna"
    assert body["tool_choice"] == "required"
    # Unsupported for Luna on OpenRouter; with require_parameters it 404s.
    assert "parallel_tool_calls" not in body
    assert [t["function"]["name"] for t in body["tools"]] == ["data_query_records"]
    from services.agent.provider import strict_nullable_tool

    assert body["tools"][0] == strict_nullable_tool(catalog[0])
    for removed in ("format", "options", "keep_alive", "think"):
        assert removed not in body
    assert "response_format" not in body
    assert [c["function"]["name"] for c in reply.tool_calls] == ["data_query_records"]
    assert json.loads(reply.tool_calls[0]["function"]["arguments"]) == {"dataset": "epss_outages"}
    assert reply.usage["computed_cost_usd"] == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines() if "llm_usage" in line]
    assert logged[0]["input_tokens"] == 1000 and logged[0]["output_tokens"] == 200
    assert logged[0]["model"] == "openai/gpt-6-luna"
    assert FAKE_KEY not in json.dumps(logged)


def test_synthesis_path_uses_strict_structured_output(clean_env):
    settings = _hosted(clean_env)
    answer = {"status": "answer", "answer": "741 ignitions.", "claims": [{"text": "741", "evidence_ids": ["e1"]}]}
    recorder = _Recorder([_chat_reply({"content": json.dumps(answer)})])
    provider = OpenAICompatibleProvider(settings, transport=httpx.MockTransport(recorder))

    async def run():
        reply = await provider.complete(
            messages=[{"role": "user", "content": "q"}],
            tools=[],
            structured_response=True,
            phase="synthesis",
            timeout_seconds=30,
        )
        await provider.close()
        return reply

    reply = asyncio.run(run())
    body = recorder.body()
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["required"] == ["status", "answer", "claims"]
    claim = schema["schema"]["properties"]["claims"]["items"]
    assert claim["required"] == ["text", "evidence_ids"]
    assert claim["additionalProperties"] is False
    assert "tools" not in body and "tool_choice" not in body
    assert body["reasoning"] == {"effort": "none"}
    assert json.loads(reply.content) == answer


def test_failed_luna_request_retries_once_on_sol(clean_env):
    settings = _hosted(clean_env)
    recorder = _Recorder(
        [
            httpx.Response(502, json={"error": {"message": "upstream"}}),
            _chat_reply({"content": '{"status":"answer","answer":"x","claims":[]}'}, model="openai/gpt-6-sol"),
        ]
    )
    provider = OpenAICompatibleProvider(settings, transport=httpx.MockTransport(recorder))

    async def run():
        reply = await provider.complete(messages=[{"role": "user", "content": "q"}], tools=[])
        await provider.close()
        return reply

    reply = asyncio.run(run())
    assert [recorder.body(i)["model"] for i in range(2)] == ["openai/gpt-6-luna", "openai/gpt-6-sol"]
    assert reply.usage["computed_cost_usd"] == pytest.approx((1000 * 2.0 + 200 * 10.0) / 1e6)


def test_retry_turns_escalate_to_the_fallback_model(clean_env):
    from services.agent.orchestrator import AgentOrchestrator

    hosted = _hosted(clean_env)
    no_fallback = replace(hosted, llm_fallback_model=None)
    for settings, second in ((hosted, "openai/gpt-6-sol"), (no_fallback, "openai/gpt-6-luna")):
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        orchestrator.settings = settings
        assert orchestrator._turn_model(1, "openai/gpt-6-luna") == "openai/gpt-6-luna"
        assert orchestrator._turn_model(2, "openai/gpt-6-luna") == second


def test_health_reports_provider_model_and_fallback(clean_env):
    settings = _hosted(clean_env)
    recorder = _Recorder(
        [httpx.Response(200, json={"data": [{"id": "openai/gpt-6-luna"}, {"id": "openai/gpt-6-sol"}]})]
    )
    provider = OpenAICompatibleProvider(settings, transport=httpx.MockTransport(recorder))

    async def run():
        health = await provider.health()
        await provider.close()
        return health

    health = asyncio.run(run())
    assert recorder.requests[0].url.path == "/api/v1/models"
    assert health["status"] == "ok" and health["available"] is True
    assert health["provider"] == "openrouter"
    assert health["model"] == "openai/gpt-6-luna"
    assert health["fallback_model"] == "openai/gpt-6-sol"
    for removed in ("configured_num_ctx", "effective_num_ctx", "runtime_model"):
        assert removed not in health


def test_openrouter_jev_backend_without_key_does_not_call(clean_env, caplog):
    from services.agent.decisions.backend import QuestionSpec
    from services.agent.decisions.typesafe_backend import OpenRouterJevBackend, reset_for_tests

    reset_for_tests()
    backend = OpenRouterJevBackend()
    with caplog.at_level("WARNING"):
        result = backend.evaluate(
            "state",
            {"q": QuestionSpec(kind="noul", instructions="x")},
            request_id="r",
            question_hash="h",
        )
    assert result is None and backend.calls == 0
    assert any("OPENROUTER_API_KEY" in rec.message for rec in caplog.records)
    reset_for_tests()


def test_make_backend_follows_the_setting():
    from services.agent.decisions.typesafe_backend import (
        OpenRouterJevBackend,
        TypeSafeBackend,
        make_backend,
    )

    assert type(make_backend("typesafe", model="jev-latest", timeout_seconds=3)) is TypeSafeBackend
    assert isinstance(make_backend("openrouter", model="jev-1.13", timeout_seconds=3), OpenRouterJevBackend)
    with pytest.raises(ValueError):
        make_backend("other", model="jev", timeout_seconds=3)


def test_openrouter_jev_backend_pins_the_dated_model(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    clean_env.setenv("AGENT_ALLOW_REMOTE_PROVIDER", "true")
    clean_env.setenv("AGENT_JEV_BACKEND", "openrouter")
    assert AgentSettings.from_env().jev_model == "typesafe/jev-1.13-20260917"
    clean_env.setenv("AGENT_JEV_MODEL", "jev-latest")
    assert AgentSettings.from_env().jev_model == "jev-latest"


def test_strict_tool_schema_makes_optional_fields_nullable():
    from services.agent.provider import strict_nullable_tool
    from services.agent.schemas import openai_tools

    original = openai_tools(["data_query_spatial"], profile="lean_enums")[0]
    strict = strict_nullable_tool(original)["function"]
    params = strict["parameters"]
    assert strict["strict"] is True
    assert set(params["required"]) == set(params["properties"])
    assert params["additionalProperties"] is False
    # Required stays non-null; optional fields accept null.
    assert params["properties"]["kind"]["type"] == "string"
    assert None not in params["properties"]["kind"]["enum"]
    assert params["properties"]["lat"]["type"] == ["number", "null"]
    assert params["properties"]["hftd_tier"]["enum"][-1] is None
    # The input catalog is not mutated.
    assert original["function"]["parameters"]["properties"]["lat"]["type"] == "number"


def test_null_arguments_mean_not_set(clean_env):
    settings = _hosted(clean_env)
    call = {
        "id": "call_1",
        "type": "function",
        "function": {
            "name": "data_query_spatial",
            "arguments": json.dumps(
                {"kind": "summary", "utility": "PGE", "lat": None, "lon": None, "hftd_tier": None}
            ),
        },
    }
    recorder = _Recorder([_chat_reply({"content": None, "tool_calls": [call]})])
    provider = OpenAICompatibleProvider(settings, transport=httpx.MockTransport(recorder))

    async def run():
        reply = await provider.complete(
            messages=[{"role": "user", "content": "q"}],
            tools=[],
            structured_response=False,
            tool_routing=True,
            candidate_tools=["data_query_spatial"],
        )
        await provider.close()
        return reply

    reply = asyncio.run(run())
    assert json.loads(reply.tool_calls[0]["function"]["arguments"]) == {"kind": "summary", "utility": "PGE"}
