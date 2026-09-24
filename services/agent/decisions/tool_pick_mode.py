"""Jev chooses the model-path tool. Qwen still writes the answer. Default off."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from services.agent.config import AgentSettings
from services.agent.decisions.integrity import question_hash
from services.agent.decisions.v3 import tool_pick_call
from services.agent.routing import (
    _COUNTY_CAPABLE_DATASETS,
    _asks_map_view,
    _ignition_definition,
    _range_for_year,
)


def _comparison_metric(lower: str) -> str | None:
    """Same metric words as the router's comparison block. None if unnamed."""
    if "epss-to-ignition" in lower or "epss to ignition" in lower:
        return "epss_to_ignition_ratio"
    if "epss" in lower or "outage" in lower:
        return "epss_outage_count"
    if "cal fire" in lower or "calfire" in lower:
        return "calfire_incident_count"
    if (
        "ignition" in lower
        or "wildfire activity" in lower
        or re.search(r"\bwildfire(?:s)?\b", lower)
    ):
        return "ignition_count"
    return None

_VIZ_DATASET = {
    "cpuc_ignitions": "ignitions",
    "us_ignitions": "us_ignitions",
    "epss_outages": "epss",
    "psps_events": "psps",
    "calfire_incidents": "calfire",
    "hftd": "hftd",
}
_SERIES_WORD = r"\b(?:trend|time series|weekly|monthly|daily)\b"
_INTERVAL_WORD = r"\b(daily|weekly|monthly)\b"
_MULTI_PRIMARY_RULES = {
    "multi_intent_count_and_trend",
    "multi_intent_territory_and_map",
}
_EXPLAIN = re.compile(r"\b(?:why|explain|difference|reason)\b")
_COUNT_WORD = re.compile(r"\b(?:how many|count|number of|tally|total)\b")
_LIST_WORD = re.compile(r"\b(?:list|records?)\b")
_TEMPLATE_INTENTS = {
    "count",
    "records_list",
    "map",
    "trend",
    "rank",
    "spatial_context",
    "compare",
}


@dataclass
class ToolPickDecision:
    tool: str | None
    confidence: float | None
    path: str
    reason: str
    latency_ms: float | None = None
    error: str | None = None


def decide_tool_pick(
    question: str,
    candidates: list[str],
    settings: AgentSettings,
) -> ToolPickDecision:
    """Ask Jev which tool to call. Any failure returns a qwen fallback."""
    try:
        from services.agent.decisions.typesafe_backend import make_backend

        backend = make_backend(
            settings.jev_backend,
            model=settings.jev_model,
            timeout_seconds=settings.jev_timeout_seconds,
        )
        call = tool_pick_call(
            question,
            date.today().isoformat(),
            list(candidates),
            settings.jev_ablation,
        )
        result = backend.evaluate(
            call["state"],
            call["questions"],
            request_id="tool-pick",
            question_hash=question_hash(question),
        )
    except Exception as exc:  # noqa: BLE001
        return ToolPickDecision(None, None, "qwen", "error", error=f"{type(exc).__name__}: {exc}")
    if result is None or result.error or "tool_pick" not in result.answers:
        message = None if result is None else result.error
        reason = "timeout" if message and "Timeout" in message else "error"
        return ToolPickDecision(None, None, "qwen", reason, error=message)
    answer = result.answers["tool_pick"]
    tool = answer.value if isinstance(answer.value, str) else None
    confidence = float(answer.confidence or 0.0)
    if tool not in candidates:
        return ToolPickDecision(
            tool, confidence, "qwen", "tool_not_allowed", result.latency_ms
        )
    if confidence < settings.jev_tool_pick_min_confidence:
        return ToolPickDecision(
            tool, confidence, "qwen", "below_threshold", result.latency_ms
        )
    return ToolPickDecision(tool, confidence, "jev", "above_threshold", result.latency_ms)


def _time_args(slots: dict[str, Any]) -> dict[str, Any]:
    """Year or start/end already on the route. Never invent a year."""
    year = slots.get("year")
    start = slots.get("start_date")
    end = slots.get("end_date")
    if start and end:
        full_year = (
            str(start).endswith("-01-01")
            and str(end).endswith("-12-31")
            and str(start)[:4] == str(end)[:4]
            and year is not None
            and year == int(str(start)[:4])
        )
        if not full_year:
            out: dict[str, Any] = {"start_date": start, "end_date": end}
            if year is not None:
                out["year"] = year
            return out
    if year is not None:
        return {"year": year}
    if start and end:
        return {"start_date": start, "end_date": end}
    return {}


def _span(slots: dict[str, Any]) -> tuple[str, str] | None:
    start = slots.get("start_date")
    end = slots.get("end_date")
    if start and end:
        return str(start), str(end)
    year = slots.get("year")
    if year is None:
        return None
    return _range_for_year(int(year))


def _planned_primary_count(decision: Any) -> int:
    return len(getattr(decision, "tool_calls", None) or [])


def _is_multi_primary(decision: Any) -> bool:
    if getattr(decision, "rule", None) in _MULTI_PRIMARY_RULES:
        return True
    return _planned_primary_count(decision) > 1


def requires_multiple_primary_tools(question: str, decision: Any) -> bool:
    """True when one tool cannot answer. Includes force_model eval cases."""
    if _is_multi_primary(decision):
        return True
    if getattr(decision, "rule", None) != "forced_eval":
        return False
    from services.agent.routing import route_question

    return _is_multi_primary(route_question(question))


def template_intent(question: str, executions: list[Any]) -> str | None:
    """Intent the template can answer. None means qwen still writes the prose."""
    primary = [
        item
        for item in executions
        if getattr(item, "ok", False) and not getattr(item, "qualification_call", False)
    ]
    if len(primary) != 1:
        return None
    item = primary[0]
    summary = item.summary or {}
    lower = " ".join(question.lower().split())
    tool = item.tool
    if tool == "data_query_records":
        if summary.get("result_mode") == "records" or (
            _LIST_WORD.search(lower) and not _COUNT_WORD.search(lower)
        ):
            return "records_list"
        if summary.get("result_mode") == "count" and _COUNT_WORD.search(lower):
            return "count"
        return None
    if tool == "visualization_create":
        return "map" if summary.get("kind") == "map" else "trend"
    if tool == "data_query_rank":
        return "rank"
    if tool == "data_query_spatial":
        return "spatial_context"
    if tool == "comparison_run" and not _EXPLAIN.search(lower):
        return "compare"
    return None


def template_can_answer(question: str, executions: list[Any]) -> bool:
    return template_intent(question, executions) in _TEMPLATE_INTENTS


def arguments_for_tool(
    tool: str,
    slots: dict[str, Any],
    question: str = "",
) -> dict[str, Any] | None:
    """Arguments from route slots. Missing required fields fall back to qwen."""
    if tool == "data_query_records":
        return _records_args(slots)
    if tool == "visualization_create":
        return _visualization_args(slots, question)
    if tool == "comparison_run":
        return _comparison_args(slots, question)
    if tool == "data_query_spatial":
        return _spatial_args(slots)
    return None


def _records_args(slots: dict[str, Any]) -> dict[str, Any] | None:
    time_args = _time_args(slots)
    dataset = slots.get("dataset")
    if not dataset or not time_args:
        return None
    utilities = list(slots.get("utilities") or [])
    if len(utilities) > 1:
        return None
    args: dict[str, Any] = {"dataset": dataset, "result_mode": "count", **time_args}
    if len(utilities) == 1 and dataset != "us_ignitions":
        args["utility"] = utilities[0]
    county = slots.get("county")
    if county and dataset in _COUNTY_CAPABLE_DATASETS:
        args["county"] = county
    return args


def _visualization_args(slots: dict[str, Any], question: str) -> dict[str, Any] | None:
    dataset = slots.get("dataset")
    viz = _VIZ_DATASET.get(dataset) if isinstance(dataset, str) else None
    if viz is None:
        return None
    lower = " ".join(question.lower().split())
    wants_map = _asks_map_view(lower)
    wants_series = bool(re.search(_SERIES_WORD, lower))
    if wants_map == wants_series:
        return None
    time_args = _time_args(slots)
    if not time_args and not (wants_map and viz == "hftd"):
        return None
    args: dict[str, Any] = {
        "kind": "map" if wants_map else "time_series",
        "dataset": viz,
        **time_args,
    }
    if not wants_map:
        interval = re.search(_INTERVAL_WORD, lower)
        if interval is None:
            return None
        args["interval"] = interval.group(1)
    utilities = list(slots.get("utilities") or [])
    if len(utilities) == 1:
        args["utility"] = utilities[0]
    elif len(utilities) > 1:
        return None
    county = slots.get("county")
    if county and dataset in _COUNTY_CAPABLE_DATASETS:
        args["county"] = county
    return args


def _comparison_args(slots: dict[str, Any], question: str) -> dict[str, Any] | None:
    lower = " ".join(question.lower().split())
    metric = _comparison_metric(lower)
    if metric is None or "us ignition" in lower:
        return None
    utilities = list(slots.get("utilities") or [])
    years = list(slots.get("years") or [])
    if len(years) == 2 and len(utilities) == 1:
        a_start, a_end = _range_for_year(int(years[0]))
        b_start, b_end = _range_for_year(int(years[1]))
        return {
            "kind": "periods",
            "scope_type": "utility",
            "scope": utilities[0],
            "metric": metric,
            "period_a_start": a_start,
            "period_a_end": a_end,
            "period_b_start": b_start,
            "period_b_end": b_end,
            "ignition_definition": _ignition_definition(lower),
        }
    span = _span(slots)
    if metric and len(utilities) >= 2 and span is not None and slots.get("year") is not None:
        start, end = span
        args = {
            "kind": "utilities",
            "utilities": utilities,
            "metric": metric,
            "start_date": start,
            "end_date": end,
            "normalize": "per_circuit" if "per circuit" in lower else "none",
            "ignition_definition": _ignition_definition(lower),
        }
        return args
    tiers = sorted(set(re.findall(r"tier\s*([23])", lower)))
    if metric and tiers == ["2", "3"] and span is not None and slots.get("year") is not None:
        start, end = span
        return {
            "kind": "regions",
            "region_type": "hftd",
            "regions": ["Tier 2", "Tier 3"],
            "metric": metric,
            "start_date": start,
            "end_date": end,
        }
    return None


def _spatial_args(slots: dict[str, Any]) -> dict[str, Any] | None:
    coords = slots.get("coords")
    if isinstance(coords, (list, tuple)) and len(coords) == 2:
        return {"kind": "point", "lat": coords[0], "lon": coords[1]}
    utilities = list(slots.get("utilities") or [])
    span = _span(slots)
    if len(utilities) != 1 or span is None:
        return None
    start, end = span
    return {
        "kind": "summary",
        "utility": utilities[0],
        "start_date": start,
        "end_date": end,
    }


def log_tool_pick(settings: AgentSettings, question: str, decision: ToolPickDecision) -> None:
    from services.agent.decisions.shadow_log import ShadowLog

    try:
        ShadowLog(
            settings.jev_log_path,
            max_bytes=int(settings.jev_log_max_mb * 1024 * 1024),
        ).write(
            {
                "type": "tool_pick_decision",
                "question": question,
                "tool": decision.tool,
                "confidence": decision.confidence,
                "path": decision.path,
                "reason": decision.reason,
                "latency_ms": decision.latency_ms,
                "error": decision.error,
            }
        )
    except Exception:  # noqa: BLE001
        return
