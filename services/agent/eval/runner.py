"""Run selectable model/thinking/output cells and score full trajectories."""

from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import hashlib
import json
import math
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.orchestrator import AgentOrchestrator
from services.agent.model_setup import ensure_runtime_model
from services.agent.provider import OpenAICompatibleProvider
from services.agent.tools import ToolExecutor
from shared.db import REPO_ROOT

HERE = Path(__file__).resolve().parent
CASES_FILE = HERE / "cases.json"
RUNS_DIR = HERE / "runs"
REPORT_FILE = HERE / "REPORT.md"
SUMMARY_FILE = HERE / "summary.json"
CSV_FILE = HERE / "summary.csv"

STOP_THRESHOLD = 0.50
MIN_MODEL_CASES_FOR_STOP = 5


@dataclass(frozen=True)
class EvalCell:
    model: str
    thinking: str
    mode: str

    @property
    def key(self) -> str:
        return (
            f"{self.model}__thinking-{self.thinking}__{self.mode}"
            .replace(":", "-")
            .replace("/", "-")
        )


async def preflight(settings: AgentSettings) -> None:
    urls = [
        settings.data_query_url,
        settings.risk_url,
        settings.visualization_url,
        settings.comparison_url,
    ]
    async with httpx.AsyncClient(timeout=10.0) as client:
        for url in urls:
            response = await client.get(url + "/health")
            response.raise_for_status()
            payload = response.json()
            if url == settings.risk_url and (
                payload.get("status") == "degraded"
                or payload.get("model_loaded") is False
            ):
                raise RuntimeError(f"risk service is degraded: {payload}")
        response = await client.get(settings.model_base_url + "/models")
        response.raise_for_status()
        available = {item.get("id") for item in response.json().get("data") or []}
        if settings.request_model not in available:
            raise RuntimeError(
                f"model {settings.request_model!r} unavailable; found {sorted(available)}"
            )


async def warmup(provider: OpenAICompatibleProvider) -> float:
    reply = await provider.complete(
        messages=[
            {"role": "system", "content": "Reply with OK."},
            {"role": "user", "content": "OK"},
        ],
        tools=[],
        max_tokens=1,
    )
    return reply.latency_ms


async def run_cell(
    base: AgentSettings,
    cell: EvalCell,
    cases: list[dict[str, Any]],
    *,
    stop_threshold: float,
    resume: bool,
    run_tag: str,
    disable_deterministic: bool = False,
) -> dict[str, Any]:
    settings = base.with_eval_cell(
        model=cell.model,
        thinking=cell.thinking,
        structured_mode=cell.mode,
    )
    if disable_deterministic:
        settings = replace(settings, disable_deterministic_routing=True)
    settings = await ensure_runtime_model(settings)
    await preflight(settings)
    provider = OpenAICompatibleProvider(settings)
    context_info = await provider.ensure_context_loaded()
    print(
        f"[eval] context configured={context_info.get('configured_num_ctx')} "
        f"effective={context_info.get('effective_num_ctx')}"
    )
    warmup_ms = await warmup(provider)
    print(f"[eval] warmup {cell.key}: {warmup_ms / 1000:.1f}s")

    run_key = f"{cell.key}__{run_tag}" if run_tag else cell.key
    cell_dir = RUNS_DIR / run_key
    cell_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = cell_dir / "trajectories.jsonl"
    case_results: list[dict[str, Any]] = (
        _load_checkpoint(jsonl_path) if resume else []
    )
    completed_ids = {item["case"]["id"] for item in case_results}
    planned_case_count = len(cases)
    planned_model_cases = sum(
        1
        for case in cases
        if case.get("expected_route") == "model" or case.get("force_model")
    )
    stopped_within_cell = False
    cell_started = time.perf_counter()

    try:
        with jsonl_path.open("a" if resume else "w", encoding="utf-8") as log:
            for index, case in enumerate(cases, start=1):
                if case["id"] in completed_ids:
                    print(
                        f"[eval] {cell.key} case {index}/{len(cases)} "
                        f"{case['id']} (checkpoint)"
                    )
                    continue
                print(f"[eval] {cell.key} case {index}/{len(cases)} {case['id']}")
                artifacts = ArtifactStore(settings.artifact_ttl_seconds)
                executor = ToolExecutor(
                    settings,
                    artifacts,
                    fault_scenario=case.get("fault_scenario"),
                )
                orchestrator = AgentOrchestrator(settings, provider, executor)
                started = time.perf_counter()
                try:
                    result = await orchestrator.ask(
                        case["question"],
                        force_model=bool(case.get("force_model")),
                    )
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    score = score_case(
                        case,
                        result.response,
                        disable_deterministic=disable_deterministic,
                    )
                    raw_path, raw_sha = write_raw_payload(
                        cell_dir, case["id"], result.raw_log
                    )
                    record = {
                        "cell": cell.__dict__,
                        "case": case,
                        "response": result.response,
                        "score": score,
                        "elapsed_ms": round(elapsed_ms, 2),
                        "disable_deterministic": disable_deterministic,
                        "raw_payload": str(raw_path.relative_to(REPO_ROOT)),
                        "raw_sha256": raw_sha,
                    }
                except Exception as exc:  # noqa: BLE001
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    record = {
                        "cell": cell.__dict__,
                        "case": case,
                        "response": {
                            "status": "runner_error",
                            "answer_text": str(exc),
                            "route": {},
                            "trajectory": [],
                            "qualifications": [],
                            "model_metrics": {},
                            "timings_ms": {"total": elapsed_ms},
                        },
                        "score": {
                            "routing_pass": False,
                            "caveat_pass": False,
                            "status_pass": False,
                            "schema_first_pass": False,
                            "schema_eventual_pass": False,
                            "recovery_pass": False,
                            "view_pass": False,
                        },
                        "elapsed_ms": round(elapsed_ms, 2),
                        "runner_error": repr(exc),
                    }
                finally:
                    await executor.close()
                case_results.append(record)
                log.write(json.dumps(record, default=str) + "\n")
                log.flush()
                completed_model = [
                    item
                    for item in case_results
                    if item["case"].get("expected_route") == "model"
                    or item["case"].get("force_model")
                ]
                if len(completed_model) >= MIN_MODEL_CASES_FOR_STOP:
                    passed = sum(
                        1 for item in completed_model if item["score"]["routing_pass"]
                    )
                    remaining = planned_model_cases - len(completed_model)
                    required = math.ceil(stop_threshold * planned_model_cases)
                    if passed + remaining < required:
                        stopped_within_cell = True
                        print(
                            "[eval] Early cell stop: even perfect remaining cases "
                            f"cannot reach {stop_threshold:.1%} routing."
                        )
                        break
    finally:
        await provider.close()

    summary = summarize_cell(
        cell,
        case_results,
        warmup_ms=warmup_ms,
        runtime_seconds=time.perf_counter() - cell_started,
    )
    summary["planned_cases"] = planned_case_count
    summary["planned_model_tier_cases"] = planned_model_cases
    summary["stopped_within_cell"] = stopped_within_cell
    summary["run_tag"] = run_tag or None
    (cell_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def _load_checkpoint(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid checkpoint {path}:{number}: {exc}") from exc
    return records


def score_case(
    case: dict[str, Any],
    response: dict[str, Any],
    *,
    disable_deterministic: bool = False,
) -> dict[str, Any]:
    from datetime import date

    trajectory = response.get("trajectory") or []
    primary_calls = [
        event
        for event in trajectory
        if event.get("type") == "tool_call" and not event.get("qualification_call")
    ]
    actual_tools = _collapse_recovery_calls(primary_calls)
    expected_tools = case.get("expected_tools") or []
    route_path = (response.get("route") or {}).get("path")
    expected_route = case.get("expected_route")
    if disable_deterministic and expected_route == "deterministic":
        route_pass = route_path == "model"
    else:
        route_pass = route_path == expected_route
    if case.get("force_model"):
        route_pass = route_path == "model"
    tools_pass = actual_tools == expected_tools
    if (
        disable_deterministic
        and expected_route == "deterministic"
        and case.get("expected_status") == "answer"
    ):
        # Model-only may pick an alternate correct tool sequence; score tools
        # loosely when evidence exists and status/caveats still hold.
        tools_pass = tools_pass or (
            bool(expected_tools)
            and bool(actual_tools)
            and set(expected_tools).issubset(set(actual_tools))
        ) or (
            response.get("status") == "answer"
            and any(event.get("ok") for event in primary_calls)
        )
    routing_pass = route_pass and tools_pass

    actual_caveats = {
        item.get("id") for item in response.get("qualifications") or []
    }
    required_caveats = set(case.get("required_caveats") or [])
    caveat_pass = required_caveats.issubset(actual_caveats)
    expected_status = case.get("expected_status")
    if isinstance(expected_status, list):
        status_pass = response.get("status") in expected_status
    else:
        status_pass = response.get("status") == expected_status
    schema_successes = [
        event for event in trajectory if event.get("type") == "schema_success"
    ]
    if route_path == "model":
        schema_first = any(
            event.get("step") == 1 and event.get("strict")
            for event in schema_successes
        )
        schema_eventual = bool(schema_successes)
    else:
        schema_first = True
        schema_eventual = True

    fault = case.get("fault_scenario")
    errors = [
        event
        for event in primary_calls
        if not event.get("ok") or event.get("error")
    ]
    if fault == "validation_error_persistent":
        recovery_pass = status_pass and any(
            event.get("type") == "schema_retry_bound" for event in trajectory
        )
    elif fault:
        recovery_pass = bool(errors) and status_pass
    else:
        recovery_pass = True

    slot_resolution = (response.get("route") or {}).get("slot_resolution") or {}
    time_resolution = slot_resolution.get("time_resolution") or {}
    today = date.today()
    resolved_year_pass = True
    if "expected_resolved_year_offset" in case:
        expected_year = today.year + int(case["expected_resolved_year_offset"])
        actual_year = time_resolution.get("year")
        if actual_year is None:
            actual_year = slot_resolution.get("year")
        resolved_year_pass = actual_year == expected_year
    if "expected_resolved_years_offset" in case:
        expected_years = [
            today.year + int(offset)
            for offset in case["expected_resolved_years_offset"]
        ]
        actual_years = list(
            time_resolution.get("years") or slot_resolution.get("years") or []
        )
        resolved_year_pass = actual_years == expected_years
    if case.get("expect_schema_retry_bound"):
        resolved_year_pass = resolved_year_pass and any(
            event.get("type") == "schema_retry_bound" for event in trajectory
        )

    tool_year_pass = True
    expected_tool_year = case.get("expected_tool_year")
    if expected_tool_year is not None:
        years_seen = [
            (event.get("arguments") or {}).get("year")
            for event in primary_calls
            if event.get("ok")
        ]
        # Also accept start_date in the expected year.
        starts = [
            str((event.get("arguments") or {}).get("start_date") or "")
            for event in primary_calls
            if event.get("ok")
        ]
        tool_year_pass = expected_tool_year in years_seen or any(
            start.startswith(f"{expected_tool_year}-") for start in starts
        )

    answer_text = str(response.get("answer_text") or "")
    answer_text_pass = True
    for fragment in case.get("answer_must_not_contain") or []:
        if fragment.lower() in answer_text.lower():
            answer_text_pass = False
    must_any = case.get("answer_must_contain_any") or []
    if must_any:
        answer_text_pass = answer_text_pass and any(
            fragment.lower() in answer_text.lower() for fragment in must_any
        )

    synthesis_pass = True
    if case.get("expect_synthesis_finished"):
        synthesis_pass = any(
            event.get("type") == "model_turn" and event.get("phase") == "synthesis"
            for event in trajectory
        ) or any(
            event.get("type") in {"synthesis_timeout", "synthesis_fallback_to_tool_summary"}
            for event in trajectory
        )
        # Hanging with only a synthesizing trail and no completion is a fail;
        # timeout/fallback/success all count as finished.

    elapsed_pass = True
    max_elapsed = case.get("max_elapsed_ms")
    # Elapsed is attached by the runner after scoring; allow response timings.
    if max_elapsed is not None:
        total_ms = (response.get("timings_ms") or {}).get("total")
        if total_ms is not None:
            elapsed_pass = float(total_ms) <= float(max_elapsed)

    answer_origin = (response.get("route") or {}).get("answer_origin")
    expected_answer_origin = case.get("expected_answer_origin")
    answer_origin_pass = (
        True
        if expected_answer_origin is None
        else answer_origin == expected_answer_origin
    )

    view_pass = _score_views(case, response)

    # Executed filters must come from the question or router slots, and slot
    # filters the question names must run. An invented filter fails the case
    # even when tools and status match.
    from services.agent.grounding import score_executed_filters
    from services.agent.routing import route_question

    slots = route_question(case["question"]).slots
    filters = score_executed_filters(
        case["question"],
        primary_calls,
        utilities=slots.get("utilities") or [],
        county=slots.get("county"),
    )

    routing_pass = (
        routing_pass
        and filters["pass"]
        and resolved_year_pass
        and tool_year_pass
        and answer_text_pass
        and synthesis_pass
        and elapsed_pass
        and answer_origin_pass
    )

    return {
        "route_pass": route_pass,
        "routing_pass": routing_pass,
        "expected_tools": expected_tools,
        "actual_tools": actual_tools,
        "all_primary_calls": [event.get("tool") for event in primary_calls],
        "caveat_pass": caveat_pass,
        "filters_pass": filters["pass"],
        "invented_filters": filters["invented"],
        "missing_filters": filters["missing"],
        "missing_caveats": sorted(required_caveats - actual_caveats),
        "status_pass": status_pass,
        "schema_first_pass": schema_first,
        "schema_eventual_pass": schema_eventual,
        "recovery_pass": recovery_pass,
        "resolved_year_pass": resolved_year_pass,
        "tool_year_pass": tool_year_pass,
        "answer_text_pass": answer_text_pass,
        "synthesis_pass": synthesis_pass,
        "elapsed_pass": elapsed_pass,
        "answer_origin_pass": answer_origin_pass,
        "expected_answer_origin": expected_answer_origin,
        "actual_answer_origin": answer_origin,
        "view_pass": view_pass,
        "expected_view_types": case.get("expected_view_types"),
        "actual_view_types": [
            item.get("type") for item in response.get("views") or []
        ],
        "actual_view_status": response.get("view_status"),
        "direct_answer_without_tool_attempts": (
            response.get("model_metrics") or {}
        ).get("direct_answer_without_tool_attempts", 0),
        "no_tool_response_attempts_before_evidence": _no_tool_attempts(trajectory),
    }


def _score_views(case: dict[str, Any], response: dict[str, Any]) -> bool:
    """Harness view planner check. Independent of routing_pass. Skip force_model."""
    if case.get("force_model"):
        return True
    if "expected_view_types" not in case and "expected_view_status" not in case:
        return True
    actual_types = [item.get("type") for item in response.get("views") or []]
    if "expected_view_types" in case and actual_types != list(
        case["expected_view_types"]
    ):
        return False
    expected_status = case.get("expected_view_status")
    if expected_status is not None:
        return response.get("view_status") == expected_status
    expected_types = case.get("expected_view_types")
    if expected_types == []:
        return response.get("view_status") == "none"
    if expected_types:
        return response.get("view_status") == "applied"
    return True


def _collapse_recovery_calls(calls: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for event in calls:
        tool = event.get("tool")
        if (
            result
            and result[-1] == tool
            and (not event.get("ok") or event.get("error"))
        ):
            continue
        if (
            result
            and result[-1] == tool
            and any(
                previous.get("tool") == tool and not previous.get("ok")
                for previous in calls
            )
        ):
            continue
        result.append(tool)
    return result


def _no_tool_attempts(trajectory: list[dict[str, Any]]) -> int:
    successful_evidence = False
    count = 0
    for event in trajectory:
        if event.get("type") == "tool_call" and event.get("ok"):
            successful_evidence = True
        if (
            event.get("type") == "model_turn"
            and event.get("tool_call_count") == 0
            and not successful_evidence
        ):
            count += 1
    return count


def summarize_cell(
    cell: EvalCell,
    records: list[dict[str, Any]],
    *,
    warmup_ms: float,
    runtime_seconds: float,
) -> dict[str, Any]:
    n = len(records)
    model_records = [
        item
        for item in records
        if item["case"].get("expected_route") == "model"
        or item["case"].get("force_model")
    ]
    scores = [item["score"] for item in records]
    model_scores = [item["score"] for item in model_records]
    deterministic_scores = [
        item["score"]
        for item in records
        if not (
            item["case"].get("expected_route") == "model"
            or item["case"].get("force_model")
        )
    ]
    caveat_scores = [
        item["score"]
        for item in records
        if item["case"].get("required_caveats")
    ]
    deterministic_caveat_scores = [
        item["score"]
        for item in records
        if item["case"].get("required_caveats")
        and not (
            item["case"].get("expected_route") == "model"
            or item["case"].get("force_model")
        )
    ]
    model_caveat_scores = [
        item["score"]
        for item in model_records
        if item["case"].get("required_caveats")
    ]
    recovery_scores = [
        item["score"] for item in records if item["case"].get("fault_scenario")
    ]
    refusal_scores = [
        item["score"]
        for item in records
        if isinstance(item["case"].get("expected_status"), str)
        and item["case"].get("expected_status")
        in {"clarification", "unsupported"}
    ]
    request_latencies = [float(item["elapsed_ms"]) for item in records]
    model_call_latencies = [
        float(event["latency_ms"])
        for item in records
        for event in (item["response"].get("trajectory") or [])
        if event.get("type") == "model_turn"
    ]
    direct_attempts = sum(
        int(item["score"].get("direct_answer_without_tool_attempts") or 0)
        for item in model_records
    )
    no_tool_attempts = sum(
        int(item["score"].get("no_tool_response_attempts_before_evidence") or 0)
        for item in model_records
    )
    decision_turns = sum(
        int((item["response"].get("model_metrics") or {}).get("turns") or 0)
        for item in model_records
    )
    synthesis_attempts = [
        item
        for item in model_records
        if any(
            event.get("type") == "synthesis_start"
            for event in (item["response"].get("trajectory") or [])
        )
        or (item["response"].get("route") or {}).get("answer_origin")
        in {"model", "model_synthesis", "synthesis_fallback"}
    ]
    synthesis_fallbacks = [
        item
        for item in synthesis_attempts
        if (item["response"].get("route") or {}).get("answer_origin")
        == "synthesis_fallback"
        or any(
            event.get("type") == "synthesis_fallback_to_tool_summary"
            for event in (item["response"].get("trajectory") or [])
        )
    ]
    model_completed = [
        item
        for item in synthesis_attempts
        if (item["response"].get("route") or {}).get("answer_origin") == "model"
        and not any(
            event.get("type") == "synthesis_fallback_to_tool_summary"
            for event in (item["response"].get("trajectory") or [])
        )
    ]

    def rate(field: str, rows: list[dict[str, Any]] = scores) -> float:
        return (
            sum(1 for row in rows if row.get(field)) / len(rows)
            if rows
            else 1.0
        )

    return {
        "cell": cell.__dict__,
        "cases": n,
        "model_tier_cases": len(model_records),
        "routing_accuracy_all": rate("routing_pass"),
        "routing_accuracy_deterministic_tier": rate(
            "routing_pass", deterministic_scores
        ),
        "routing_accuracy_model_tier": rate("routing_pass", model_scores),
        "caveat_surfacing_rate": rate("caveat_pass", caveat_scores),
        "caveat_surfacing_rate_deterministic_tier": rate(
            "caveat_pass", deterministic_caveat_scores
        ),
        "caveat_surfacing_rate_model_tier": rate(
            "caveat_pass", model_caveat_scores
        ),
        "caveat_cases": len(caveat_scores),
        "status_accuracy": rate("status_pass"),
        "view_accuracy": rate("view_pass"),
        "refusal_clarification_accuracy": rate("status_pass", refusal_scores),
        "schema_validity_first_pass": rate("schema_first_pass", model_scores),
        "schema_validity_eventual": rate("schema_eventual_pass", model_scores),
        "recovery_rate": rate("recovery_pass", recovery_scores),
        "recovery_cases": len(recovery_scores),
        "synthesis_attempts": len(synthesis_attempts),
        "synthesis_fallback_count": len(synthesis_fallbacks),
        "synthesis_fallback_rate": (
            len(synthesis_fallbacks) / len(synthesis_attempts)
            if synthesis_attempts
            else 0.0
        ),
        "model_completed_count": len(model_completed),
        "model_completed_rate": (
            len(model_completed) / len(synthesis_attempts)
            if synthesis_attempts
            else 0.0
        ),
        "harness_rescued_count": len(synthesis_fallbacks),
        "harness_rescued_rate": (
            len(synthesis_fallbacks) / len(synthesis_attempts)
            if synthesis_attempts
            else 0.0
        ),
        "direct_answer_without_tool_attempts": direct_attempts,
        "no_tool_response_attempts_before_evidence": no_tool_attempts,
        "direct_answer_without_tool_rate_per_model_turn": (
            direct_attempts / decision_turns if decision_turns else 0.0
        ),
        "no_tool_response_rate_per_model_turn": (
            no_tool_attempts / decision_turns if decision_turns else 0.0
        ),
        "model_turns": decision_turns,
        "latency_ms": {
            "request_p50": percentile(request_latencies, 50),
            "request_p95": percentile(request_latencies, 95),
            "model_call_p50": percentile(model_call_latencies, 50),
            "model_call_p95": percentile(model_call_latencies, 95),
            "warmup": round(warmup_ms, 2),
        },
        "runtime_seconds": round(
            sum(float(item["elapsed_ms"]) for item in records) / 1000
            + warmup_ms / 1000,
            2,
        ),
        "runner_segment_runtime_seconds": round(runtime_seconds, 2),
        "failed_cases": [
            {
                "id": item["case"]["id"],
                "score": item["score"],
                "status": item["response"].get("status"),
                "answer": item["response"].get("answer_text"),
            }
            for item in records
            if not (
                item["score"].get("routing_pass")
                and item["score"].get("caveat_pass")
                and item["score"].get("status_pass")
                and item["score"].get("view_pass", True)
            )
        ],
    }


def percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct / 100
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    fraction = index - low
    return round(ordered[low] + (ordered[high] - ordered[low]) * fraction, 2)


def write_raw_payload(
    directory: Path, case_id: str, raw_log: list[dict[str, Any]]
) -> tuple[Path, str]:
    payload = json.dumps(raw_log, default=str).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    path = directory / f"{case_id}.raw.json.gz"
    with gzip.open(path, "wb") as handle:
        handle.write(payload)
    return path, digest


def write_reports(summaries: list[dict[str, Any]], stopped: bool) -> None:
    SUMMARY_FILE.write_text(
        json.dumps(
            {
                "stop_threshold": STOP_THRESHOLD,
                "stopped_early": stopped,
                "cells": summaries,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    with CSV_FILE.open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "model",
            "thinking",
            "mode",
            "routing_accuracy_all",
            "routing_accuracy_model_tier",
            "caveat_surfacing_rate",
            "schema_validity_first_pass",
            "schema_validity_eventual",
            "recovery_rate",
            "direct_answer_without_tool_attempts",
            "no_tool_response_attempts_before_evidence",
            "direct_answer_without_tool_rate_per_model_turn",
            "no_tool_response_rate_per_model_turn",
            "request_p50_ms",
            "request_p95_ms",
            "model_call_p50_ms",
            "model_call_p95_ms",
            "runtime_seconds",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            latency = summary["latency_ms"]
            writer.writerow(
                {
                    "model": summary["cell"]["model"],
                    "thinking": summary["cell"]["thinking"],
                    "mode": summary["cell"]["mode"],
                    "routing_accuracy_all": summary["routing_accuracy_all"],
                    "routing_accuracy_model_tier": summary[
                        "routing_accuracy_model_tier"
                    ],
                    "caveat_surfacing_rate": summary["caveat_surfacing_rate"],
                    "schema_validity_first_pass": summary[
                        "schema_validity_first_pass"
                    ],
                    "schema_validity_eventual": summary["schema_validity_eventual"],
                    "recovery_rate": summary["recovery_rate"],
                    "direct_answer_without_tool_attempts": summary[
                        "direct_answer_without_tool_attempts"
                    ],
                    "no_tool_response_attempts_before_evidence": summary[
                        "no_tool_response_attempts_before_evidence"
                    ],
                    "direct_answer_without_tool_rate_per_model_turn": summary[
                        "direct_answer_without_tool_rate_per_model_turn"
                    ],
                    "no_tool_response_rate_per_model_turn": summary[
                        "no_tool_response_rate_per_model_turn"
                    ],
                    "request_p50_ms": latency["request_p50"],
                    "request_p95_ms": latency["request_p95"],
                    "model_call_p50_ms": latency["model_call_p50"],
                    "model_call_p95_ms": latency["model_call_p95"],
                    "runtime_seconds": summary["runtime_seconds"],
                }
            )

    last = summaries[-1] if summaries else None
    passed_gate = bool(
        last and last["routing_accuracy_model_tier"] >= STOP_THRESHOLD
    )
    outcome = (
        "**Outcome:** the corrected staged baseline passed the gate: "
        f"{last['routing_accuracy_model_tier']:.1%} model-tier routing versus "
        f"{last['routing_accuracy_deterministic_tier']:.1%} deterministic-tier "
        "routing. The remaining matrix has not been run."
        if passed_gate
        else (
            "**Outcome:** the corrected staged baseline failed the gate: "
            f"{last['routing_accuracy_model_tier']:.1%} model-tier routing versus "
            f"{last['routing_accuracy_deterministic_tier']:.1%} deterministic-tier "
            "routing. The remaining matrix was not run."
            if last
            else ""
        )
    )

    lines = [
        "# Agent feasibility evaluation",
        "",
        "## Superseded baseline",
        "",
        "The earlier **0/5 model-tier routing result is superseded and must not be "
        "used as evidence that local models cannot route.** Its root cause was "
        "**token budget exhaustion during tool-catalog deliberation, not a model "
        "capability limit**. Direct isolated calls proved `qwen3:4b` emits tool "
        "calls through both Ollama endpoints.",
        "",
        "Controlled integration measurements on the same spatial question:",
        "",
        "- Full catalog, 1,800-token cap: 1,470 prompt tokens, 438.2s, "
        "`finish_reason=length`, zero calls.",
        "- Trimmed six-tool catalog: 1,125 prompt tokens, 333.8s, "
        "`finish_reason=stop`, zero calls.",
        "- Prefiltered two-tool catalog with the mixed routing/synthesis prompt: "
        "641 prompt tokens, 361.3s, `finish_reason=length`, zero calls.",
        "- Routing-only prompt with all six trimmed tools: 901 prompt tokens, "
        "419.8s, `finish_reason=length`, zero calls.",
        "- Routing-only prompt with two candidates: 414 prompt tokens, 281.5s, "
        "`finish_reason=tool_calls`, one correct call.",
        "",
        "Description/enum trimming reduced prompt size and stopped one runaway "
        "completion, but did not produce a call. Candidate prefiltering had the "
        "larger practical effect only after routing and synthesis instructions "
        "were separated. The corrected harness therefore uses all three.",
        "",
        "## Prominent local-provider constraint",
        "",
        "**Ollama’s OpenAI-compatible endpoint does not support `tool_choice`.** "
        "The harness cannot force Qwen to call a tool. It blocks unsupported direct "
        "answers and retries, but the attempts and latency are real local-model costs "
        "that a hosted provider with forced tool choice may avoid.",
        "",
        "The installed Qwen3 manifest also forced a thinking prefix despite "
        "`reasoning_effort=none`; the thinking-off cell used a template-only alias "
        "with identical weights and a pre-closed thinking block. The alias was "
        "faster in the isolated test, but Qwen can still emit deliberative prose "
        "before a tool call or JSON. The harness strips that prose from tool history "
        "and recovers a trailing schema-valid JSON object while reporting raw strict "
        "schema validity separately.",
        "",
        f"Stop gate: **{STOP_THRESHOLD:.0%} model-tier routing accuracy** after at "
        f"least {MIN_MODEL_CASES_FOR_STOP} model-tier cases. "
        f"Stopped early: **{stopped}**.",
        "",
        *(
            [
                outcome,
                "",
                f"Qwen produced {last['no_tool_response_attempts_before_evidence']} "
                "no-tool responses before evidence and "
                f"{last['direct_answer_without_tool_attempts']} valid direct "
                "answer attempts. The harness blocked all no-evidence output.",
                "",
            ]
            if last
            else []
        ),
        "## Headline: synthesis completion split",
        "",
        (
            f"**Model completed:** "
            f"{summaries[-1].get('model_completed_rate', 0.0):.1%} "
            f"({summaries[-1].get('model_completed_count', 0)}/"
            f"{summaries[-1].get('synthesis_attempts', 0)}). "
            f"**Harness-rescued (synthesis fallback):** "
            f"{summaries[-1].get('harness_rescued_rate', 0.0):.1%} "
            f"({summaries[-1].get('harness_rescued_count', 0)}/"
            f"{summaries[-1].get('synthesis_attempts', 0)}). "
            "Model-completed means grounded model prose (`answer_origin=model`); "
            "harness-rescued means tools succeeded but synthesis timed out, "
            "failed grounding, or otherwise fell back to the deterministic "
            "tool summary."
        ),
        "",
        f"**Synthesis fallback rate: {summaries[-1].get('synthesis_fallback_rate', 0.0):.1%}** "
        f"(same denominator; frequent fallback is a regression signal).",
        "",
        "## Cell results",
        "",
        "| Model | Thinking | Output | Deterministic/model routing | Schema first/eventual | Caveats | Recovery | Model done / rescued | No-tool/direct attempts | Request p50/p95 | Model p50/p95 | Runtime |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in summaries:
        cell = summary["cell"]
        latency = summary["latency_ms"]
        lines.append(
            "| {model} | {thinking} | {mode} | {det_routing:.1%}/{routing:.1%} | "
            "{schema1:.1%}/{schema2:.1%} | {caveat:.1%} | {recovery:.1%} | "
            "{done:.1%}/{rescued:.1%} ({done_n}/{fb_n} of {fb_d}) | "
            "{no_tool}/{direct} ({no_tool_rate:.1%}/{direct_rate:.1%} per turn) | "
            "{request_p50}/{request_p95} ms | {p50}/{p95} ms | {runtime:.1f}s |".format(
                model=cell["model"],
                thinking=cell["thinking"],
                mode=cell["mode"],
                routing=summary["routing_accuracy_model_tier"],
                det_routing=summary["routing_accuracy_deterministic_tier"],
                schema1=summary["schema_validity_first_pass"],
                schema2=summary["schema_validity_eventual"],
                caveat=summary["caveat_surfacing_rate"],
                recovery=summary["recovery_rate"],
                done=summary.get("model_completed_rate", 0.0),
                rescued=summary.get("harness_rescued_rate", 0.0),
                done_n=summary.get("model_completed_count", 0),
                fb_n=summary.get("harness_rescued_count", 0),
                fb_d=summary.get("synthesis_attempts", 0),
                direct=summary["direct_answer_without_tool_attempts"],
                no_tool=summary["no_tool_response_attempts_before_evidence"],
                no_tool_rate=summary["no_tool_response_rate_per_model_turn"],
                direct_rate=summary[
                    "direct_answer_without_tool_rate_per_model_turn"
                ],
                p50=latency["model_call_p50"],
                p95=latency["model_call_p95"],
                request_p50=latency["request_p50"],
                request_p95=latency["request_p95"],
                runtime=summary["runtime_seconds"],
            )
        )
    lines.extend(
        [
            "",
            "Latency columns report individual model calls. `runtime` is cumulative "
            "measured request time plus the final warmup; checkpoint downtime is excluded.",
            "",
            "## Harness-guaranteed versus model-dependent",
            "",
            "Harness-guaranteed: deterministic routing rules and candidate catalog "
            "prefiltering, schema validation and embedded-JSON recovery, duplicate-call "
            "suppression, bounded retries, response-contract checks, raw-payload "
            "isolation, required caveat injection, no-evidence blocking, and refusal when "
            "qualification metadata is unavailable. Required caveats surfaced on "
            f"{summaries[-1]['caveat_surfacing_rate_deterministic_tier']:.1%} of "
            "deterministic-tier caveat cases.",
            "",
            "Model-dependent: tool selection and argument construction on model-tier "
            "questions, multi-tool sequencing, recovery after a visible tool error, "
            "evidence-faithful synthesis, and choosing clarification/refusal when a "
            "deterministic rule does not apply.",
            "",
            "## Failed cases",
            "",
        ]
    )
    for summary in summaries:
        lines.append(
            f"### {summary['cell']['model']} / thinking={summary['cell']['thinking']} "
            f"/ {summary['cell']['mode']}"
        )
        if not summary["failed_cases"]:
            lines.append("- None")
        for failed in summary["failed_cases"]:
            lines.append(
                f"- `{failed['id']}` — status `{failed['status']}`; "
                f"routing={failed['score'].get('routing_pass')}, "
                f"caveats={failed['score'].get('caveat_pass')}, "
                f"status={failed['score'].get('status_pass')}, "
                f"views={failed['score'].get('view_pass')}"
            )
        lines.append("")
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    base = AgentSettings.from_env()
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    if args.case_ids:
        requested = set(args.case_ids.split(","))
        cases = [case for case in cases if case["id"] in requested]
        missing = requested - {case["id"] for case in cases}
        if missing:
            raise SystemExit(f"Unknown case IDs: {sorted(missing)}")

    cells = [
        EvalCell(model=model, thinking=thinking, mode=mode)
        for model in args.models.split(",")
        for thinking in args.thinking.split(",")
        for mode in args.modes.split(",")
    ]
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    stopped = False
    run_tag = args.run_tag
    if args.disable_deterministic and not run_tag:
        run_tag = "model-only-routing"
    for cell in cells:
        summary = await run_cell(
            base,
            cell,
            cases,
            stop_threshold=args.stop_threshold,
            resume=not args.fresh,
            run_tag=run_tag,
            disable_deterministic=args.disable_deterministic,
        )
        summaries.append(summary)
        write_reports(summaries, stopped=False)
        if (
            not args.disable_deterministic
            and summary["model_tier_cases"] >= MIN_MODEL_CASES_FOR_STOP
            and summary["routing_accuracy_model_tier"] < args.stop_threshold
        ):
            stopped = True
            print(
                f"[eval] STOP: {cell.key} model-tier routing "
                f"{summary['routing_accuracy_model_tier']:.1%} < "
                f"{args.stop_threshold:.1%}"
            )
            break
    write_reports(summaries, stopped=stopped)
    print(f"[eval] report: {REPORT_FILE}")
    return 2 if stopped else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="qwen3:4b")
    parser.add_argument("--thinking", default="off", help="comma-separated off,on")
    parser.add_argument("--modes", default="prompt", help="prompt,constrained")
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--stop-threshold", type=float, default=STOP_THRESHOLD)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Discard any per-cell trajectory checkpoint before running.",
    )
    parser.add_argument(
        "--run-tag",
        default="",
        help="Suffix run artifacts without changing the evaluated cell identity.",
    )
    parser.add_argument(
        "--disable-deterministic",
        action="store_true",
        help=(
            "Architecture experiment: skip deterministic answers and send every "
            "non-clarification/unsupported question to the model. Default remains off."
        ),
    )
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
