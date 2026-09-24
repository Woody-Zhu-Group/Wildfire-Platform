"""Public, model-output, and tool schemas."""

from __future__ import annotations

from functools import lru_cache

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from services.shared.dataset_registry import (
    AGENT_DATASET_VALUES,
    ALLOWED_RANK_PAIRS,
    DATASETS,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Utility(str, Enum):
    PGE = "PGE"
    SCE = "SCE"
    SDGE = "SDGE"
    PACIFICORP = "PACIFICORP"
    LIBERTY = "Liberty"
    BVES = "BVES"
    UNTAGGED = "untagged"


class Dataset(str, Enum):
    CPUC_IGNITIONS = DATASETS["cpuc_ignitions"].agent_key
    US_IGNITIONS = DATASETS["us_ignitions"].agent_key
    EPSS_OUTAGES = DATASETS["epss_outages"].agent_key
    PSPS_EVENTS = DATASETS["psps_events"].agent_key
    CALFIRE_INCIDENTS = DATASETS["calfire_incidents"].agent_key
    CIRCUITS = DATASETS["circuits"].agent_key
    # Discrepancy: warehouse/canonical key is hftd_tiers; agent enum value is hftd.
    HFTD = DATASETS["hftd_tiers"].agent_key
    IOU_TERRITORIES = DATASETS["iou_territories"].agent_key


assert {member.value for member in Dataset} == set(AGENT_DATASET_VALUES)


class HftdTier(str, Enum):
    TIER_2 = "Tier 2"
    TIER_3 = "Tier 3"


class DataQueryRecordsArgs(StrictModel):
    """Filtered warehouse reads and counts."""

    dataset: Dataset
    result_mode: Literal["count", "records"] = "count"
    utility: Utility | None = None
    year: int | None = Field(None, ge=1900, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    bbox: tuple[float, float, float, float] | None = None
    county: str | None = None
    tier: HftdTier | None = None
    circuit_id: str | None = Field(None, pattern=r"^\d{9}$")
    min_acres: float | None = Field(None, ge=0)
    incident_type_mode: Literal["wildfire_default", "all", "untyped"] | None = None
    limit: int = Field(10, ge=1, le=25)

    @model_validator(mode="after")
    def validate_dates_and_dataset(self) -> "DataQueryRecordsArgs":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date")
        if self.tier and self.dataset != Dataset.HFTD:
            raise ValueError("tier is valid only for dataset=hftd")
        if self.circuit_id and self.dataset not in {
            Dataset.CIRCUITS,
            Dataset.EPSS_OUTAGES,
        }:
            raise ValueError("circuit_id is valid only for circuits or epss_outages")
        # Discrepancy: data_query /psps/events has no county= (registry
        # allowed_filters omit it) but this records schema still allows county
        # on any dataset except us_ignitions.
        if self.county and self.dataset == Dataset.US_IGNITIONS:
            raise ValueError("county is unavailable for US ignitions")
        return self


class DataQueryRankArgs(StrictModel):
    """Single-dataset top-N ranking. Never mix warehouse datasets."""

    dataset: Literal["cpuc_ignitions", "calfire_incidents", "epss_outages"]
    group_by: Literal["county", "utility", "circuit"]
    metric: Literal["count", "acres_burned"] = "count"
    utility: Utility | None = None
    year: int | None = Field(None, ge=1900, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    county: str | None = None
    incident_type_mode: Literal["wildfire_default", "all", "untyped"] | None = None
    limit: int = Field(10, ge=1, le=25)

    @model_validator(mode="after")
    def validate_rank_pair(self) -> "DataQueryRankArgs":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date")
        pair = (self.dataset, self.group_by, self.metric)
        if pair not in ALLOWED_RANK_PAIRS:
            raise ValueError(
                f"ranking is not available for dataset={self.dataset} "
                f"group_by={self.group_by} metric={self.metric}"
            )
        if self.group_by == "county" and self.county:
            raise ValueError("county filter cannot be combined with group_by=county")
        if self.incident_type_mode and self.dataset != "calfire_incidents":
            raise ValueError("incident_type_mode is valid only for calfire_incidents")
        return self


class DataQuerySpatialArgs(StrictModel):
    """Point context or counts inside one IOU/HFTD region."""

    kind: Literal["point", "summary"]
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    utility: Utility | None = None
    hftd_tier: HftdTier | None = None
    start_date: date | None = None
    end_date: date | None = None
    # Harness only: the router sets this for Census city center points. It is
    # left out of the model-facing JSON schema, so the model never sees it.
    snap_shoreline: SkipJsonSchema[bool] = False

    @model_validator(mode="after")
    def validate_kind(self) -> "DataQuerySpatialArgs":
        if self.kind == "point":
            if self.lat is None or self.lon is None:
                raise ValueError("point requires lat and lon")
        elif self.snap_shoreline:
            raise ValueError("snap_shoreline applies only to a point")
        else:
            if (self.utility is None) == (self.hftd_tier is None):
                raise ValueError("summary requires exactly one utility or hftd_tier")
            if self.start_date is None or self.end_date is None:
                raise ValueError("summary requires start_date and end_date")
            if self.start_date > self.end_date:
                raise ValueError("start_date must be <= end_date")
        return self


class VisualizationCreateArgs(StrictModel):
    """Create a map layer or time series."""

    kind: Literal["map", "time_series"]
    dataset: Literal[
        "ignitions", "us_ignitions", "epss", "psps", "calfire", "hftd"
    ]
    utility: Utility | None = None
    year: int | None = Field(None, ge=1900, le=2100)
    start_date: date | None = None
    end_date: date | None = None
    county: str | None = None
    tier: HftdTier | None = None
    interval: Literal["daily", "weekly", "monthly"] | None = None
    incident_type_mode: Literal["wildfire_default", "all", "untyped"] | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> "VisualizationCreateArgs":
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date")
        if self.kind == "time_series":
            if self.dataset == "hftd":
                raise ValueError("hftd has no time series")
            if (self.interval or "weekly") == "weekly" and self.year is None:
                raise ValueError("weekly time series requires year")
        return self


class VisualizationInspectArgs(StrictModel):
    """Inspect a utility territory or a single event/circuit."""

    kind: Literal["utility_territory", "event_detail"]
    utility: Utility | None = None
    dataset: Literal[
        "ignitions", "us_ignitions", "epss", "psps", "calfire", "circuits"
    ] | None = None
    record_id: str | None = None
    year: int | None = Field(None, ge=1900, le=2100)

    @model_validator(mode="after")
    def validate_kind(self) -> "VisualizationInspectArgs":
        if self.kind == "utility_territory" and self.utility is None:
            raise ValueError("utility_territory requires utility")
        if self.kind == "event_detail" and (
            self.dataset is None or not self.record_id
        ):
            raise ValueError("event_detail requires dataset and record_id")
        return self


class RiskForecastArgs(StrictModel):
    """Historical cNHPP risk for exactly one place (cell, point, county, or IOU)."""

    date: date
    cell_id: int | None = Field(None, ge=0, le=823)
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    county: str | None = None
    utility: Utility | None = None
    lookback_days: int | None = Field(None, ge=1, le=365)

    @model_validator(mode="after")
    def exactly_one_place(self) -> "RiskForecastArgs":
        groups: list[str] = []
        if self.cell_id is not None:
            groups.append("cell_id")
        if self.lat is not None or self.lon is not None:
            if self.lat is None or self.lon is None:
                raise ValueError("lat and lon must be provided together")
            groups.append("point")
        if self.county:
            groups.append("county")
        if self.utility is not None:
            if self.utility.value not in {"PGE", "SCE", "SDGE"}:
                raise ValueError("utility must be PGE, SCE, or SDGE")
            groups.append("utility")
        if len(groups) != 1:
            raise ValueError(
                "risk_forecast requires exactly one of cell_id, lat+lon, county, or utility"
            )
        return self


Metric = Literal[
    "ignition_count",
    "epss_outage_count",
    "epss_to_ignition_ratio",
    "calfire_incident_count",
    "acres_burned",
    "psps_event_count",
    "customers_deenergized",
]


class ComparisonRunArgs(StrictModel):
    """Compare one metric across utilities, regions, or periods."""

    kind: Literal["utilities", "regions", "periods"]
    metric: Metric
    normalize: Literal["none", "per_circuit", "per_km2"] = "none"
    ignition_definition: Literal["attribute", "spatial"] | None = None
    utilities: list[Utility] | None = None
    region_type: Literal["county", "hftd"] | None = None
    regions: list[str] | None = None
    scope_type: Literal["utility", "county", "hftd"] | None = None
    scope: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    period_a_start: date | None = None
    period_a_end: date | None = None
    period_b_start: date | None = None
    period_b_end: date | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> "ComparisonRunArgs":
        if self.kind == "utilities":
            if not self.utilities or self.start_date is None or self.end_date is None:
                raise ValueError("utilities requires utilities, start_date, and end_date")
        elif self.kind == "regions":
            if (
                not self.region_type
                or not self.regions
                or self.start_date is None
                or self.end_date is None
            ):
                raise ValueError(
                    "regions requires region_type, regions, start_date, and end_date"
                )
        else:
            fields = (
                self.scope_type,
                self.scope,
                self.period_a_start,
                self.period_a_end,
                self.period_b_start,
                self.period_b_end,
            )
            if any(value is None for value in fields):
                raise ValueError("periods requires scope and both complete periods")
        return self


TOOL_MODELS: dict[str, type[StrictModel]] = {
    "data_query_records": DataQueryRecordsArgs,
    "data_query_rank": DataQueryRankArgs,
    "data_query_spatial": DataQuerySpatialArgs,
    "visualization_create": VisualizationCreateArgs,
    "visualization_inspect": VisualizationInspectArgs,
    "risk_forecast": RiskForecastArgs,
    "comparison_run": ComparisonRunArgs,
}


class RiskSurfaceArgs(StrictModel):
    """Historical cNHPP risk for every grid cell on one day (GET /surface)."""

    date: date


# Tools only the router calls. They are never offered to the model or to Jev,
# so the model tool list and the Jev payloads do not change.
HARNESS_TOOL_MODELS: dict[str, type[StrictModel]] = {
    "risk_surface": RiskSurfaceArgs,
}
EXECUTABLE_TOOL_MODELS: dict[str, type[StrictModel]] = {
    **TOOL_MODELS,
    **HARNESS_TOOL_MODELS,
}


@lru_cache(maxsize=None)
def harness_only_arguments(tool: str) -> frozenset[str]:
    """Argument fields left out of the model-facing JSON schema (SkipJsonSchema).

    Only the router sets these (for example snap_shoreline on a city center
    point read). A model never sees them and must not be able to send them.
    """
    model = EXECUTABLE_TOOL_MODELS.get(tool)
    if model is None:
        return frozenset()
    visible = set(model.model_json_schema().get("properties") or {})
    return frozenset(set(model.model_fields) - visible)


def strip_harness_only_arguments(
    tool: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Drop harness-only arguments from a call that did not come from the router."""
    hidden = harness_only_arguments(tool)
    stripped = sorted(key for key in arguments if key in hidden)
    if not stripped:
        return arguments, []
    return {key: value for key, value in arguments.items() if key not in hidden}, stripped

TOOL_DESCRIPTIONS = {
    "data_query_records": (
        "Use for filtered warehouse counts or small record samples. "
        "Do not use for maps, trends, forecasts, spatial containment, or comparisons."
    ),
    "data_query_rank": (
        "Use for a top-N ranking inside one dataset (county, utility, or "
        "circuit). Do not rank across datasets (CPUC vs CAL FIRE vs US). "
        "Do not use for EPSS-by-utility or US-by-state."
    ),
    "data_query_spatial": (
        "Use for coordinate context or counts inside one IOU/HFTD polygon. "
        "Do not use for attribute-tagged utility counts."
    ),
    "visualization_create": (
        "Use to create a map layer or time series artifact. "
        "Do not use for scalar comparisons or forecasts."
    ),
    "visualization_inspect": (
        "Use for one utility territory or one event/circuit detail. "
        "Do not use for aggregate analysis."
    ),
    "risk_forecast": (
        "Use for fitted historical ignition risk at one California place/date "
        "(cell_id, lat+lon, county, or PGE/SCE/SDGE). "
        "Do not use for current fires, national risk, or optimization."
    ),
    "comparison_run": (
        "Use for one metric across utilities, regions, or two periods. "
        "kind=utilities for two+ IOUs in one date range; kind=regions for "
        "HFTD/county lists; kind=periods only when one scope has two ranges. "
        "Do not use for cross-dataset counts (CPUC vs US ignitions, or any "
        "two warehouse datasets); call data_query_records once per dataset."
    ),
}

TRIMMED_TOOL_DESCRIPTIONS = {
    "data_query_records": "Filtered counts or samples; not spatial containment.",
    "data_query_rank": "Top-N inside one dataset; never mix CPUC/CAL FIRE/US.",
    "data_query_spatial": "Point context or counts inside one IOU/HFTD polygon.",
    "visualization_create": "Create a map layer or time series.",
    "visualization_inspect": "Inspect one territory, event, or circuit.",
    "risk_forecast": "Historical fitted risk for one place (cell, point, county, or IOU) and date.",
    "comparison_run": (
        "One metric: kind=utilities (IOUs, one range), regions, or "
        "periods (one scope, two ranges). Not CPUC-vs-US."
    ),
}


# Fields the harness or deterministic router supplies, so exposing them to the
# model only adds catalog tokens. Diagnostic transcripts showed bbox in
# particular triggering re-litigation of whether a utility territory needs one.
HARNESS_MANAGED_FIELDS: dict[str, set[str]] = {
    "data_query_records": {"limit", "min_acres", "bbox"},
    "data_query_rank": {"limit"},
    "data_query_spatial": set(),
    "visualization_create": set(),
    "visualization_inspect": set(),
    "risk_forecast": {"lookback_days"},
    "comparison_run": {"normalize", "ignition_definition"},
}


def openai_tools(
    names: list[str] | None = None,
    *,
    profile: Literal["full", "trimmed", "lean", "lean_enums"] = "full",
) -> list[dict[str, Any]]:
    selected = names or list(TOOL_MODELS)
    unknown = set(selected) - set(TOOL_MODELS)
    if unknown:
        raise ValueError(f"Unknown tool names: {sorted(unknown)}")
    descriptions = (
        TOOL_DESCRIPTIONS if profile == "full" else TRIMMED_TOOL_DESCRIPTIONS
    )
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": descriptions[name],
                "parameters": _model_tool_schema(
                    TOOL_MODELS[name].model_json_schema(),
                    profile=profile,
                    drop_fields=(
                        HARNESS_MANAGED_FIELDS[name]
                        if profile in {"lean", "lean_enums"}
                        else set()
                    ),
                ),
            },
        }
        for name in selected
    ]


def openai_selector_tools(names: list[str]) -> list[dict[str, Any]]:
    """Expose tool choice without argument schemas; the harness compiles slots.

    Unadopted prototype. Wiring this in would measure list-picking rather than
    routing, so it stays out of the orchestrator pending an explicit decision.
    """
    unknown = set(names) - set(TOOL_MODELS)
    if unknown:
        raise ValueError(f"Unknown tool names: {sorted(unknown)}")
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": TRIMMED_TOOL_DESCRIPTIONS[name],
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }
        for name in names
    ]


def openai_planned_tools(
    names: list[str],
    planned_calls: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Expose selected tool names with harness-fixed argument choices.

    Unadopted prototype; see openai_selector_tools.
    """
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    for name, arguments in planned_calls:
        if name in grouped:
            grouped[name].append(arguments)

    tools: list[dict[str, Any]] = []
    for name in names:
        variants = grouped[name]
        if not variants:
            tools.extend(openai_selector_tools([name]))
            continue
        keys = sorted(set.intersection(*(set(item) for item in variants)))
        properties: dict[str, Any] = {}
        for key in keys:
            values: list[Any] = []
            for item in variants:
                if item[key] not in values:
                    values.append(item[key])
            schema: dict[str, Any] = {"type": _json_type(values[0])}
            if len(values) == 1:
                schema["const"] = values[0]
            else:
                schema["enum"] = values
            properties[key] = schema
        repeat = (
            f" Call {len(variants)} times, once per listed argument variant."
            if len(variants) > 1
            else ""
        )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": (
                        f"{TRIMMED_TOOL_DESCRIPTIONS[name]} Arguments are "
                        f"harness-fixed; call immediately.{repeat}"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": keys,
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    return "string"


def _model_tool_schema(
    value: dict[str, Any],
    *,
    profile: Literal["full", "trimmed", "lean", "lean_enums"],
    drop_fields: set[str] | None = None,
) -> dict[str, Any]:
    compact = _compact_tool_schema(value)
    if profile == "full":
        return compact
    # lean_enums keeps long option lists: under constrained decoding they become
    # grammar alternatives that force a valid value rather than context to read.
    simplified = compact if profile == "lean_enums" else _trim_long_enums(compact)
    definitions = simplified.get("$defs") or {}
    flattened = _inline_local_refs(simplified, definitions)
    flattened.pop("$defs", None)
    if drop_fields:
        properties = flattened.get("properties")
        if isinstance(properties, dict):
            flattened["properties"] = {
                key: item
                for key, item in properties.items()
                if key not in drop_fields
            }
        required = flattened.get("required")
        if isinstance(required, list):
            flattened["required"] = [
                key for key in required if key not in drop_fields
            ]
    return flattened


def _compact_tool_schema(value: Any) -> Any:
    """Remove display-only JSON Schema tokens while preserving constraints."""
    if isinstance(value, list):
        return [_compact_tool_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned = {
        key: _compact_tool_schema(item)
        for key, item in value.items()
        if key not in {"title", "default"}
    }
    any_of = cleaned.get("anyOf")
    if (
        isinstance(any_of, list)
        and len(any_of) == 2
        and any(
            isinstance(item, dict) and item.get("type") == "null"
            for item in any_of
        )
    ):
        # Omitted fields are optional; explicit null only bloats tool context.
        return next(
            item
            for item in any_of
            if not (isinstance(item, dict) and item.get("type") == "null")
        )
    return cleaned


def _trim_long_enums(value: Any) -> Any:
    if isinstance(value, list):
        return [_trim_long_enums(item) for item in value]
    if not isinstance(value, dict):
        return value
    if isinstance(value.get("enum"), list) and len(value["enum"]) > 4:
        # The executor still validates the complete Pydantic enum. Omitting long
        # option lists here reduces small-model catalog deliberation.
        return {"type": value.get("type", "string")}
    return {key: _trim_long_enums(item) for key, item in value.items()}


def _inline_local_refs(value: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_inline_local_refs(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    ref = value.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        name = ref.rsplit("/", 1)[-1]
        return _inline_local_refs(definitions.get(name, {"type": "string"}), definitions)
    return {
        key: _inline_local_refs(item, definitions)
        for key, item in value.items()
        if key != "$defs"
    }


class EvidenceClaim(StrictModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class AgentAnswer(StrictModel):
    status: Literal["answer", "clarification", "unsupported", "error"]
    answer: str
    claims: list[EvidenceClaim] = Field(default_factory=list)


# Workspace panel identity for a grounded stat card. The UI applies stat_mode
# instead of rendering the card as a frozen cited number.
MedicalStatMode = Literal["medical_exposure", "summary"]
MedicalViewId = Literal["medical-exposure", "summary-stats"]
SeriesMode = Literal[
    "yearly",
    "seasonal",
    "cumulative_acres",
    "customer_events",
    "regional",
    "timeline",
]
# Map canvas mode. risk and residual draw the cNHPP grid for one historical day.
MapMode = Literal["events", "risk", "residual"]


class AskRequest(StrictModel):
    question: str = Field(..., min_length=2, max_length=2000)


class Qualification(StrictModel):
    id: str
    text: str
    source: str


class DecisionSource(StrictModel):
    """Who made the answer, clarify, or refuse decision. Never Jev's raw payload."""

    source: Literal["backstop", "jev", "router"]
    mode: str
    rule: str | None = None  # backstop: the router rule id
    disposition: Literal["answer", "clarify", "unsupported"] | None = None  # jev
    confidence: float | None = None  # jev
    why: str | None = None  # router: why Jev did not decide
    jev_disposition: str | None = None  # router, below the gate or agreeing
    jev_confidence: float | None = None


class AskResponse(StrictModel):
    request_id: str
    status: str
    answer_text: str
    decision_source: DecisionSource | None = None
    route: dict[str, Any]
    qualifications: list[Qualification] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    trajectory: list[dict[str, Any]] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    model_metrics: dict[str, int] = Field(default_factory=dict)
    views: list[dict[str, Any]] = Field(default_factory=list)
    view_status: Literal["applied", "planner_fallback", "none"] = "none"
    view_scope: dict[str, Any] = Field(default_factory=dict)
