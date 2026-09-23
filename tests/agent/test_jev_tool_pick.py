"""Jev tool_pick mode. Qwen still writes the answer. Default stays off."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from services.agent.config import AgentSettings
from services.agent.decisions.tool_pick_mode import ToolPickDecision
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import ModelReply
from services.agent.tools import ToolExecution


QUESTION = "Tell me about CPUC ignitions in 2023"


def _settings(tmp_path, **overrides) -> AgentSettings:
    base = AgentSettings.from_env()
    values = {
        "jev_mode": "tool_pick",
        "jev_log_path": str(tmp_path / "jev.jsonl"),
        "jev_tool_pick_min_confidence": 0.8,
    }
    values.update(overrides)
    return replace(base, **values)


class _Executor:
    def __init__(self) -> None:
        self.tools: list[str] = []

    async def execute(self, tool, arguments, **kwargs):
        self.tools.append(tool)
        return ToolExecution(
            tool=tool,
            arguments=arguments,
            ok=True,
            summary={
                "kind": "summary",
                "result_mode": arguments.get("result_mode", "count"),
                "dataset": arguments.get("dataset"),
                "total": 3,
                "counts": {"ignitions": 3},
            },
            raw={},
            error=None,
            artifact=None,
            latency_ms=1.0,
            evidence_id="evidence_test",
        )

    async def close(self) -> None:
        return None


class _Provider:
    def __init__(self) -> None:
        self.phases: list[str | None] = []

    async def complete(self, **kwargs):
        phase = kwargs.get("phase")
        self.phases.append(phase)
        if phase != "synthesis":
            raise AssertionError("qwen was asked to pick a tool")
        return ModelReply(
            content=(
                '{"status":"answer","answer":"3 ignitions.",'
                '"claims":[{"text":"3 ignitions","evidence_ids":["evidence_test"]}]}'
            ),
            tool_calls=[],
            raw={"choices": [{"finish_reason": "stop"}]},
            latency_ms=1.0,
            usage={},
        )


def _decision(**kwargs) -> ToolPickDecision:
    base = dict(tool="data_query_records", confidence=0.91, path="jev", reason="above_threshold", latency_ms=12.0)
    base.update(kwargs)
    return ToolPickDecision(**base)


def test_high_confidence_jev_picks_the_tool_and_qwen_only_synthesizes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.decide_tool_pick",
        lambda *args, **kwargs: _decision(),
    )
    provider = _Provider()
    executor = _Executor()
    result = asyncio.run(
        AgentOrchestrator(_settings(tmp_path), provider, executor).ask(QUESTION)
    )
    assert executor.tools[0] == "data_query_records"
    assert provider.phases == ["synthesis"]
    assert result.response["status"] == "answer"
    log = (tmp_path / "jev.jsonl").read_text(encoding="utf-8")
    record = json.loads(log.splitlines()[-1])
    assert record["path"] == "jev"
    assert record["confidence"] == 0.91


def test_low_confidence_falls_back_to_qwen(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.decide_tool_pick",
        lambda *args, **kwargs: _decision(confidence=0.4, path="qwen", reason="below_threshold"),
    )
    provider = _Provider()
    calls = []

    async def routing_complete(**kwargs):
        calls.append(kwargs.get("phase"))
        if kwargs.get("phase") == "synthesis":
            return await _Provider().complete(**kwargs)
        return ModelReply(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "data_query_records",
                        "arguments": '{"dataset":"cpuc_ignitions","result_mode":"count","year":2023}',
                    },
                }
            ],
            raw={"choices": [{"finish_reason": "tool_calls"}]},
            latency_ms=1.0,
            usage={},
        )

    provider.complete = routing_complete
    asyncio.run(AgentOrchestrator(_settings(tmp_path), provider, _Executor()).ask(QUESTION))
    assert None in calls
    record = json.loads((tmp_path / "jev.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["path"] == "qwen"
    assert record["reason"] == "below_threshold"


def test_jev_error_falls_back_to_qwen(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.decide_tool_pick",
        lambda *args, **kwargs: _decision(tool=None, confidence=None, path="qwen", reason="timeout", error="TimeoutError"),
    )
    seen = []

    class Provider(_Provider):
        async def complete(self, **kwargs):
            seen.append(kwargs.get("phase"))
            if kwargs.get("phase") != "synthesis":
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "data_query_records",
                                "arguments": '{"dataset":"cpuc_ignitions","result_mode":"count","year":2023}',
                            },
                        }
                    ],
                    raw={},
                    latency_ms=1.0,
                    usage={},
                )
            return await _Provider().complete(**kwargs)

    asyncio.run(AgentOrchestrator(_settings(tmp_path), Provider(), _Executor()).ask(QUESTION))
    assert None in seen
    record = json.loads((tmp_path / "jev.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["reason"] == "timeout"


def test_off_mode_does_not_ask_jev(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("Jev was called")

    monkeypatch.setattr("services.agent.decisions.tool_pick_mode.decide_tool_pick", boom)

    class Provider:
        async def complete(self, **kwargs):
            if kwargs.get("phase") != "synthesis":
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "data_query_records",
                                "arguments": '{"dataset":"cpuc_ignitions","result_mode":"count","year":2023}',
                            },
                        }
                    ],
                    raw={},
                    latency_ms=1.0,
                    usage={},
                )
            return ModelReply(
                content=(
                    '{"status":"answer","answer":"3 ignitions.",'
                    '"claims":[{"text":"3 ignitions","evidence_ids":["evidence_test"]}]}'
                ),
                tool_calls=[],
                raw={},
                latency_ms=1.0,
                usage={},
            )

    result = asyncio.run(
        AgentOrchestrator(
            _settings(tmp_path, jev_mode="off"), Provider(), _Executor()
        ).ask(QUESTION)
    )
    assert result.response["status"] == "answer"


def test_count_plus_trend_uses_qwen(tmp_path, monkeypatch):
    _assert_two_part_uses_qwen(
        tmp_path,
        monkeypatch,
        "Give me the PGE ignition count and its monthly trend for 2024.",
    )


def test_holdout_count_trend_uses_qwen(tmp_path, monkeypatch):
    _assert_two_part_uses_qwen(
        tmp_path,
        monkeypatch,
        "Give me the SCE ignition count and its weekly trend for 2023.",
    )


def _assert_two_part_uses_qwen(tmp_path, monkeypatch, question: str) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("Jev picked a tool for a two-part question")

    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.decide_tool_pick", boom
    )
    phases: list[str | None] = []

    class Provider:
        async def complete(self, **kwargs):
            phases.append(kwargs.get("phase"))
            if kwargs.get("phase") != "synthesis":
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "data_query_records",
                                "arguments": '{"dataset":"cpuc_ignitions","result_mode":"count","year":2024}',
                            },
                        }
                    ],
                    raw={},
                    latency_ms=1.0,
                    usage={},
                )
            return ModelReply(
                content=(
                    '{"status":"answer","answer":"3 ignitions.",'
                    '"claims":[{"text":"3 ignitions","evidence_ids":["evidence_test"]}]}'
                ),
                tool_calls=[],
                raw={},
                latency_ms=1.0,
                usage={},
            )

    result = asyncio.run(
        AgentOrchestrator(_settings(tmp_path), Provider(), _Executor()).ask(
            question, force_model=True
        )
    )
    assert None in phases
    assert result.response["status"] == "answer"
    record = json.loads((tmp_path / "jev.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["path"] == "qwen"
    assert record["reason"] == "multiple_primary_tools"


def test_template_count_skips_qwen(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.decide_tool_pick",
        lambda *args, **kwargs: _decision(confidence=0.86),
    )
    monkeypatch.setattr(
        "services.agent.orchestrator.collect_qualifications",
        _no_caveats,
    )

    class Provider:
        async def complete(self, **kwargs):
            raise AssertionError("qwen was called for a count template")

    result = asyncio.run(
        AgentOrchestrator(
            _settings(tmp_path, jev_mode="tool_pick_template"),
            Provider(),
            _Executor(),
        ).ask("How many EPSS outages occurred in 2024?", force_model=True)
    )
    assert result.response["status"] == "answer"
    assert "count:" in result.response["answer_text"]
    assert result.response["route"]["answer_origin"] == "model"


def test_template_skips_explanation_comparison_and_synthesizes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.decide_tool_pick",
        lambda *args, **kwargs: _decision(tool="comparison_run", confidence=0.90),
    )
    phases: list[str | None] = []

    class Provider:
        async def complete(self, **kwargs):
            phases.append(kwargs.get("phase"))
            return ModelReply(
                content=(
                    '{"status":"answer","answer":"PGE had more than SCE.",'
                    '"claims":[{"text":"PGE had more than SCE.","evidence_ids":["evidence_test"]}]}'
                ),
                tool_calls=[],
                raw={},
                latency_ms=1.0,
                usage={},
            )

    class Executor(_Executor):
        async def execute(self, tool, arguments, **kwargs):
            self.tools.append(tool)
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=True,
                summary={
                    "kind": "utilities",
                    "metric": "ignition_count",
                    "results": [
                        {"key": "PGE", "value": 10},
                        {"key": "SCE", "value": 4},
                    ],
                },
                raw={},
                error=None,
                artifact=None,
                latency_ms=1.0,
                evidence_id="evidence_test",
            )

    question = (
        "Compare ignition counts for PGE versus SCE in one shared year, 2024 "
        "and explain the difference"
    )
    asyncio.run(
        AgentOrchestrator(
            _settings(tmp_path, jev_mode="tool_pick_template"),
            Provider(),
            Executor(),
        ).ask(question, force_model=True)
    )
    assert phases == ["synthesis"]


def test_plain_comparison_uses_the_template(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.decide_tool_pick",
        lambda *args, **kwargs: _decision(tool="comparison_run", confidence=0.90),
    )
    monkeypatch.setattr(
        "services.agent.orchestrator.collect_qualifications",
        _no_caveats,
    )

    class Provider:
        async def complete(self, **kwargs):
            raise AssertionError("qwen was called for a plain comparison")

    class Executor(_Executor):
        async def execute(self, tool, arguments, **kwargs):
            self.tools.append(tool)
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=True,
                summary={
                    "kind": "utilities",
                    "metric": "ignition_count",
                    "results": [
                        {"key": "PGE", "value": 10},
                        {"key": "SCE", "value": 4},
                    ],
                },
                raw={},
                error=None,
                artifact=None,
                latency_ms=1.0,
                evidence_id="evidence_test",
            )

    result = asyncio.run(
        AgentOrchestrator(
            _settings(tmp_path, jev_mode="tool_pick_template"),
            Provider(),
            Executor(),
        ).ask(
            "Compare ignition counts for PGE versus SCE in one shared year, 2024",
            force_model=True,
        )
    )
    assert "comparison:" in result.response["answer_text"]


def test_live_tool_pick_payload_matches_the_offline_hybrid_call():
    from datetime import date

    from services.agent.decisions.canonical import payload_hash
    from services.agent.decisions.schemas import context_for_call
    from services.agent.decisions.shadow import _payload
    from services.agent.decisions.v3 import calls_for_config, tool_pick_call
    from services.agent.routing import candidate_tools

    # The offline builder reads the wall clock, so use the same day on both sides.
    today = date.today().isoformat()
    questions = [
        "How many EPSS outages occurred in 2024?",
        "Show a weekly CPUC ignition time series for 2024.",
        "How many US ignition sample events occurred in 2024?",
    ]
    for question in questions:
        tools = candidate_tools(question)
        live = tool_pick_call(question, today, tools, "v3_hybrid")
        offline = next(
            call
            for call in calls_for_config(question, today, tools, "v3_hybrid")
            if call["name"] == "tool_pick"
        )
        # Since PR 26 the tool pick call carries the tool_pick policy subset,
        # not the full DOMAIN_CONTEXT; live and offline must still agree.
        assert live["state"]["glossary"] == context_for_call("tool_pick")
        assert live["state"]["glossary"] == offline["state"]["glossary"]
        assert live["state"]["question"] == question
        assert live["state"]["today"] == today
        assert payload_hash(_payload(live["state"], live["questions"], "jev-latest")) == (
            payload_hash(_payload(offline["state"], offline["questions"], "jev-latest"))
        )


def _jev_wire_bodies(backend_name: str, call: dict, monkeypatch) -> tuple[list[dict], list[str]]:
    """Send one v3 call through a backend on a mock transport; return wire bodies and URLs."""
    import httpx2

    from services.agent.decisions.typesafe_backend import make_backend, reset_for_tests

    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    urls: list[str] = []

    def reply(request):  # type: ignore[no-untyped-def]
        urls.append(str(request.url))
        answers = {}
        for name, spec in call["questions"].items():
            if spec.kind == "choice":
                first = next(iter(spec.criteria))
                answers[name] = {"type": "choice", "choice": first, "confidence": 0.9, "probabilities": {first: 0.9}}
            else:
                answers[name] = {"type": "noul", "noul": 0.9}
        return httpx2.Response(
            200,
            json={"model": "jev-test", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 1}},
        )

    reset_for_tests()
    backend = make_backend(backend_name, model="jev-latest", timeout_seconds=3)
    backend._transport = httpx2.MockTransport(reply)
    captured: list[bytes] = []
    result = backend.evaluate(
        call["state"],
        call["questions"],
        request_id="parity",
        question_hash="parity",
        capture=captured,
    )
    reset_for_tests()
    assert result is not None and result.error is None, result and result.error
    assert result.extra["backend"] == backend_name
    return [json.loads(body) for body in captured], urls


def test_openrouter_jev_request_carries_the_same_state_questions_and_options(monkeypatch):
    """Both backends must put identical state, questions, and options on the wire."""
    from services.agent.decisions.canonical import payload_hash
    from services.agent.decisions.shadow import _payload
    from services.agent.decisions.v3 import calls_for_config
    from services.agent.routing import candidate_tools

    today = "2026-09-22"
    questions = [
        "How many EPSS outages occurred in 2024?",
        "Show a weekly CPUC ignition time series for 2024.",
        "How many ignitions were there?",
    ]
    for question in questions:
        for call in calls_for_config(question, today, candidate_tools(question), "v3_hybrid"):
            typesafe, ts_urls = _jev_wire_bodies("typesafe", call, monkeypatch)
            openrouter, or_urls = _jev_wire_bodies("openrouter", call, monkeypatch)
            assert ts_urls == ["https://api.typesafe.ai/v1/systemone"]
            assert or_urls == ["https://openrouter.ai/api/v1/systemone"]
            assert len(typesafe) == len(openrouter) == 1
            ts_body, or_body = typesafe[0], openrouter[0]
            assert or_body["state"] == ts_body["state"] == call["state"], call["name"]
            assert or_body["questions"] == ts_body["questions"], call["name"]
            # Options are the criteria inside each question; compare them explicitly too.
            for name in ts_body["questions"]:
                assert or_body["questions"][name].get("criteria") == ts_body["questions"][name].get("criteria")
            # Same model on both sides means the whole body, not just parts, must match,
            # and it must match the payload the shadow log hashes.
            assert or_body == ts_body
            expected = _payload(call["state"], call["questions"], "jev-latest")
            assert payload_hash(or_body) == payload_hash(expected)


def test_count_plus_trend_runs_both_tools_without_a_model():
    from services.agent.routing import route_question

    for question, interval in (
        ("Give me the PGE ignition count and its monthly trend for 2024.", "monthly"),
        ("Give me the SCE ignition count and its weekly trend for 2023.", "weekly"),
    ):
        decision = route_question(question)
        assert decision.path == "deterministic"
        assert decision.rule == "multi_intent_count_and_trend"
        assert [name for name, _ in decision.tool_calls] == [
            "data_query_records",
            "visualization_create",
        ]
        assert decision.tool_calls[1][1]["interval"] == interval


def test_count_plus_trend_ask_does_not_call_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.agent.orchestrator.collect_qualifications",
        _no_caveats,
    )

    class Provider:
        async def complete(self, **kwargs):
            raise AssertionError("model was called")

    class Executor:
        def __init__(self) -> None:
            self.tools: list[str] = []

        async def execute(self, tool, arguments, **kwargs):
            self.tools.append(tool)
            if tool == "visualization_create":
                summary = {
                    "kind": "time_series",
                    "dataset": arguments.get("dataset"),
                    "interval": arguments.get("interval"),
                    "total_events": 12,
                }
            else:
                summary = {
                    "kind": "summary",
                    "result_mode": "count",
                    "dataset": arguments.get("dataset"),
                    "total": 3,
                }
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=True,
                summary=summary,
                raw={},
                error=None,
                artifact=None,
                latency_ms=1.0,
                evidence_id="evidence_test",
            )

        async def close(self) -> None:
            return None

    executor = Executor()
    result = asyncio.run(
        AgentOrchestrator(
            _settings(tmp_path, jev_mode="off"), Provider(), executor
        ).ask("Give me the SCE ignition count and its weekly trend for 2023.")
    )
    assert executor.tools == ["data_query_records", "visualization_create"]
    assert "count:" in result.response["answer_text"]
    assert "time series" in result.response["answer_text"]
    assert result.response["route"]["answer_origin"] == "deterministic"


async def _no_caveats(*args, **kwargs):
    return [], [], None


def _slots(question: str) -> dict:
    from services.agent.routing import route_question

    return route_question(question, force_model=True).slots


def test_visualization_and_comparison_fill_from_route_slots():
    from services.agent.decisions.tool_pick_mode import arguments_for_tool

    weekly = _slots("Show a weekly CPUC ignition time series for 2024.")
    assert arguments_for_tool("visualization_create", weekly, "Show a weekly CPUC ignition time series for 2024.") == {
        "kind": "time_series",
        "dataset": "ignitions",
        "year": 2024,
        "interval": "weekly",
    }
    mapped = _slots("Map the 2024 US ignition sample.")
    assert arguments_for_tool("visualization_create", mapped, "Map the 2024 US ignition sample.")["kind"] == "map"
    compare = "Compare ignition counts for PGE versus SCE in one shared year, 2024"
    args = arguments_for_tool("comparison_run", _slots(compare), compare)
    assert args["kind"] == "utilities"
    assert args["utilities"] == ["PGE", "SCE"]
    assert args["metric"] == "ignition_count"
    spatial = _slots("How many ignitions were spatially inside PG&E territory in 2024?")
    assert arguments_for_tool(
        "data_query_spatial",
        spatial,
        "How many ignitions were spatially inside PG&E territory in 2024?",
    )["kind"] == "summary"


def test_missing_required_slot_does_not_guess():
    from services.agent.decisions.tool_pick_mode import arguments_for_tool

    vague = _slots("Tell me about wildfire incidents in Sacramento County during 2024")
    assert arguments_for_tool(
        "data_query_records",
        vague,
        "Tell me about wildfire incidents in Sacramento County during 2024",
    ) is None
    trend = "Give me the PGE ignition count and its monthly trend for 2024."
    assert arguments_for_tool("visualization_create", _slots(trend), trend)["interval"] == "monthly"
    assert arguments_for_tool(
        "visualization_create",
        _slots("Show CPUC ignitions in 2024."),
        "Show CPUC ignitions in 2024.",
    ) is None
