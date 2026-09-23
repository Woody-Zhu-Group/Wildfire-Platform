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
    clean_env.setenv("AGENT_LLM_PROVIDER", "openrouter")
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


def test_defaults_are_unchanged(clean_env):
    settings = AgentSettings.from_env()
    assert settings.llm_provider == "ollama"
    assert settings.hosted_llm is False
    assert settings.model == "qwen2.5:7b"
    assert settings.model_base_url == "http://127.0.0.1:11434/v1"
    assert settings.llm_fallback_model is None
    assert settings.jev_backend == "typesafe"
    assert settings.jev_model == "jev-latest"
    assert settings.jev_mode == "off"


def test_default_ollama_requests_still_use_the_native_workarounds(clean_env):
    settings = AgentSettings.from_env()
    recorder = _Recorder(
        [
            httpx.Response(
                200,
                json={
                    "message": {"content": '{"calls":[{"tool":"data_query_records","arguments":{}}]}'},
                    "prompt_eval_count": 5,
                    "eval_count": 3,
                },
            ),
            httpx.Response(
                200,
                json={
                    "message": {"content": '{"status":"answer","answer":"x","claims":[]}'},
                    "prompt_eval_count": 5,
                    "eval_count": 3,
                },
            ),
        ]
    )
    provider = OpenAICompatibleProvider(settings, transport=httpx.MockTransport(recorder))
    provider.effective_num_ctx = settings.num_ctx

    async def run():
        routed = await provider.complete(
            messages=[{"role": "user", "content": "q"}],
            tools=[],
            structured_response=False,
            constrained_tool_routing=True,
            candidate_tools=["data_query_records"],
        )
        synth = await provider.complete(messages=[{"role": "user", "content": "q"}], tools=[])
        await provider.close()
        return routed, synth

    routed, _ = asyncio.run(run())
    assert [r.url.path for r in recorder.requests] == ["/api/chat", "/api/chat"]
    assert "format" in recorder.body(0) and "tool_choice" not in recorder.body(0)
    assert routed.tool_calls[0]["id"] == "call_envelope_0"
    assert recorder.body(1)["format"]["required"] == ["status", "answer", "claims"]


def test_openrouter_settings_use_luna_and_sol(clean_env):
    settings = _hosted(clean_env)
    assert settings.model_base_url == "https://openrouter.ai/api/v1"
    assert settings.model == "openai/gpt-6-luna"
    assert settings.llm_fallback_model == "openai/gpt-6-sol"
    assert FAKE_KEY not in repr(settings)


def test_openrouter_still_needs_the_remote_provider_gate(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    clean_env.setenv("AGENT_LLM_PROVIDER", "openrouter")
    with pytest.raises(ValueError, match="AGENT_ALLOW_REMOTE_PROVIDER"):
        AgentSettings.from_env()


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
            constrained_tool_routing=True,
            candidate_tools=["data_query_records"],
            model=settings.request_model,
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
    assert [t["function"]["name"] for t in body["tools"]] == ["data_query_records"]
    assert body["tools"][0] == catalog[0]
    for ollama_only in ("format", "options", "keep_alive", "think"):
        assert ollama_only not in body
    assert "response_format" not in body
    assert reply.tool_calls == [call]
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


def test_hosted_retry_turns_escalate_to_the_fallback_model(clean_env):
    from services.agent.orchestrator import AgentOrchestrator

    hosted = _hosted(clean_env)
    local = replace(hosted, llm_provider="ollama", llm_fallback_model=None)
    for settings, second in ((hosted, "openai/gpt-6-sol"), (local, "openai/gpt-6-luna")):
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        orchestrator.settings = settings
        assert orchestrator._turn_model(1, "openai/gpt-6-luna") == "openai/gpt-6-luna"
        assert orchestrator._turn_model(2, "openai/gpt-6-luna") == second


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
    clean_env.setenv("AGENT_JEV_BACKEND", "openrouter")
    assert AgentSettings.from_env().jev_model == "typesafe/jev-1.13-20260917"
    clean_env.setenv("AGENT_JEV_MODEL", "jev-latest")
    assert AgentSettings.from_env().jev_model == "jev-latest"
