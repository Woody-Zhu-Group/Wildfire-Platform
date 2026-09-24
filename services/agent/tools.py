"""Validated read-only HTTP tools, response checks, and concise summaries."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import ValidationError

from services.agent.argument_normalize import prepare_tool_arguments
from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.schemas import (
    ComparisonRunArgs,
    DataQueryRankArgs,
    DataQueryRecordsArgs,
    DataQuerySpatialArgs,
    EXECUTABLE_TOOL_MODELS,
    RiskForecastArgs,
    strip_harness_only_arguments,
    RiskMetricsArgs,
    RiskSurfaceArgs,
    VisualizationCreateArgs,
    VisualizationInspectArgs,
)
from services.agent.time_resolve import CallWindows, apply_harness_years
from services.shared.counties import UnknownCountyError, normalize_county
from services.agent.coverage import (
    call_coverage_gap,
    call_dataset,
    call_periods,
    count_coverage_gap,
    dataset_known,
    named_utilities,
)
from services.shared.dataset_registry import (
    COMPARISON_METRIC_DATASETS,
    data_query_path,
    dataset_coverage_gap,
    group_code_and_label,
    not_covered_message,
    partial_coverage_note,
)


@dataclass
class ToolExecution:
    tool: str
    arguments: dict[str, Any]
    ok: bool
    summary: dict[str, Any]
    raw: dict[str, Any] | None
    error: dict[str, Any] | None
    artifact: dict[str, Any] | None
    latency_ms: float
    evidence_id: str = field(default_factory=lambda: f"evidence_{uuid.uuid4().hex[:12]}")
    qualification_call: bool = False
    stripped_utilities: list[str] = field(default_factory=list)

    def model_payload(self) -> dict[str, Any]:
        if self.ok:
            return {
                "ok": True,
                "evidence_id": self.evidence_id,
                "summary": self.summary,
                "artifact_ref": self.artifact["ref"] if self.artifact else None,
            }
        return {"ok": False, "error": self.error}


class ToolExecutor:
    def __init__(
        self,
        settings: AgentSettings,
        artifacts: ArtifactStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        fault_scenario: str | None = None,
    ) -> None:
        self.settings = settings
        self.artifacts = artifacts
        self.fault_scenario = fault_scenario
        self._fault_used = False
        self._client = httpx.AsyncClient(
            timeout=min(settings.request_timeout_seconds, 120.0),
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def preview_arguments(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        year: int | None = None,
        years: list[int] | None = None,
        utilities: list[str] | None = None,
        time_resolution: dict[str, Any] | None = None,
        qualification_call: bool = False,
        harness_call: bool = False,
        allow_untagged: bool = False,
        call_windows: CallWindows | None = None,
    ) -> dict[str, Any]:
        """Return harness-normalized arguments without calling the backend.

        Used so SSE/audit trails show the year/utility actually executed after
        slot fill and year override — not only the raw model payload.
        """
        if not harness_call:
            arguments, _hidden = strip_harness_only_arguments(tool, arguments)
        normalized = prepare_tool_arguments(
            tool,
            arguments,
            year=year,
            years=years,
            utilities=utilities,
            fill_aliases=True,
            fill_year=True,
            fill_utility=True,
            repair_comparison=True,
        )
        if not qualification_call:
            normalized, _stripped = _strip_ungrounded_utilities(
                normalized, utilities=utilities, allow_untagged=allow_untagged
            )
            normalized, _error = apply_harness_years(
                normalized,
                time_resolution=time_resolution,
                hold_window=not harness_call,
                windows=call_windows,
            )
        return normalized

    async def execute(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        request_id: str,
        attempt: int,
        qualification_call: bool = False,
        year: int | None = None,
        years: list[int] | None = None,
        utilities: list[str] | None = None,
        time_resolution: dict[str, Any] | None = None,
        harness_call: bool = False,
        allow_untagged: bool = False,
        call_windows: CallWindows | None = None,
    ) -> ToolExecution:
        started = time.perf_counter()
        if tool not in EXECUTABLE_TOOL_MODELS:
            print(
                json.dumps(
                    {
                        "event": "tool_attempt",
                        "request_id": request_id,
                        "attempt": attempt,
                        "tool": tool,
                        "arguments": arguments,
                        "qualification_call": qualification_call,
                    },
                    default=str,
                )
            )
            return self._error(
                tool,
                arguments,
                "unknown_tool",
                f"Unknown tool {tool!r}",
                False,
                "Choose one of the provided tools.",
                started,
                qualification_call,
            )
        # Harness-only arguments (snap_shoreline) come only from the router. A
        # model or any other caller cannot turn them on for its own reads.
        if not harness_call:
            arguments, hidden = strip_harness_only_arguments(tool, arguments)
            if hidden:
                print(
                    json.dumps(
                        {
                            "event": "harness_arguments_stripped",
                            "request_id": request_id,
                            "attempt": attempt,
                            "tool": tool,
                            "arguments": hidden,
                        }
                    )
                )
        normalized_arguments = prepare_tool_arguments(
            tool,
            arguments,
            year=year,
            years=years,
            utilities=utilities,
            fill_aliases=True,
            fill_year=True,
            fill_utility=True,
            repair_comparison=True,
        )
        stripped_utilities: list[str] = []
        time_corrections: list[dict[str, Any]] = []
        normalized_arguments, county_corrections = _normalize_county_arguments(
            normalized_arguments
        )
        for correction in county_corrections:
            print(
                json.dumps(
                    {
                        "event": "harness_county_correction",
                        "request_id": request_id,
                        "attempt": attempt,
                        "tool": tool,
                        **correction,
                    }
                )
            )
        if not qualification_call:
            normalized_arguments, stripped_utilities = _strip_ungrounded_utilities(
                normalized_arguments, utilities=utilities, allow_untagged=allow_untagged
            )
            normalized_arguments, year_error = apply_harness_years(
                normalized_arguments,
                time_resolution=time_resolution,
                hold_window=not harness_call,
                corrections=time_corrections,
                windows=call_windows,
            )
            for correction in time_corrections:
                print(
                    json.dumps(
                        {
                            "event": "harness_time_correction",
                            "request_id": request_id,
                            "attempt": attempt,
                            "tool": tool,
                            **correction,
                        },
                        default=str,
                    )
                )
            if year_error:
                print(
                    json.dumps(
                        {
                            "event": "tool_attempt",
                            "request_id": request_id,
                            "attempt": attempt,
                            "tool": tool,
                            "arguments": normalized_arguments,
                            "requested_arguments": arguments,
                            "qualification_call": qualification_call,
                        },
                        default=str,
                    )
                )
                return self._error(
                    tool,
                    normalized_arguments,
                    "year_not_derived",
                    year_error,
                    False,
                    "Use only harness-resolved years from the question, or ask for clarification.",
                    started,
                    qualification_call,
                )
        print(
            json.dumps(
                {
                    "event": "tool_attempt",
                    "request_id": request_id,
                    "attempt": attempt,
                    "tool": tool,
                    "arguments": normalized_arguments,
                    "requested_arguments": arguments,
                    "qualification_call": qualification_call,
                    "stripped_utilities": stripped_utilities,
                    "time_corrections": time_corrections,
                },
                default=str,
            )
        )
        try:
            parsed = EXECUTABLE_TOOL_MODELS[tool].model_validate(normalized_arguments)
        except ValidationError as exc:
            return self._error(
                tool,
                normalized_arguments,
                "invalid_arguments",
                "Tool arguments failed schema validation.",
                True,
                "Correct the listed fields and retry this tool.",
                started,
                qualification_call,
                field_errors=_json_safe_errors(exc.errors(include_url=False)),
            )

        # Dataset coverage is enforced here, for every caller (router, model,
        # Jev templates, slot planner): a read for a utility the dataset holds
        # no rows for is a structured not-covered result, never a zero.
        gap = coverage_gap(tool, parsed.model_dump(mode="json", exclude_none=True))
        if gap is not None:
            result = self._not_covered(
                tool,
                parsed.model_dump(mode="json", exclude_none=True),
                gap,
                started,
                qualification_call,
            )
            print(
                json.dumps(
                    {
                        "event": "tool_result",
                        "request_id": request_id,
                        "tool": tool,
                        "ok": False,
                        "error_code": "not_covered",
                        "latency_ms": round(result.latency_ms, 2),
                        "evidence_id": None,
                    }
                )
            )
            return result

        if self.fault_scenario == "validation_error_persistent" and not qualification_call:
            return self._error(
                tool,
                normalized_arguments,
                "invalid_arguments",
                "Injected persistent schema validation failure.",
                True,
                "Do not retry the same invalid arguments.",
                started,
                qualification_call,
                field_errors=[
                    {
                        "type": "missing",
                        "loc": ("dataset",),
                        "msg": "Field required",
                        "input": {},
                    }
                ],
            )

        if self.fault_scenario and not self._fault_used and not qualification_call:
            self._fault_used = True
            if self.fault_scenario == "validation_error_once":
                return self._error(
                    tool,
                    normalized_arguments,
                    "invalid_arguments",
                    "Injected validation failure for recovery evaluation.",
                    True,
                    "Review the arguments and retry the same intended tool.",
                    started,
                    qualification_call,
                )
            if self.fault_scenario == "service_503_once":
                return self._error(
                    tool,
                    normalized_arguments,
                    "service_unavailable",
                    "Injected HTTP 503 for recovery evaluation.",
                    True,
                    "Retry the same tool once.",
                    started,
                    qualification_call,
                )

        try:
            url, params = self._map_request(tool, parsed)
            raw = await self._get_json(url, params)
            if self.fault_scenario == "partial_200_once" and self._fault_used:
                self.fault_scenario = None
                raw = {"unexpected": "partial response with HTTP 200"}
            summary = self._validate_and_summarize(tool, parsed, raw)
            mark_uncovered_counts(
                tool, parsed.model_dump(mode="json", exclude_none=True), summary
            )
            artifact = self.artifacts.put(tool, raw)
            result = ToolExecution(
                tool=tool,
                arguments=parsed.model_dump(mode="json", exclude_none=True),
                ok=True,
                summary=summary,
                raw=raw,
                error=None,
                artifact=artifact,
                latency_ms=(time.perf_counter() - started) * 1000,
                qualification_call=qualification_call,
                stripped_utilities=list(stripped_utilities),
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            recoverable = status in {400, 408, 409, 422, 429, 500, 502, 503, 504}
            detail = _response_detail(exc.response)
            # A place or utility the backend does not know is a question for
            # the user, not something to guess. The code lets the harness show
            # the backend's suggestions instead of a generic failure.
            code = f"http_{status}"
            if status == 400 and detail.lower().startswith("unknown county"):
                code = "unknown_county"
            elif status == 400 and detail.lower().startswith("unknown utility"):
                code = "unknown_utility"
            result = self._error(
                tool,
                parsed.model_dump(mode="json", exclude_none=True),
                code,
                detail or f"Backend returned HTTP {status}",
                recoverable,
                (
                    "Correct arguments using the backend detail and retry."
                    if status in {400, 404, 422}
                    else "Retry once; if it fails again, do not improvise."
                ),
                started,
                qualification_call,
            )
        except (httpx.RequestError, ValueError, KeyError, TypeError) as exc:
            code = (
                "unexpected_partial_response"
                if isinstance(exc, (ValueError, KeyError, TypeError))
                else "transport_error"
            )
            result = self._error(
                tool,
                parsed.model_dump(mode="json", exclude_none=True),
                code,
                str(exc),
                True,
                (
                    "Retry or use another service; do not infer missing fields."
                    if code == "unexpected_partial_response"
                    else "Retry the same tool once."
                ),
                started,
                qualification_call,
            )

        print(
            json.dumps(
                {
                    "event": "tool_result",
                    "request_id": request_id,
                    "tool": tool,
                    "ok": result.ok,
                    "error_code": (result.error or {}).get("code"),
                    "latency_ms": round(result.latency_ms, 2),
                    "evidence_id": result.evidence_id if result.ok else None,
                }
            )
        )
        return result

    async def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(2):
            try:
                response = await self._client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Backend response must be a JSON object")
                return payload
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last = exc
                if attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                if attempt == 0 and exc.response.status_code in {502, 503, 504}:
                    await asyncio.sleep(0.15)
                    continue
                raise
        assert last is not None
        raise last

    def _map_request(
        self, tool: str, parsed: Any
    ) -> tuple[str, dict[str, Any]]:
        if tool == "data_query_records":
            return self._map_data_records(parsed)
        if tool == "data_query_rank":
            return self._map_data_rank(parsed)
        if tool == "data_query_spatial":
            return self._map_spatial(parsed)
        if tool == "visualization_create":
            return self._map_visualization(parsed)
        if tool == "visualization_inspect":
            return self._map_inspect(parsed)
        if tool == "risk_forecast":
            return self._map_risk(parsed)
        if tool == "risk_surface":
            return self._map_risk_surface(parsed)
        if tool == "risk_metrics":
            return self.settings.risk_url + "/metrics", {}
        if tool == "comparison_run":
            return self._map_comparison(parsed)
        raise ValueError(f"Unsupported tool {tool}")

    def _map_data_records(
        self, args: DataQueryRecordsArgs
    ) -> tuple[str, dict[str, Any]]:
        path = data_query_path(args.dataset.value)
        params = args.model_dump(mode="json", exclude_none=True)
        params.pop("dataset", None)
        mode = params.pop("result_mode", "count")
        requested_limit = params.pop("limit", 10)
        params["limit"] = 1 if mode == "count" else requested_limit
        params["geometry"] = False
        params["format"] = "json"
        if "bbox" in params:
            params["bbox"] = ",".join(str(v) for v in params["bbox"])
        incident_mode = params.pop("incident_type_mode", None)
        if incident_mode and incident_mode != "wildfire_default":
            params["incident_type"] = incident_mode
        if "tier" in params:
            params["tier"] = params["tier"]
        return self.settings.data_query_url + path, params

    def _map_data_rank(
        self, args: DataQueryRankArgs
    ) -> tuple[str, dict[str, Any]]:
        params = args.model_dump(mode="json", exclude_none=True)
        incident_mode = params.pop("incident_type_mode", None)
        if incident_mode and incident_mode != "wildfire_default":
            params["incident_type"] = incident_mode
        return self.settings.data_query_url + "/rank", params

    def _map_spatial(
        self, args: DataQuerySpatialArgs
    ) -> tuple[str, dict[str, Any]]:
        params = args.model_dump(mode="json", exclude_none=True)
        kind = params.pop("kind")
        # Sent only when set, so other point calls keep their exact URL.
        if params.pop("snap_shoreline", False):
            params["snap_shoreline"] = "true"
        if kind == "point":
            return self.settings.data_query_url + "/spatial/point", params
        if "hftd_tier" in params:
            params["hftd_tier"] = params["hftd_tier"]
        return self.settings.data_query_url + "/spatial/summary", params

    def _map_visualization(
        self, args: VisualizationCreateArgs
    ) -> tuple[str, dict[str, Any]]:
        params = args.model_dump(mode="json", exclude_none=True)
        kind = params.pop("kind")
        incident_mode = params.pop("incident_type_mode", None)
        if incident_mode and incident_mode != "wildfire_default":
            params["incident_type"] = incident_mode
        if kind == "map":
            params.pop("interval", None)
            return self.settings.visualization_url + "/map-layer", params
        params.pop("tier", None)
        params["interval"] = params.get("interval", "weekly")
        return self.settings.visualization_url + "/time-series", params

    def _map_inspect(
        self, args: VisualizationInspectArgs
    ) -> tuple[str, dict[str, Any]]:
        params = args.model_dump(mode="json", exclude_none=True)
        kind = params.pop("kind")
        if kind == "utility_territory":
            return self.settings.visualization_url + "/utility-territory", {
                "utility": params["utility"]
            }
        params["id"] = params.pop("record_id")
        params.pop("utility", None)
        return self.settings.visualization_url + "/event-detail", params

    def _map_risk_surface(self, args: RiskSurfaceArgs) -> tuple[str, dict[str, Any]]:
        return (
            self.settings.risk_url + "/surface",
            args.model_dump(mode="json", exclude_none=True),
        )

    def _map_risk(self, args: RiskForecastArgs) -> tuple[str, dict[str, Any]]:
        return (
            self.settings.risk_url + "/predict",
            args.model_dump(mode="json", exclude_none=True),
        )

    def _map_comparison(
        self, args: ComparisonRunArgs
    ) -> tuple[str, dict[str, Any]]:
        params = args.model_dump(mode="json", exclude_none=True)
        kind = params.pop("kind")
        if kind == "utilities":
            params["utilities"] = ",".join(params["utilities"])
            return self.settings.comparison_url + "/compare-utilities", params
        if kind == "regions":
            params["regions"] = ",".join(params["regions"])
            return self.settings.comparison_url + "/compare-regions", params
        return self.settings.comparison_url + "/compare-periods", params

    def _validate_and_summarize(
        self, tool: str, args: Any, raw: dict[str, Any]
    ) -> dict[str, Any]:
        if tool == "data_query_records":
            meta = _require_dict(raw, "meta")
            total = _require_int(meta, "total")
            data = raw.get("data")
            if not isinstance(data, list):
                raise ValueError("data_query response missing data list")
            return {
                "dataset": args.dataset.value,
                "result_mode": args.result_mode,
                "total": total,
                "returned": len(data),
                "filters": meta.get("filters") or {},
                "records": [_human_record(row) for row in data[:5]],
                "metadata": _select_metadata(meta),
            }
        if tool == "data_query_rank":
            meta = _require_dict(raw, "meta")
            data = raw.get("data")
            if not isinstance(data, list):
                raise ValueError("rank response missing data list")
            canvas_metric = {
                ("cpuc_ignitions", "count"): "ignition_count",
                ("calfire_incidents", "count"): "calfire_incident_count",
                ("calfire_incidents", "acres_burned"): "acres_burned",
                ("epss_outages", "count"): "epss_outage_count",
            }.get((args.dataset, args.metric), args.metric)
            results = []
            for row in data:
                if not isinstance(row, dict):
                    raise ValueError("rank row must be an object")
                key = row.get("group_value")
                # Deploy-order safeguard: if the agent is updated before the
                # data query service, rank rows arrive without code or label,
                # so fill them from the registry with the same rule.
                names = (
                    group_code_and_label(args.group_by, str(key))
                    if key is not None
                    else {"code": None, "label": None}
                )
                results.append(
                    {
                        "key": key,
                        "code": row.get("code", names["code"]),
                        "label": row.get("label", names["label"]),
                        "value": row.get("metric_value"),
                        "division": row.get("division"),
                        "circuit_name": row.get("circuit_name"),
                    }
                )
            return {
                "dataset": args.dataset,
                "group_by": args.group_by,
                "metric": args.metric,
                "canvas_metric": canvas_metric,
                "kind": "ranking",
                "total": _require_int(meta, "total"),
                "returned": int(meta.get("returned") or len(results)),
                "limit": int(meta.get("limit") or args.limit),
                "tie_extended": bool(meta.get("tie_extended")),
                "ties_cut": bool(meta.get("ties_cut")),
                "empty_reason": meta.get("empty_reason"),
                "results": results,
                "filters": meta.get("filters") or {},
                "metadata": _select_metadata(meta),
            }
        if tool == "data_query_spatial":
            if args.kind == "point":
                _require_dict(raw, "iou")
                _require_dict(raw, "grid_cell")
                return {
                    "kind": "point",
                    "lat": raw.get("lat"),
                    "lon": raw.get("lon"),
                    "iou": raw["iou"],
                    "hftd_tier": raw.get("hftd_tier"),
                    "grid_cell": raw["grid_cell"],
                    "county": raw.get("county"),
                    "metadata": raw.get("meta") or {},
                }
            counts = _require_dict(raw, "counts")
            _require_dict(raw, "region")
            return {
                "kind": "summary",
                "region": raw["region"],
                "start_date": raw.get("start_date"),
                "end_date": raw.get("end_date"),
                "counts": counts,
                "metadata": raw.get("meta") or {},
            }
        if tool == "visualization_create":
            meta = _require_dict(raw, "meta")
            if args.kind == "map":
                _require_int(meta, "total")
                if "truncated" not in meta:
                    raise ValueError("map response missing meta.truncated")
                geojson = _require_dict(raw, "geojson")
                features = geojson.get("features")
                if not isinstance(features, list):
                    raise ValueError("map response missing feature list")
                return {
                    "kind": "map",
                    "dataset": raw.get("dataset"),
                    "total": meta["total"],
                    "returned": meta.get("returned"),
                    "truncated": bool(meta["truncated"]),
                    "filters": meta.get("filters") or {},
                    "metadata": _select_metadata(meta),
                }
            buckets = raw.get("buckets")
            if not isinstance(buckets, list):
                raise ValueError("time-series response missing buckets")
            if "total_events" not in meta:
                raise ValueError("time-series response missing meta.total_events")
            ranked = sorted(
                buckets,
                key=lambda item: int(item.get("count") or 0),
                reverse=True,
            )[:5]
            return {
                "kind": "time_series",
                "dataset": raw.get("dataset"),
                "interval": raw.get("interval"),
                "total_events": meta["total_events"],
                "bucket_count": len(buckets),
                "top_buckets": ranked,
                "filters": meta.get("filters") or {},
                "metadata": _select_metadata(meta),
            }
        if tool == "visualization_inspect":
            if args.kind == "utility_territory":
                _require_dict(raw, "bounds")
                return {
                    "kind": "utility_territory",
                    "utility": raw.get("utility"),
                    "utility_name": raw.get("utility_name"),
                    "bounds": raw["bounds"],
                    "suggested_view": raw.get("suggested_view"),
                }
            _require_dict(raw, "attributes")
            return {
                "kind": "event_detail",
                "dataset": raw.get("dataset"),
                "id": raw.get("id"),
                "attributes": _human_record(raw["attributes"]),
                "detail_fields": raw.get("detail_fields") or [],
                "outage_count": len(raw.get("outages") or []),
                "affected_circuit_count": len(raw.get("affected_circuits") or []),
            }
        if tool == "risk_surface":
            return _summarize_risk_surface(args, raw)
        if tool == "risk_metrics":
            return _summarize_risk_metrics(raw)
        if tool == "risk_forecast":
            for key in ("date", "risk", "xi", "lookback_days"):
                if key not in raw:
                    raise ValueError(f"risk response missing {key}")
            summary = {
                "date": raw["date"],
                "risk": raw["risk"],
                "expected_count": raw.get("expected_count"),
                "xi": raw["xi"],
                "lookback_days": raw["lookback_days"],
                "aggregation": raw.get("aggregation"),
                "aggregation_note": raw.get("aggregation_note"),
                "cell_count": raw.get("cell_count"),
                "scope": raw.get("scope") or {},
                "local_percentile": raw.get("local_percentile"),
                "statewide_percentile": raw.get("statewide_percentile"),
                "local_period": raw.get("local_period"),
                "local_n": raw.get("local_n"),
                "intensity": raw.get("intensity"),
                "mean_intensity": raw.get("mean_intensity"),
                "includes_cell_461": bool(raw.get("includes_cell_461")),
            }
            if "cell_id" in raw:
                summary["cell_id"] = raw["cell_id"]
            if raw.get("cell_ids"):
                summary["cell_ids"] = raw["cell_ids"]
            return summary
        if tool == "comparison_run":
            meta = _require_dict(raw, "meta")
            if args.kind in {"utilities", "regions"}:
                results = raw.get("results")
                if not isinstance(results, list):
                    raise ValueError("comparison response missing results")
                for row in results:
                    if row.get("value") is None and not row.get("reason"):
                        raise ValueError("null comparison result missing reason")
                if args.kind == "utilities":
                    # Deploy-order safeguard: if the agent is updated before the
                    # comparison service, utility rows arrive without code or
                    # label, so fill them from the registry with the same rule.
                    results = [
                        {**group_code_and_label("utility", str(row.get("key"))), **row}
                        if row.get("key") is not None
                        else row
                        for row in results
                    ]
                return {
                    "kind": args.kind,
                    "metric": raw.get("metric"),
                    "normalize": raw.get("normalize"),
                    "results": results,
                    "metadata": meta,
                }
            for key in ("period_a", "period_b", "delta"):
                if not isinstance(raw.get(key), dict):
                    raise ValueError(f"period comparison missing {key}")
            return {
                "kind": "periods",
                "metric": raw.get("metric"),
                "scope_type": raw.get("scope_type"),
                "scope": raw.get("scope"),
                "period_a": raw["period_a"],
                "period_b": raw["period_b"],
                "delta": raw["delta"],
                "metadata": meta,
            }
        raise ValueError(f"No response validator for {tool}")

    def _not_covered(
        self,
        tool: str,
        arguments: dict[str, Any],
        gap: dict[str, Any],
        started: float,
        qualification_call: bool,
    ) -> ToolExecution:
        result = self._error(
            tool,
            arguments,
            "not_covered",
            not_covered_message(gap),
            False,
            (
                "Do not report a count or a zero. Tell the user the dataset does "
                "not cover this utility and offer the alternatives listed."
            ),
            started,
            qualification_call,
        )
        result.error["not_covered"] = gap
        return result

    def _error(
        self,
        tool: str,
        arguments: dict[str, Any],
        code: str,
        message: str,
        recoverable: bool,
        suggested_action: str,
        started: float,
        qualification_call: bool,
        *,
        field_errors: list[dict[str, Any]] | None = None,
    ) -> ToolExecution:
        return ToolExecution(
            tool=tool,
            arguments=arguments,
            ok=False,
            summary={},
            raw=None,
            error={
                "code": code,
                "message": message,
                "recoverable": recoverable,
                "suggested_action": suggested_action,
                "field_errors": field_errors or [],
            },
            artifact=None,
            latency_ms=(time.perf_counter() - started) * 1000,
            qualification_call=qualification_call,
        )


def _json_safe_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pydantic error dicts can embed Exception objects in ``ctx``; make them JSON-safe."""
    return json.loads(json.dumps(errors, default=str))


def _strip_ungrounded_utilities(
    arguments: dict[str, Any],
    *,
    utilities: list[str] | None,
    allow_untagged: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Remove utility filters that were not named in the question/slots.

    Place names (e.g. Sacramento) must never be silently coerced into an IOU.
    Prefer stripping and answering at the asked scope over rejecting the call
    after the model invents SCE/PGE. "untagged" (records with no utility) is
    kept only when allow_untagged says the question asked for it; it is a
    valid value that would otherwise turn a statewide count into a count of
    unattributed records.
    """
    allowed = set(utilities or [])
    if allow_untagged:
        allowed.add("untagged")
    filled = dict(arguments)
    stripped: list[str] = []
    value = filled.get("utility")
    if isinstance(value, str) and value.strip() and value.strip() not in allowed:
        stripped.append(value.strip())
        filled.pop("utility", None)
    if "utilities" in filled:
        kept = [
            item
            for item in (filled.get("utilities") or [])
            if isinstance(item, str) and item.strip() and item.strip() in allowed
        ]
        removed = [
            item
            for item in (filled.get("utilities") or [])
            if isinstance(item, str) and item.strip() and item.strip() not in allowed
        ]
        stripped.extend(removed)
        if kept:
            filled["utilities"] = kept
        else:
            filled.pop("utilities", None)
    if filled.get("scope_type") == "utility":
        scope = filled.get("scope")
        if isinstance(scope, str) and scope.strip() and scope.strip() not in allowed:
            stripped.append(scope.strip())
            filled.pop("scope", None)
            filled.pop("scope_type", None)
    return filled, stripped


def _ungrounded_utility_error(
    arguments: dict[str, Any],
    *,
    utilities: list[str] | None,
) -> str | None:
    """Compatibility helper: report whether any utility would be stripped."""
    _filled, stripped = _strip_ungrounded_utilities(
        arguments, utilities=utilities
    )
    if not stripped:
        return None
    return (
        f"Utility {stripped[0]!r} was not named in the question; "
        "do not invent an IOU from a place or county name"
    )


def _normalize_county_arguments(
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Resolve county arguments to the canonical warehouse name before the call.

    "Butte County" from a model becomes "Butte". A value that resolves to no
    county is left as written so the backend rejects it with suggestions;
    the harness never guesses.
    """
    corrections: list[dict[str, Any]] = []
    filled = dict(arguments)

    def fix(field: str, value: Any) -> Any:
        if not isinstance(value, str) or not value.strip():
            return value
        try:
            canonical = normalize_county(value)
        except UnknownCountyError:
            return value
        if canonical != value:
            corrections.append({"field": field, "from": value, "to": canonical})
        return canonical

    if "county" in filled:
        filled["county"] = fix("county", filled["county"])
    if filled.get("region_type") == "county" and isinstance(filled.get("regions"), list):
        filled["regions"] = [fix("regions", item) for item in filled["regions"]]
    if filled.get("scope_type") == "county" and "scope" in filled:
        filled["scope"] = fix("scope", filled["scope"])
    return filled, corrections


def _response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body)
    except Exception:  # noqa: BLE001
        pass
    return response.text[:500]


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"response missing object {key}")
    return value


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValueError(f"response missing integer {key}")
    return value


def _select_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "incident_type_mode",
        "null_incident_type_count",
        "null_utility_records_in_table",
        "utility_filter_definition",
        "source",
        "utility_attributed",
        "census",
        "coverage",
        "not_comparable_to",
        "sample_geography",
        "notes",
        "epss_scope",
        "ignition_definition",
        "calfire_incident_types",
        "empty_reason",
        "multi_county_incidents",
    }
    return {key: value for key, value in meta.items() if key in keep}


def _human_record(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    preferred = (
        "event_date",
        "start_date",
        "end_date",
        "incident_name",
        "incident_type",
        "circuit_id",
        "circuit_name",
        "circuit",
        "event_name",
        "utility",
        "utility_name",
        "county",
        "tier",
        "acres_burned",
        "risk",
        "reason",
    )
    selected = {key: row[key] for key in preferred if key in row}
    if not selected:
        selected = {
            key: value
            for key, value in list(row.items())[:12]
            if key not in {"geometry", "geom", "style"}
        }
    return selected


_SURFACE_CELL_COUNT = 824
_METRIC_MODELS = ("HPP", "NHPP", "cNHPP")
_RANKING_METRICS = ("top5_precision", "top1_precision", "lift_top5")
_METRIC_LABELS = {
    "log_likelihood": "log-likelihood",
    "auc": "AUC",
    "top5_precision": "top 5% precision",
    "top1_precision": "top 1% precision",
    "lift_top5": "top 5% lift",
}


def _metric_number(row: dict[str, Any], key: str, *, nullable: bool = False) -> float | None:
    value = row.get(key)
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ValueError(f"metrics {row.get('model')} {key} is not a finite number")
    return float(value)


def _summarize_risk_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """Check /metrics has all three models on one window, then keep every shown value.

    HPP has no ranking, so its top-k precision and lift must be null with a
    reason; a ranking model (NHPP, cNHPP) must have all three. The display
    strings are the rounded forms the answer prints, so every rendered number
    is in the evidence.
    """
    rows = [raw, *(raw.get("baselines") or [])]
    by_model: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("model") in by_model:
            raise ValueError("metrics rows are missing or repeated")
        by_model[str(row.get("model"))] = row
    if raw.get("model") != "cNHPP" or set(by_model) != set(_METRIC_MODELS):
        raise ValueError("metrics must hold cNHPP with HPP and NHPP baselines")
    models: list[dict[str, Any]] = []
    for name in _METRIC_MODELS:
        row = by_model[name]
        entry: dict[str, Any] = {
            "model": name,
            "log_likelihood": _metric_number(row, "log_likelihood"),
            "auc": _metric_number(row, "auc"),
        }
        reason = row.get("not_applicable_reason")
        for key in _RANKING_METRICS:
            entry[key] = _metric_number(row, key, nullable=True)
        missing = [key for key in _RANKING_METRICS if entry[key] is None]
        if missing and (len(missing) != len(_RANKING_METRICS) or not reason):
            raise ValueError(f"metrics {name} ranking values are partly missing or lack a reason")
        if name != "HPP" and missing:
            raise ValueError(f"metrics {name} must have ranking values")
        entry["not_applicable_reason"] = reason if missing else None
        entry["display"] = {
            "log_likelihood": f"{entry['log_likelihood']:.1f}",
            "auc": f"{entry['auc']:.3f}",
            **{
                key: None if entry[key] is None else (
                    f"{entry[key]:.2f}" if key == "lift_top5" else f"{entry[key] * 100:.2f}%"
                )
                for key in _RANKING_METRICS
            },
        }
        models.append(entry)
    train_years = raw.get("train_years")
    if not isinstance(train_years, list) or not train_years or not all(isinstance(y, int) for y in train_years):
        raise ValueError("metrics train_years must be a list of years")
    eval_year = raw.get("eval_year")
    sha = raw.get("params_sha256")
    if not isinstance(eval_year, int) or not isinstance(sha, str) or len(sha) != 64:
        raise ValueError("metrics eval_year or params_sha256 is missing")
    nhpp, cnhpp = by_model["NHPP"], by_model["cNHPP"]
    return {
        "kind": "model_metrics",
        "models": models,
        "xi": _metric_number(raw, "xi"),
        "train_years": list(train_years),
        "eval_year": eval_year,
        "eval_start": raw.get("eval_start"),
        "eval_end": raw.get("eval_end"),
        "params_sha256": sha,
        "cnhpp_minus_nhpp_log_likelihood": (
            f"{float(cnhpp['log_likelihood']) - float(nhpp['log_likelihood']):.1f}"
        ),
        "labels": dict(_METRIC_LABELS),
    }


def _summarize_risk_surface(args: RiskSurfaceArgs, raw: dict[str, Any]) -> dict[str, Any]:
    """Check the whole grid came back for the asked day, then keep a small summary."""
    asked = args.date.isoformat()
    if raw.get("date") != asked:
        raise ValueError(f"risk surface date {raw.get('date')!r} != requested {asked}")
    cells = raw.get("cells")
    if not isinstance(cells, list) or len(cells) != _SURFACE_CELL_COUNT:
        raise ValueError("risk surface did not return the full 824-cell grid")
    ids: set[int] = set()
    for cell in cells:
        risk = cell.get("risk") if isinstance(cell, dict) else None
        cell_id = cell.get("cell_id") if isinstance(cell, dict) else None
        if not isinstance(cell_id, int) or cell_id in ids:
            raise ValueError("risk surface cell ids are missing or repeated")
        if not isinstance(risk, (int, float)) or not 0 <= risk <= 1:
            raise ValueError("risk surface cell risk is outside [0, 1]")
        ids.add(cell_id)
    top = max(cells, key=lambda cell: cell["risk"])
    return {
        "kind": "surface",
        "date": asked,
        "lookback_days": raw.get("lookback_days"),
        "cell_count": len(cells),
        "max_risk": top["risk"],
        "max_risk_cell_id": top["cell_id"],
        "includes_cell_461": 461 in ids,
    }


def coverage_gap(tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The measured coverage gap a call would hit, or None when some of it is covered."""
    return call_coverage_gap(tool, arguments)


def mark_uncovered_counts(
    tool: str, arguments: dict[str, Any], summary: dict[str, Any]
) -> None:
    """Replace each count that falls outside measured coverage with None.

    A result can hold counts for several datasets at once (a spatial summary
    counts ignitions, EPSS outages, and CAL FIRE incidents inside one
    territory). Each count key is resolved to its dataset through the
    registry; where that dataset has no rows for the named utility in the
    call's period, the count becomes None and ``summary["not_covered"][key]``
    records why, so no answer, view, or grounding check can read it as a zero.
    An unknown count key raises: a count that cannot be traced to a dataset is
    not rendered. A comparison's rows are checked side by side the same way.
    """
    if tool == "comparison_run":
        _mark_uncovered_comparison(arguments, summary)
        return
    counts = summary.get("counts")
    datasets = list(counts) if isinstance(counts, dict) else []
    for key in datasets:
        gap = count_coverage_gap(key, tool, arguments)
        if gap is None:
            continue
        counts[key] = None
        summary.setdefault("not_covered", {})[key] = {
            **gap,
            "message": not_covered_message(gap, subject="count"),
        }
    # A covered count whose period runs past measured coverage counts only the
    # covered part; the answer says which part.
    dataset = call_dataset(tool, arguments)
    if dataset and not datasets and dataset_known(dataset):
        datasets = [dataset]
    (start, end), *_ = call_periods(tool, arguments)
    notes = [
        note
        for key in datasets
        if key not in (summary.get("not_covered") or {})
        for utility in named_utilities(tool, arguments) or [None]
        if (note := partial_coverage_note(key, utility, start, end))
    ]
    if notes:
        summary["coverage_notes"] = list(dict.fromkeys(notes))


def _mark_uncovered_comparison(arguments: dict[str, Any], summary: dict[str, Any]) -> None:
    """Null every comparison value outside measured coverage, and note partial periods.

    The comparison service applies the same measured coverage; this holds the
    invariant even if a service response disagrees, so a value outside
    coverage is never shown as a number.
    """
    dataset = COMPARISON_METRIC_DATASETS.get(str(summary.get("metric") or arguments.get("metric")))
    if not dataset:
        return
    notes: list[str] = []

    def check(row: dict[str, Any], utility: str | None, start: Any, end: Any) -> None:
        gap = dataset_coverage_gap(dataset, [utility] if utility else [], start, end)
        if gap is not None:
            row["value"] = None
            row["raw_value"] = None
            row["reason"] = gap["reason"]
            row["not_covered"] = gap
            return
        note = partial_coverage_note(dataset, utility, start, end)
        if note:
            row["coverage_note"] = note
            if note not in notes:
                notes.append(note)

    # Rows are copied: the summary must not rewrite the raw service response.
    kind = summary.get("kind") or arguments.get("kind")
    if kind in {"utilities", "regions"}:
        start, end = arguments.get("start_date"), arguments.get("end_date")
        summary["results"] = [dict(row) for row in summary.get("results") or []]
        for row in summary["results"]:
            utility = str(row.get("key")) if kind == "utilities" else None
            check(row, utility, start, end)
    else:
        utility = named_utilities("comparison_run", arguments)
        for name in ("period_a", "period_b"):
            if isinstance(summary.get(name), dict):
                row = summary[name] = dict(summary[name])
                check(
                    row,
                    utility[0] if utility else None,
                    row.get("start_date") or arguments.get(f"{name}_start"),
                    row.get("end_date") or arguments.get(f"{name}_end"),
                )
        if isinstance(summary.get("delta"), dict) and any(
            (summary.get(name) or {}).get("value") is None for name in ("period_a", "period_b")
        ):
            delta = summary["delta"] = dict(summary["delta"])
            delta["value"] = None
            delta["reason"] = delta.get("reason") or "Cannot compute delta when either period value is null"
    if notes:
        summary["coverage_notes"] = notes


def not_covered_notes(executions: list[Any]) -> list[str]:
    """One sentence per uncovered count or partly covered period, in order."""
    notes: list[str] = []
    for execution in executions:
        if not getattr(execution, "ok", False):
            continue
        summary = execution.summary or {}
        messages = [gap.get("message") for gap in (summary.get("not_covered") or {}).values()]
        messages.extend(summary.get("coverage_notes") or [])
        for message in messages:
            if message and message not in notes:
                notes.append(message)
    return notes
