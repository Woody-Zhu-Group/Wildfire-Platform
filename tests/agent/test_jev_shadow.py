"""Shadow-mode tests. No network. Jev must not change what ask() returns."""

from __future__ import annotations

import asyncio
import builtins
import importlib
import json
import os
import sys
import time
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from services.agent.config import AgentSettings
from services.agent.decisions.backend import Answer, DecisionResult, QuestionSpec
from services.agent.decisions.integrity import answers_equal, parse_raw_answers
from services.agent.decisions.mapping import RULE_TO_INTENT, routing_rule_ids
from services.agent.decisions.shadow import ShadowRunner
from services.agent.decisions.shadow_log import ShadowLog
from services.agent.eval.jev_metrics import accuracy, field_applies, labels_match
from services.agent.orchestrator import AgentOrchestrator
from services.agent.routing import route_question


def _settings(tmp_path, **overrides) -> AgentSettings:
    base = AgentSettings.from_env()
    values = {
        "jev_mode": "off",
        "jev_log_path": str(tmp_path / "jev_shadow.jsonl"),
        "jev_log_max_mb": 1,
        "jev_timeout_seconds": 3,
        "jev_sample_rate": 1,
        "jev_max_concurrency": 4,
        "jev_daily_call_cap": 5000,
    }
    values.update(overrides)
    return replace(base, **values)


def _runner(settings: AgentSettings, backend) -> ShadowRunner:
    log = ShadowLog(settings.jev_log_path, max_bytes=int(settings.jev_log_max_mb * 1024 * 1024))
    return ShadowRunner(settings, backend, log=log)


def _orchestrator(settings: AgentSettings, shadow=None) -> AgentOrchestrator:
    return AgentOrchestrator(settings, MagicMock(), MagicMock(), shadow=shadow)


def _strip(value):
    if isinstance(value, dict):
        return {
            key: _strip(item)
            for key, item in value.items()
            if key not in {"request_id", "timings_ms"}
        }
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def _wait(log: ShadowLog, predicate, timeout: float = 2.0):
    deadline = time.time() + timeout
    rows: list = []
    while time.time() < deadline:
        rows = log.read_records()
        if predicate(rows):
            return rows
        time.sleep(0.02)
    return rows


def _result(request_id: str, question_hash: str, **overrides) -> DecisionResult:
    payload = {
        "answers": {},
        "model_version": "jev-test",
        "latency_ms": 1.0,
        "input_tokens": 3,
        "raw": {"model": "jev-test", "answers": {}, "usage": {"input_tokens": 3}},
        "request_id": request_id,
        "question_hash": question_hash,
        "unexpected_option": False,
        "error": None,
    }
    payload.update(overrides)
    return DecisionResult(**payload)


def test_mode_off_never_imports_sdk_or_builds_runner(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_JEV_MODE", "off")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    imported: list[str] = []
    real_import = builtins.__import__

    def guard(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "typesafe_sdk" or name.startswith("typesafe_sdk."):
            imported.append(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guard)
    sys.modules.pop("typesafe_sdk", None)
    app_module = importlib.reload(importlib.import_module("services.agent.app"))

    async def _runtime(settings):
        return settings

    async def _context(self):
        return {}

    monkeypatch.setattr(app_module, "ensure_runtime_model", _runtime)
    monkeypatch.setattr(
        "services.agent.provider.OpenAICompatibleProvider.ensure_context_loaded",
        _context,
    )

    async def _run():
        async with app_module.lifespan(app_module.app):
            assert app_module.orchestrator is not None
            assert app_module.orchestrator.shadow is None
            return await app_module.orchestrator.ask("What fires are near me?")

    result = asyncio.run(_run())
    assert result.response["status"] == "clarification"
    assert imported == []
    assert "typesafe_sdk" not in sys.modules
    from services.agent.decisions import shadow as shadow_mod

    assert shadow_mod._RUNNER is None


def test_shadow_exception_does_not_change_response(tmp_path):
    class Boom:
        name = "boom"

        def evaluate(self, state, questions, *, request_id, question_hash):
            raise RuntimeError("boom")

    off = _settings(tmp_path)
    shadow_settings = _settings(
        tmp_path / "shadow",
        jev_mode="shadow",
        jev_log_path=str(tmp_path / "shadow" / "jev.jsonl"),
    )
    (tmp_path / "shadow").mkdir()
    runner = _runner(shadow_settings, Boom())
    question = "What fires are near me?"
    off_response = asyncio.run(_orchestrator(off).ask(question)).response
    shadow_response = asyncio.run(
        _orchestrator(shadow_settings, shadow=runner).ask(question)
    ).response
    assert _strip(off_response) == _strip(shadow_response)


def test_slow_backend_does_not_block_ask_and_logs_timeout(tmp_path):
    class Sleeper:
        name = "sleeper"

        def evaluate(self, state, questions, *, request_id, question_hash):
            time.sleep(5)
            return _result(request_id, question_hash)

    settings = _settings(tmp_path, jev_mode="shadow", jev_timeout_seconds=0.2)
    runner = _runner(settings, Sleeper())
    started = time.perf_counter()
    asyncio.run(
        _orchestrator(settings, shadow=runner).ask("What fires are near me?")
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0
    rows = _wait(
        runner.log,
        lambda found: any("TimeoutError" in str(row.get("error")) for row in found),
        timeout=2,
    )
    assert any(row.get("error") and "TimeoutError" in row["error"] for row in rows)
    assert any(row.get("jev") is None for row in rows if row.get("type") == "routing")


def test_saturated_pool_drops_without_blocking(tmp_path):
    started = threading_event()

    class Blocker:
        name = "blocker"

        def evaluate(self, state, questions, *, request_id, question_hash):
            started.set()
            time.sleep(0.4)
            return _result(request_id, question_hash)

    settings = _settings(tmp_path, jev_max_concurrency=1, jev_timeout_seconds=2)
    runner = _runner(settings, Blocker())
    decision = route_question("How many PG&E ignitions in 2024?")
    t0 = time.perf_counter()
    runner.submit_routing("a", "How many PG&E ignitions in 2024?", decision, "2026-09-21", forced=False)
    assert started.wait(1)
    runner.submit_routing("b", "How many PG&E ignitions in 2023?", decision, "2026-09-21", forced=False)
    assert time.perf_counter() - t0 < 0.5
    assert runner.dropped == 1


def threading_event():
    import threading

    return threading.Event()


def test_daily_cap_blocks_further_calls(tmp_path):
    class Counter:
        name = "counter"
        calls = 0

        def evaluate(self, state, questions, *, request_id, question_hash):
            self.calls += 1
            return _result(request_id, question_hash)

    backend = Counter()
    settings = _settings(tmp_path, jev_daily_call_cap=1, jev_max_concurrency=2)
    runner = _runner(settings, backend)
    decision = route_question("How many PG&E ignitions in 2024?")
    runner.submit_routing("a", "one", decision, "2026-09-21", forced=False)
    runner.submit_routing("b", "two", decision, "2026-09-21", forced=False)
    rows = _wait(runner.log, lambda found: any(row.get("reason") == "daily_cap" for row in found))
    deadline = time.time() + 2
    while backend.calls < 3 and time.time() < deadline:
        time.sleep(0.02)
    # The cap counts questions. The admitted question makes three v3 calls.
    assert backend.calls == 3
    assert runner._calls_today == 1
    assert runner.cap_blocked == 1
    assert any(row.get("reason") == "daily_cap" for row in rows)


def test_missing_api_key_warns_once_and_does_not_call(monkeypatch, caplog):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    from services.agent.decisions.typesafe_backend import TypeSafeBackend, reset_for_tests

    reset_for_tests()
    backend = TypeSafeBackend()
    with caplog.at_level("WARNING"):
        first = backend.evaluate(
            "state",
            {},
            request_id="r",
            question_hash="h",
        )
        second = backend.evaluate(
            "state",
            {},
            request_id="r2",
            question_hash="h2",
        )
    assert first is None and second is None
    assert backend.calls == 0
    warnings = [rec.message for rec in caplog.records if "TYPESAFE_API_KEY" in rec.message]
    assert len(warnings) == 1
    reset_for_tests()


def test_rule_to_intent_covers_routing_py():
    missing = routing_rule_ids() - set(RULE_TO_INTENT)
    assert not missing


def test_log_records_are_json_and_hide_the_api_key(monkeypatch, tmp_path):
    key = "super-secret-typesafe-key"
    monkeypatch.setenv("TYPESAFE_API_KEY", key)

    class Echo:
        name = "echo"

        def evaluate(self, state, questions, *, request_id, question_hash):
            answer = Answer(
                kind="choice",
                value="clarify",
                confidence=0.8,
                probabilities={"clarify": 0.8, "answer": 0.2},
            )
            raw = {
                "model": "jev-test",
                "answers": {
                    "disposition": {
                        "type": "choice",
                        "choice": "clarify",
                        "confidence": 0.8,
                        "probabilities": {"clarify": 0.8, "answer": 0.2},
                    }
                },
                "usage": {"input_tokens": 4},
            }
            return DecisionResult(
                answers={"disposition": answer},
                model_version="jev-test",
                latency_ms=2,
                input_tokens=4,
                raw=raw,
                request_id=request_id,
                question_hash=question_hash,
            )

    settings = _settings(tmp_path, jev_mode="shadow")
    runner = _runner(settings, Echo())
    question = "What fires are near me?"
    asyncio.run(_orchestrator(settings, shadow=runner).ask(question))
    rows = _wait(runner.log, lambda found: any(row.get("type") == "routing" for row in found))
    routing = next(row for row in rows if row.get("type") == "routing")
    required = {
        "type",
        "schema_version",
        "request_id",
        "ts",
        "question",
        "question_hash",
        "forced",
        "regex",
        "request_payload",
        "jev",
        "error",
    }
    assert required <= set(routing)
    text = (tmp_path / "jev_shadow.jsonl").read_text(encoding="utf-8")
    assert key not in text
    runner.log.write({"type": "routing", "error": f"leaked {key}"})
    assert key not in (tmp_path / "jev_shadow.jsonl").read_text(encoding="utf-8")


def test_log_rotation(tmp_path):
    log = ShadowLog(str(tmp_path / "jev_shadow.jsonl"), max_bytes=180, backups=5)
    for index in range(8):
        log.write({"type": "dropped", "n": index, "pad": "x" * 40})
    assert (tmp_path / "jev_shadow.jsonl").exists()
    assert (tmp_path / "jev_shadow.jsonl.1").exists()
    kept = list(tmp_path.glob("jev_shadow.jsonl*"))
    assert len(kept) <= 6


def test_parse_round_trip_for_all_three_types():
    raw = {
        "model": "jev-1.13.0",
        "answers": {
            "tone": {
                "type": "choice",
                "choice": "angry",
                "confidence": 0.8,
                "probabilities": {"angry": 0.8, "calm": 0.2},
            },
            "urgent": {"type": "noul", "noul": 0.25},
            "harm": {
                "type": "score",
                "score": 1.2,
                "confidence": 0.7,
                "legend": {"0": "low", "1": "mid", "2": "high"},
                "probabilities": {"0": 0.1, "1": 0.6, "2": 0.3},
            },
        },
        "usage": {"input_tokens": 12, "output_tokens": 4},
    }
    specs = {
        "tone": QuestionSpec("choice", "Tone?", {"angry": "Upset.", "calm": "Even."}),
        "urgent": QuestionSpec("noul", "It is urgent."),
        "harm": QuestionSpec("score", "Harm?", ["low", "mid", "high"]),
    }
    first, unexpected = parse_raw_answers(raw, specs)
    second, _ = parse_raw_answers(raw, specs)
    assert not unexpected
    assert answers_equal(first, second)
    assert first["tone"].value == "angry"
    assert first["tone"].probabilities == {"angry": 0.8, "calm": 0.2}
    assert first["urgent"].value == 0.25
    assert first["urgent"].confidence is None
    assert first["harm"].value == 1.2


def test_unexpected_option_is_kept_verbatim():
    raw = {
        "model": "jev-test",
        "answers": {
            "tone": {
                "type": "choice",
                "choice": "not_in_schema",
                "confidence": 0.4,
                "probabilities": {"angry": 0.2, "not_in_schema": 0.8},
            }
        },
    }
    specs = {
        "tone": QuestionSpec("choice", "Tone?", {"angry": "Upset.", "calm": "Even."})
    }
    parsed, unexpected = parse_raw_answers(raw, specs)
    assert unexpected is True
    assert parsed["tone"].value == "not_in_schema"


def test_swapped_response_is_a_wiring_error(tmp_path):
    class Swap:
        name = "swap"

        def evaluate(self, state, questions, *, request_id, question_hash):
            return _result("other-id", "other-hash")

    settings = _settings(tmp_path, jev_timeout_seconds=1)
    runner = _runner(settings, Swap())
    decision = route_question("What fires are near me?")
    runner.submit_routing("job-1", "What fires are near me?", decision, "2026-09-21", forced=False)
    rows = _wait(runner.log, lambda found: any(row.get("type") == "wiring_error" for row in found))
    assert any(row.get("type") == "wiring_error" for row in rows)
    assert not any(row.get("type") == "routing" for row in rows)


def test_null_jev_is_excluded_from_accuracy():
    correct, total = accuracy([("answer", None), ("answer", "answer")])
    assert (correct, total) == (1, 1)


def test_clarify_reason_does_not_score_when_disposition_is_answer():
    expected = {"disposition": "answer", "clarify_reason": "not_applicable", "intent": "count"}
    assert field_applies("clarify_reason", expected) is False
    assert field_applies(
        "clarify_reason",
        {"disposition": "clarify", "clarify_reason": "missing_location"},
    ) is True
    assert field_applies(
        "clarify_reason",
        {"disposition": "answer", "clarify_reason": "map_missing_year"},
    ) is False
    assert field_applies(
        "clarify_reason",
        {"disposition": "unsupported", "clarify_reason": "missing_location"},
    ) is False
    assert field_applies("intent", {"disposition": "clarify", "intent": "other"}) is False
    assert field_applies("tool_pick", {"disposition": "unsupported", "tool_pick": "unsupported"}) is False
    assert field_applies("dataset", {"disposition": "clarify", "dataset": "cpuc_ignitions"}) is False
    assert field_applies(
        "unsupported_topic",
        {
            "disposition": "unsupported",
            "unsupported_topic": "unsupported_cost",
            "acceptable_outcomes": [
                {"disposition": "unsupported", "unsupported_topic": "unexpressable_county_filter"},
                {"disposition": "answer", "dataset": "calfire_incidents"},
            ],
        },
    ) is False
    assert labels_match(
        ["unsupported_cost", "unsupported_optimization"],
        "unsupported_optimization",
    )
    rows = [(expected, "ambiguous_risk_metric")]
    pairs = [
        (item.get("clarify_reason"), actual)
        for item, actual in rows
        if field_applies("clarify_reason", item)
    ]
    assert accuracy(pairs) == (0, 0)


def test_unmapped_rule_is_null_not_other():
    from services.agent.decisions.mapping import regex_labels
    from services.agent.routing import RouteDecision

    unknown = regex_labels(RouteDecision("model", "not_a_real_rule", "x"))
    assert unknown["intent"] is None
    assert unknown["unmapped_rule"] is True
    disabled = regex_labels(
        RouteDecision("model", "deterministic_router_disabled", "x")
    )
    assert disabled["intent"] is None
    assert disabled["unmapped_rule"] is False


def test_fault_cases_use_the_question_not_the_error_status():
    from services.agent.eval.jev_offline_eval import CASES_FILE, derive_case_labels, load_cases

    by_id = {case["id"]: case for case in load_cases(CASES_FILE)}
    retry = derive_case_labels(by_id["schema_retry_bound_persistent"])
    assert retry["disposition"] == "answer"
    assert retry["intent"] == "count"
    assert retry["tool_pick"] == "data_query_records"
    partial = derive_case_labels(by_id["detect_partial_200"])
    assert partial["disposition"] == "answer"
    assert partial["intent"] == "map"
    bounded = derive_case_labels(by_id["model_synthesis_bounded"])
    assert bounded["disposition"] == "answer"
    assert bounded["intent"] == "count"
    recover = derive_case_labels(by_id["recover_503"])
    assert recover["disposition"] == "answer"
    assert recover["intent"] == "trend"


def test_named_rows_match_the_router():
    from services.agent.eval.jev_offline_eval import CASES_FILE, derive_case_labels, load_cases

    by_id = {case["id"]: case for case in load_cases(CASES_FILE)}
    ranking = derive_case_labels(by_id["unsupported_ranking_circuit_most"])
    assert ranking["disposition"] == "answer"
    assert ranking["intent"] == "rank"
    assert ranking["tool_pick"] == "data_query_rank"
    assert ranking["unsupported_topic"] is None
    place = derive_case_labels(by_id["utility_not_invented_from_place"])
    assert place["disposition"] == "unsupported"
    assert place["intent"] is None
    assert place["tool_pick"] is None
    assert place["unsupported_topic"] == "unexpressable_county_filter"
    collision = derive_case_labels(by_id["collision_count_inside_sce_territory"])
    assert collision["intent"] == "spatial_context"
    assert collision["tool_pick"] == "data_query_spatial"
    near = derive_case_labels(by_id["ambiguous_near_me"])
    assert near["intent"] is None
    assert near["clarify_reason"] == "missing_location"
    cpz = derive_case_labels(by_id["unsupported_cpz"])
    assert cpz["intent"] is None
    assert cpz["unsupported_topic"] == "unsupported_cpz"
    coverage = derive_case_labels(by_id["risk_out_of_coverage"])
    assert coverage["disposition"] == "clarify"
    assert coverage["intent"] is None
    assert coverage["tool_pick"] is None
    assert coverage["clarify_reason"] == "risk_future_date"


def test_shadow_matches_off_on_deterministic_and_model_paths(tmp_path):
    from services.agent.provider import ModelReply
    from services.agent.tools import ToolExecution

    class CountExecutor:
        async def execute(self, tool, arguments, **kwargs):
            if tool == "data_query_spatial":
                summary = {"kind": "summary", "counts": {"ignitions": 4}}
            else:
                summary = {
                    "dataset": arguments.get("dataset") or "cpuc_ignitions",
                    "result_mode": "count",
                    "total": 3,
                    "returned": 3,
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

    class OneToolProvider:
        def __init__(self):
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "data_query_records",
                                "arguments": (
                                    '{"dataset":"cpuc_ignitions","result_mode":"count","year":2024}'
                                ),
                            },
                        }
                    ],
                    raw={"choices": [{"finish_reason": "tool_calls"}]},
                    latency_ms=1.0,
                    usage={},
                )
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

    class Echo:
        name = "echo"

        def evaluate(self, state, questions, *, request_id, question_hash):
            return _result(request_id, question_hash)

    off = _settings(tmp_path / "off")
    (tmp_path / "off").mkdir()
    shadow_dir = tmp_path / "on"
    shadow_dir.mkdir()
    shadow_settings = _settings(
        shadow_dir,
        jev_mode="shadow",
        jev_log_path=str(shadow_dir / "jev.jsonl"),
    )
    runner = _runner(shadow_settings, Echo())
    count_question = "How many PG&E utility-attributed ignitions were there in 2024?"
    off_count = asyncio.run(
        AgentOrchestrator(off, MagicMock(), CountExecutor()).ask(count_question)
    ).response
    on_count = asyncio.run(
        AgentOrchestrator(
            shadow_settings, MagicMock(), CountExecutor(), shadow=runner
        ).ask(count_question)
    ).response
    assert off_count["status"] == "answer"
    assert _strip(off_count) == _strip(on_count)

    model_question = "Tell me about CPUC ignitions in 2023"
    off_model = asyncio.run(
        AgentOrchestrator(off, OneToolProvider(), CountExecutor()).ask(
            model_question, force_model=True
        )
    ).response
    on_model = asyncio.run(
        AgentOrchestrator(
            shadow_settings, OneToolProvider(), CountExecutor(), shadow=runner
        ).ask(model_question, force_model=True)
    ).response
    assert off_model["route"]["path"] == "model"
    assert off_model["status"] == "answer"
    assert _strip(off_model) == _strip(on_model)


def test_policy_sentences_cover_every_clarify_and_refuse_rule():
    from services.agent.decisions.mapping import policy_rule_ids
    from services.agent.decisions.schemas import (
        CONTEXT_DEFERRED_RULES,
        DOMAIN_CONTEXT,
        POLICY_SENTENCES,
    )

    missing = policy_rule_ids() - set(POLICY_SENTENCES) - set(CONTEXT_DEFERRED_RULES)
    assert not missing
    assert not set(CONTEXT_DEFERRED_RULES) & set(POLICY_SENTENCES)
    for rule_id, sentence in POLICY_SENTENCES.items():
        assert sentence in DOMAIN_CONTEXT
        assert sentence.strip()
    from services.agent.decisions.schemas import context_for_call

    topic = context_for_call("topic")
    facts = context_for_call("facts")
    places = context_for_call("places")
    tool_pick = context_for_call("tool_pick")
    for key in (
        "medical_exposure_missing_year",
        "series_mode_missing_year",
        "series_mode_missing_dataset",
    ):
        sentence = POLICY_SENTENCES[key]
        assert sentence in topic
        assert sentence not in facts
        assert sentence not in places
        if key in {
            "medical_exposure_missing_year",
            "series_mode_missing_year",
            "series_mode_missing_dataset",
        }:
            assert sentence not in tool_pick


def test_api_key_whitespace_is_stripped_without_logging_the_value(monkeypatch, caplog):
    monkeypatch.setenv("TYPESAFE_API_KEY", "  whitespace-test-token  ")
    from services.agent.decisions.typesafe_backend import prepared_api_key, reset_for_tests

    reset_for_tests()
    with caplog.at_level("WARNING"):
        value = prepared_api_key()
    assert value == "whitespace-test-token"
    assert os.environ["TYPESAFE_API_KEY"] == "whitespace-test-token"
    warnings = [rec.message for rec in caplog.records]
    assert any("whitespace" in message for message in warnings)
    assert all("whitespace-test-token" not in message for message in warnings)
    reset_for_tests()
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    from services.agent.eval.jev_offline_eval import load_cases, CASES_FILE, derive_case_labels

    cases = load_cases(CASES_FILE)
    assert cases
    labeled = [derive_case_labels(case) for case in cases]
    assert all("disposition" in row for row in labeled)
    assert json.dumps(labeled)
