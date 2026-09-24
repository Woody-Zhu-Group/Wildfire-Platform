"""Harness-owned canvas view planner.

Component specs are derived from successful tool executions (same guarantees as
tools: schema, validation, grounding). The model does not emit render code or a
render_view tool. Invalid specs are rejected; the answer still stands.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from services.agent.schemas import (
    MapMode,
    MedicalStatMode,
    MedicalViewId,
    Metric,
    SeriesMode,
    StrictModel,
)
from services.agent.tools import ToolExecution
from services.shared.dataset_registry import (
    COUNT_MAP_DATASETS as _COUNT_MAP_DATASETS,
    DQ_TO_VIZ as _DQ_TO_VIZ,
    HDW_YEARS as _HDW_YEARS,
    STAT_LABELS as _STAT_LABELS,
)

ViewStatus = Literal["applied", "planner_fallback", "none"]
ComponentType = Literal[
    "map",
    "time_series",
    "comparison",
    "record_table",
    "stat_card",
    "spatial_context",
]

_SPATIAL_COUNT_LABELS = {
    "ignitions": "CPUC ignitions",
    "epss_outages": "EPSS outages",
    "calfire_incidents": "CAL FIRE incidents",
}
# California event layers the HDW grid can play under (not the CONUS sample).
_HDW_EVENT_LAYERS = frozenset({"ignitions", "epss", "psps", "calfire"})
# Datasets the workspace timeline can overlay on one axis.
_TIMELINE_DATASETS = ("ignitions", "epss", "calfire")
# Risk calls that score one historical day; a grid map may cite either.
_RISK_DAY_TOOLS = frozenset({"risk_forecast", "risk_surface"})
_MAX_VISUAL = 2
_MAX_STATS = 3
_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class GroundingError(ValueError):
    """A component spec is not traceable to tool evidence."""


class MapViewParams(StrictModel):
    datasets: list[str] = Field(default_factory=list, max_length=4)
    year: int | None = Field(None, ge=1900, le=2100)
    start_date: str | None = None
    end_date: str | None = None
    utility: str | None = None
    county: str | None = None
    extent: Literal["auto_fit", "statewide", "conus", "territory"] = "statewide"
    highlight_ids: list[str] = Field(default_factory=list)
    show_territory: bool = False
    show_hftd: bool = False
    show_hdw: bool = False
    map_mode: MapMode = "events"
    risk_date: str | None = None

    @model_validator(mode="after")
    def validate_extent(self) -> "MapViewParams":
        allowed = {"ignitions", "us_ignitions", "epss", "psps", "calfire", "hftd"}
        for item in self.datasets:
            if item not in allowed:
                raise ValueError(f"unknown map dataset {item!r}")
        if self.map_mode == "events":
            if self.risk_date is not None:
                raise ValueError("risk_date requires map_mode risk or residual")
        else:
            # The grid covers every cell for one day; no event layer rides along.
            if not self.risk_date or self.datasets or self.show_hdw:
                raise ValueError(f"map_mode={self.map_mode} takes only risk_date")
            date.fromisoformat(self.risk_date)
        if self.show_hdw:
            if len(self.datasets) != 1 or self.datasets[0] not in _HDW_EVENT_LAYERS:
                raise ValueError("HDW plays over exactly one California event layer")
            if self.year not in _HDW_YEARS:
                raise ValueError(f"HDW has no playback file for {self.year}")
        if self.extent == "territory" and not self.utility:
            raise ValueError("extent=territory requires utility")
        if self.extent == "conus" and "us_ignitions" not in self.datasets:
            raise ValueError("extent=conus requires us_ignitions")
        if self.county and "us_ignitions" in self.datasets and self.datasets == [
            "us_ignitions"
        ]:
            raise ValueError("us_ignitions cannot take a county filter")
        return self


class TimeSeriesViewParams(StrictModel):
    dataset: str
    interval: Literal["daily", "weekly", "monthly"] = "weekly"
    year: int | None = Field(None, ge=1900, le=2100)
    start_date: str | None = None
    end_date: str | None = None
    utility: str | None = None
    county: str | None = None
    incident_type_mode: Literal["wildfire_default", "all", "untyped"] | None = None
    series_mode: SeriesMode | None = None
    datasets: list[str] | None = None

    @model_validator(mode="after")
    def validate_series(self) -> "TimeSeriesViewParams":
        if self.series_mode == "timeline":
            datasets = self.datasets or []
            if len(datasets) < 2 or len(set(datasets)) != len(datasets):
                raise ValueError("timeline needs two or more distinct datasets")
            if any(item not in _TIMELINE_DATASETS for item in datasets):
                raise ValueError("timeline datasets must be CPUC, EPSS, or CAL FIRE")
            if datasets[0] != self.dataset:
                raise ValueError("timeline dataset must be the first of datasets")
        elif self.datasets is not None:
            raise ValueError("datasets is only for series_mode timeline")
        if self.dataset not in {
            "ignitions",
            "us_ignitions",
            "epss",
            "psps",
            "calfire",
        }:
            raise ValueError(f"time_series dataset {self.dataset!r} is not chartable")
        if self.interval == "weekly" and self.year is None:
            raise ValueError("weekly time series requires year")
        if self.county and self.dataset == "us_ignitions":
            raise ValueError("us_ignitions cannot take a county filter")
        return self


class ComparisonViewParams(StrictModel):
    kind: Literal["utilities", "regions", "periods", "ranking"]
    metric: Metric
    normalize: Literal["none", "per_circuit", "per_km2"] = "none"
    ignition_definition: Literal["attribute", "spatial"] | None = None
    utilities: list[str] | None = None
    region_type: Literal["county", "hftd"] | None = None
    regions: list[str] | None = None
    scope_type: Literal["utility", "county", "hftd"] | None = None
    scope: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    period_a_start: str | None = None
    period_a_end: str | None = None
    period_b_start: str | None = None
    period_b_end: str | None = None
    dataset: str | None = None
    group_by: str | None = None
    year: int | None = Field(None, ge=1900, le=2100)
    limit: int | None = Field(None, ge=1, le=25)


class RecordTableViewParams(StrictModel):
    dataset: str
    columns: Literal["default"] = "default"
    row_limit: int = Field(25, ge=1, le=25)
    year: int | None = Field(None, ge=1900, le=2100)
    start_date: str | None = None
    end_date: str | None = None
    utility: str | None = None
    county: str | None = None


class StatCardViewParams(StrictModel):
    kind: Literal["count", "risk", "spatial_metric"]
    value: float
    label: str
    scope: str
    period: str
    source_dataset: str
    unit: Literal["events", "risk", "percentile"] | None = "events"
    stat_mode: MedicalStatMode | None = None
    view_id: MedicalViewId | None = None
    year: int | None = Field(None, ge=1900, le=2100)
    start_date: str | None = None
    end_date: str | None = None
    utility: str | None = None
    county: str | None = None


class SpatialContextViewParams(StrictModel):
    lat: float
    lon: float
    iou: str | None = None
    hftd_tier: str | None = None
    county: str | None = None
    cell_id: int | None = None


class ComponentSpec(StrictModel):
    type: ComponentType
    params: dict[str, Any]
    evidence_ids: list[str] = Field(min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_params_for_type(self) -> "ComponentSpec":
        _params_model(self.type).model_validate(self.params)
        return self


class PlannedViews(StrictModel):
    views: list[ComponentSpec] = Field(default_factory=list)
    view_status: ViewStatus = "none"
    view_scope: dict[str, Any] = Field(default_factory=dict)


def _params_model(component_type: str) -> type[StrictModel]:
    return {
        "map": MapViewParams,
        "time_series": TimeSeriesViewParams,
        "comparison": ComparisonViewParams,
        "record_table": RecordTableViewParams,
        "stat_card": StatCardViewParams,
        "spatial_context": SpatialContextViewParams,
    }[component_type]


def format_view_scope(scope: dict[str, Any]) -> str:
    """Human label for stale-view chrome, e.g. 'showing 2023, PG&E'."""
    bits: list[str] = []
    year = scope.get("year")
    years = scope.get("years") or []
    if year is not None:
        bits.append(str(year))
    elif len(years) == 1:
        bits.append(str(years[0]))
    elif len(years) > 1:
        bits.append(", ".join(str(item) for item in years))
    elif scope.get("start_date") and scope.get("end_date"):
        bits.append(f"{scope['start_date']} → {scope['end_date']}")
    utility = scope.get("utility")
    utilities = scope.get("utilities") or []
    if utility:
        bits.append(str(utility))
    elif len(utilities) == 1:
        bits.append(str(utilities[0]))
    elif len(utilities) > 1:
        bits.append(" / ".join(str(item) for item in utilities))
    county = scope.get("county")
    if county:
        bits.append(f"{county} County" if "county" not in str(county).lower() else str(county))
    return ", ".join(bits)


def _medical_exposure_views(primary: list[ToolExecution]) -> list[ComponentSpec]:
    """One EPSS exposure card, grounded on the count that selected the year."""
    for item in primary:
        args = item.arguments or {}
        summary = item.summary or {}
        if item.tool != "data_query_records":
            continue
        dataset = str(summary.get("dataset") or args.get("dataset") or "")
        if dataset not in {"epss_outages", "epss"}:
            continue
        if (summary.get("result_mode") or args.get("result_mode")) != "count":
            continue
        if summary.get("total") is None:
            continue
        year = _year_from_args(args, summary)
        ref = (item.artifact or {}).get("ref")
        params = StatCardViewParams(
            kind="count",
            value=float(summary.get("total") or 0),
            label="EPSS outages",
            scope=_scope_label(args, summary),
            period=_period_label(args, summary, year=year),
            source_dataset="epss_outages",
            unit="events",
            stat_mode="medical_exposure",
            view_id="medical-exposure",
            year=year,
            start_date=_date_str(args.get("start_date")),
            end_date=_date_str(args.get("end_date")),
            utility=args.get("utility"),
            county=args.get("county"),
        )
        return [
            ComponentSpec(
                type="stat_card",
                params=params.model_dump(mode="json"),
                evidence_ids=[item.evidence_id],
                artifact_refs=[ref] if ref else [],
            )
        ]
    return []


def _summary_stat_views(primary: list[ToolExecution]) -> list[ComponentSpec]:
    """One live summary panel, grounded on the count for that dataset and window."""
    for item in primary:
        args = item.arguments or {}
        summary = item.summary or {}
        if item.tool != "data_query_records":
            continue
        dataset = str(summary.get("dataset") or args.get("dataset") or "")
        if not dataset:
            continue
        if (summary.get("result_mode") or args.get("result_mode")) != "count":
            continue
        if summary.get("total") is None:
            continue
        year = _year_from_args(args, summary)
        ref = (item.artifact or {}).get("ref")
        params = StatCardViewParams(
            kind="count",
            value=float(summary.get("total") or 0),
            label="Summary",
            scope=_scope_label(args, summary),
            period=_period_label(args, summary, year=year),
            source_dataset=dataset,
            unit="events",
            stat_mode="summary",
            view_id="summary-stats",
            year=year,
            start_date=_date_str(args.get("start_date")),
            end_date=_date_str(args.get("end_date")),
            utility=args.get("utility"),
            county=args.get("county"),
        )
        return [
            ComponentSpec(
                type="stat_card",
                params=params.model_dump(mode="json"),
                evidence_ids=[item.evidence_id],
                artifact_refs=[ref] if ref else [],
            )
        ]
    return []


def _timeline_views(
    primary: list[ToolExecution], requested: list[str]
) -> list[ComponentSpec]:
    """One multi-dataset timeline, only when every named dataset has its own series."""
    found: dict[str, ToolExecution] = {}
    for item in primary:
        args = item.arguments or {}
        if item.tool != "visualization_create" or args.get("kind") != "time_series":
            continue
        dataset = str(args.get("dataset"))
        if dataset in requested and dataset not in found:
            found[dataset] = item
    if len(requested) < 2 or set(found) != set(requested):
        return []
    window = {
        tuple(
            (item.arguments or {}).get(key)
            for key in ("interval", "year", "start_date", "end_date", "utility", "county")
        )
        for item in found.values()
    }
    if len(window) != 1:
        return []
    first = found[requested[0]]
    args = first.arguments or {}
    params = TimeSeriesViewParams(
        dataset=requested[0],
        datasets=list(requested),
        interval=args.get("interval") or "weekly",
        year=_year_from_args(args, first.summary or {}),
        start_date=_date_str(args.get("start_date")),
        end_date=_date_str(args.get("end_date")),
        utility=args.get("utility"),
        county=args.get("county"),
        series_mode="timeline",
    )
    refs = [
        (found[name].artifact or {}).get("ref")
        for name in requested
        if (found[name].artifact or {}).get("ref")
    ]
    return [
        ComponentSpec(
            type="time_series",
            params=params.model_dump(mode="json"),
            evidence_ids=[found[name].evidence_id for name in requested],
            artifact_refs=refs,
        )
    ]


def _risk_grid_map(primary: list[ToolExecution], map_mode: str) -> ComponentSpec | None:
    """Risk surface or residual grid for the day a cited risk call scored."""
    for item in primary:
        if item.tool not in _RISK_DAY_TOOLS:
            continue
        scored = _date_str((item.summary or {}).get("date") or (item.arguments or {}).get("date"))
        if not scored:
            continue
        return ComponentSpec(
            type="map",
            params=MapViewParams(
                datasets=[],
                map_mode=map_mode,
                risk_date=scored,
            ).model_dump(mode="json"),
            evidence_ids=[item.evidence_id],
            artifact_refs=[],
        )
    return None


def _stamp_hdw(visuals: list[ComponentSpec], show_hdw: Any) -> None:
    if show_hdw is not True:
        return
    for spec in visuals:
        if spec.type == "map" and spec.artifact_refs:
            spec.params["show_hdw"] = True


def plan_views(
    executions: list[ToolExecution],
    *,
    status: str,
    slots: dict[str, Any] | None = None,
) -> PlannedViews:
    """Build grounded canvas specs from successful primary tool results."""
    slots = slots or {}
    scope = view_scope(slots, executions)
    if status != "answer":
        return PlannedViews(views=[], view_status="none", view_scope=scope)

    primary = [item for item in executions if item.ok and not item.qualification_call]
    if not primary:
        return PlannedViews(views=[], view_status="none", view_scope=scope)

    if slots.get("stat_mode") == "medical_exposure":
        try:
            views = _medical_exposure_views(primary)
            if not views:
                return PlannedViews(
                    views=[], view_status="planner_fallback", view_scope=scope
                )
            grounded = ground_views(views, executions)
        except (GroundingError, ValidationError, KeyError, TypeError, ValueError):
            return PlannedViews(
                views=[], view_status="planner_fallback", view_scope=scope
            )
        return PlannedViews(views=grounded, view_status="applied", view_scope=scope)

    if slots.get("stat_mode") == "summary":
        try:
            views = _summary_stat_views(primary)
            if not views:
                return PlannedViews(
                    views=[], view_status="planner_fallback", view_scope=scope
                )
            grounded = ground_views(views, executions)
        except (GroundingError, ValidationError, KeyError, TypeError, ValueError):
            return PlannedViews(
                views=[], view_status="planner_fallback", view_scope=scope
            )
        return PlannedViews(views=grounded, view_status="applied", view_scope=scope)

    if slots.get("series_mode") == "timeline":
        try:
            views = _timeline_views(primary, list(slots.get("timeline_datasets") or []))
            if not views:
                return PlannedViews(
                    views=[], view_status="planner_fallback", view_scope=scope
                )
            grounded = ground_views(views, executions)
        except (GroundingError, ValidationError, KeyError, TypeError, ValueError):
            return PlannedViews(
                views=[], view_status="planner_fallback", view_scope=scope
            )
        return PlannedViews(views=grounded, view_status="applied", view_scope=scope)

    try:
        stats: list[ComponentSpec] = []
        visuals: list[ComponentSpec] = []
        for item in primary:
            for spec in _specs_for_execution(item):
                if spec.type == "stat_card":
                    stats.append(spec)
                else:
                    visuals.append(spec)
        if slots.get("map_mode") in {"risk", "residual"}:
            grid = _risk_grid_map(primary, slots["map_mode"])
            if grid is not None:
                visuals.append(grid)
        visuals = _drop_derived_maps_if_conflicting(visuals)
        visuals = _drop_derived_series_if_explicit(visuals)
        visuals = _cap_visuals(visuals)
        _stamp_series_mode(visuals, slots.get("series_mode"))
        _stamp_hdw(visuals, slots.get("show_hdw"))
        views = _cap_stats(stats) + visuals
        if not views:
            return PlannedViews(views=[], view_status="none", view_scope=scope)
        grounded = ground_views(views, executions)
    except (GroundingError, ValidationError, KeyError, TypeError, ValueError):
        return PlannedViews(views=[], view_status="planner_fallback", view_scope=scope)
    return PlannedViews(views=grounded, view_status="applied", view_scope=scope)


def dump_planned(planned: PlannedViews) -> dict[str, Any]:
    """Wire fields for AskResponse and ad-hoc error payloads."""
    return {
        "views": [spec.model_dump(mode="json") for spec in planned.views],
        "view_status": planned.view_status,
        "view_scope": planned.view_scope,
    }


def empty_views_payload(
    slots: dict[str, Any] | None = None,
    executions: list[ToolExecution] | None = None,
) -> dict[str, Any]:
    return dump_planned(
        PlannedViews(
            views=[],
            view_status="none",
            view_scope=view_scope(slots or {}, executions),
        )
    )


def scope_diverges(
    answer_scope: dict[str, Any],
    canvas_scope: dict[str, Any],
) -> bool:
    """True when the answer's year, utility, or county is not what the canvas shows.

    Used later to mark keep-until-replaced canvas as stale when view_status is
    none or planner_fallback and no new view was emitted.
    """
    for key in ("utility", "county"):
        left = answer_scope.get(key)
        right = canvas_scope.get(key)
        if left in (None, "", []):
            continue
        if right in (None, "", []) or str(left) != str(right):
            return True
    answer_years = _scope_years(answer_scope)
    canvas_years = _scope_years(canvas_scope)
    if answer_years and canvas_years and answer_years != canvas_years:
        return True
    if answer_years and not canvas_years:
        return True
    return False


def _scope_years(scope: dict[str, Any]) -> list[int]:
    if scope.get("year") is not None:
        return [int(scope["year"])]
    return [int(item) for item in (scope.get("years") or []) if item is not None]


def view_scope(
    slots: dict[str, Any],
    executions: list[ToolExecution] | None = None,
) -> dict[str, Any]:
    """Answer-side year/utility/county for stale-canvas comparison."""
    utilities = [
        item
        for item in (slots.get("utilities") or [])
        if isinstance(item, str) and item
    ]
    time_resolution = slots.get("time_resolution") or {}
    scope: dict[str, Any] = {
        "year": slots.get("year") or time_resolution.get("year"),
        "years": list(slots.get("years") or time_resolution.get("years") or []),
        "utility": utilities[0] if len(utilities) == 1 else None,
        "utilities": utilities,
        "county": slots.get("county"),
        "start_date": slots.get("start_date") or time_resolution.get("start_date"),
        "end_date": slots.get("end_date") or time_resolution.get("end_date"),
    }
    for item in executions or []:
        if not item.ok or item.qualification_call:
            continue
        args = item.arguments or {}
        filters = (item.summary or {}).get("filters") or {}
        utility = args.get("utility") or filters.get("utility")
        if isinstance(utility, str) and utility and utility not in scope["utilities"]:
            scope["utilities"].append(utility)
        if scope["utility"] is None and isinstance(utility, str) and utility:
            if not args.get("utilities"):
                scope["utility"] = utility
        county = args.get("county") or filters.get("county")
        if county and not scope["county"]:
            scope["county"] = county
        year = args.get("year") or filters.get("year")
        if year is not None and scope["year"] is None:
            scope["year"] = year
        start = args.get("start_date") or filters.get("start_date")
        end = args.get("end_date") or filters.get("end_date")
        if start and not scope["start_date"]:
            scope["start_date"] = str(start)
        if end and not scope["end_date"]:
            scope["end_date"] = str(end)
    if scope["utility"] is None and len(scope["utilities"]) == 1:
        scope["utility"] = scope["utilities"][0]
    return scope


def ground_views(
    specs: list[ComponentSpec],
    executions: list[ToolExecution],
) -> list[ComponentSpec]:
    """Reject any spec whose parameters are not in cited tool evidence."""
    by_id = {
        item.evidence_id: item
        for item in executions
        if item.ok and not item.qualification_call
    }
    artifact_refs = {
        (item.artifact or {}).get("ref")
        for item in executions
        if item.ok and item.artifact
    }
    grounded: list[ComponentSpec] = []
    for spec in specs:
        cited = _cited(spec, by_id)
        _params_model(spec.type).model_validate(spec.params)
        for ref in spec.artifact_refs:
            if ref not in artifact_refs:
                raise GroundingError(f"unknown artifact_ref {ref}")
        if spec.type == "stat_card":
            _ground_stat(spec, cited)
        elif spec.type == "map":
            _ground_map(spec, cited)
        elif spec.type == "time_series":
            _ground_time_series(spec, cited)
        elif spec.type == "comparison":
            _ground_comparison(spec, cited)
        elif spec.type == "record_table":
            _ground_records(spec, cited)
        elif spec.type == "spatial_context":
            _ground_point(spec, cited)
        else:
            raise GroundingError(f"unknown component type {spec.type}")
        grounded.append(spec)
    return grounded


def _cited(
    spec: ComponentSpec, by_id: dict[str, ToolExecution]
) -> list[ToolExecution]:
    if not spec.evidence_ids:
        raise GroundingError("component spec missing evidence_ids")
    found: list[ToolExecution] = []
    for evidence_id in spec.evidence_ids:
        item = by_id.get(evidence_id)
        if item is None:
            raise GroundingError(f"evidence_id {evidence_id} is not a primary tool result")
        found.append(item)
    return found


def _ground_stat(spec: ComponentSpec, cited: list[ToolExecution]) -> None:
    params = spec.params
    item = cited[0]
    summary = item.summary or {}
    kind = params.get("kind")
    value = params.get("value")
    if kind == "count":
        expected = summary.get("total")
        if not _numbers_equal(value, expected):
            raise GroundingError(
                f"stat_card value {value} != evidence total {expected}"
            )
        _match_filters(params, item, keys=("utility", "county", "year"))
        dataset = params.get("source_dataset")
        evidenced = summary.get("dataset") or item.arguments.get("dataset")
        if dataset and evidenced and dataset != evidenced:
            raise GroundingError(
                f"stat_card dataset {dataset!r} != evidence {evidenced!r}"
            )
        return
    if kind == "risk":
        unit = params.get("unit") or "risk"
        if unit == "percentile":
            allowed = (
                summary.get("local_percentile"),
                summary.get("statewide_percentile"),
            )
            if any(
                item is not None
                and (
                    _numbers_equal(value, item)
                    or _numbers_equal(value, round(float(item)))
                )
                for item in allowed
            ):
                return
            raise GroundingError(
                f"stat_card percentile {value} != local/statewide evidence {allowed}"
            )
        expected = summary.get("risk")
        if not _numbers_equal(value, expected):
            raise GroundingError(f"stat_card risk {value} != evidence {expected}")
        return
    if kind == "spatial_metric":
        counts = summary.get("counts") or {}
        source = params.get("source_dataset")
        expected = counts.get(source)
        if expected is None:
            # allow viz-style keys
            expected = counts.get(_DQ_TO_VIZ.get(source or "", source))
        if not _numbers_equal(value, expected):
            raise GroundingError(
                f"stat_card spatial value {value} != counts[{source!r}] {expected}"
            )
        return
    raise GroundingError(f"unsupported stat_card kind {kind!r}")


def _ground_map(spec: ComponentSpec, cited: list[ToolExecution]) -> None:
    params = spec.params
    if (params.get("map_mode") or "events") != "events":
        risk_date = params.get("risk_date")
        scored = {
            _date_str((item.summary or {}).get("date") or (item.arguments or {}).get("date"))
            for item in cited
            if item.tool in _RISK_DAY_TOOLS
        }
        if risk_date not in scored:
            raise GroundingError(f"risk_date {risk_date!r} is not a scored risk date")
        return
    if params.get("show_hdw") and not any(
        item.tool == "visualization_create" and (item.arguments or {}).get("kind") == "map"
        for item in cited
    ):
        raise GroundingError("HDW overlay needs a cited event map layer")
    datasets = list(params.get("datasets") or [])
    evidenced: set[str] = set()
    for item in cited:
        args = item.arguments or {}
        if item.tool == "visualization_create" and args.get("kind") == "map":
            evidenced.add(str(args.get("dataset")))
            _match_filters(params, item, keys=("utility", "county", "year"))
        elif item.tool == "visualization_inspect" and args.get("kind") == "utility_territory":
            _match_filters(params, item, keys=("utility",))
        elif item.tool == "data_query_records":
            ds = (item.summary or {}).get("dataset") or args.get("dataset")
            viz = _DQ_TO_VIZ.get(str(ds), str(ds) if ds else "")
            if viz:
                evidenced.add(viz)
            _match_filters(params, item, keys=("utility", "county", "year"))
    if datasets and not set(datasets).issubset(evidenced | {"hftd"}):
        # hftd may be a background flag rather than a dedicated map tool
        leftover = set(datasets) - evidenced
        leftover.discard("hftd")
        if leftover:
            raise GroundingError(f"map datasets {leftover} not in cited map tools")


_SERIES_MODE_DATASET = {
    "cumulative_acres": "calfire",
    "customer_events": "psps",
    "regional": "epss",
}


def _stamp_series_mode(visuals: list[ComponentSpec], series_mode: Any) -> None:
    if series_mode not in _SERIES_MODE_DATASET and series_mode not in {
        "yearly",
        "seasonal",
    }:
        return
    for spec in visuals:
        if spec.type == "time_series":
            spec.params["series_mode"] = series_mode


def _ground_time_series(spec: ComponentSpec, cited: list[ToolExecution]) -> None:
    params = spec.params
    if params.get("series_mode") == "timeline":
        datasets = list(params.get("datasets") or [])
        if len(cited) != len(datasets):
            raise GroundingError("timeline needs one cited series per dataset")
        for dataset, item in zip(datasets, cited):
            args = item.arguments or {}
            if item.tool != "visualization_create" or args.get("kind") != "time_series":
                raise GroundingError("timeline evidence must be time series tools")
            if args.get("dataset") != dataset:
                raise GroundingError(f"timeline dataset {dataset} does not match evidence")
            if params.get("interval") != (args.get("interval") or "weekly"):
                raise GroundingError("time_series interval does not match evidence")
            _match_filters(
                params, item, keys=("utility", "county", "year", "start_date", "end_date")
            )
        return
    item = cited[0]
    args = item.arguments or {}
    required = _SERIES_MODE_DATASET.get(params.get("series_mode"))
    if required and params.get("dataset") != required:
        raise GroundingError(
            f"series_mode {params.get('series_mode')} requires dataset {required}"
        )
    if item.tool == "data_query_records":
        ds = (item.summary or {}).get("dataset") or args.get("dataset")
        viz = _DQ_TO_VIZ.get(str(ds), str(ds) if ds else "")
        if params.get("dataset") != viz:
            raise GroundingError("time_series dataset does not match evidence")
        _match_filters(
            params, item, keys=("utility", "county", "year", "start_date", "end_date")
        )
        return
    if args.get("dataset") != params.get("dataset"):
        raise GroundingError("time_series dataset does not match evidence")
    interval = args.get("interval") or "weekly"
    if params.get("interval") != interval:
        raise GroundingError("time_series interval does not match evidence")
    _match_filters(params, item, keys=("utility", "county", "year"))


def _ground_comparison(spec: ComponentSpec, cited: list[ToolExecution]) -> None:
    item = cited[0]
    args = item.arguments or {}
    params = spec.params
    if item.tool == "data_query_rank":
        if params.get("kind") != "ranking":
            raise GroundingError("comparison kind does not match ranking evidence")
        expected = (item.summary or {}).get("canvas_metric")
        if expected and params.get("metric") != expected:
            raise GroundingError("comparison metric does not match ranking evidence")
        _match_filters(params, item, keys=("start_date", "end_date", "year", "utility", "county"))
        return
    if args.get("kind") and params.get("kind") != args.get("kind"):
        raise GroundingError("comparison kind does not match evidence")
    if args.get("metric") and params.get("metric") != args.get("metric"):
        raise GroundingError("comparison metric does not match evidence")
    _match_filters(params, item, keys=("start_date", "end_date"))


def _ground_records(spec: ComponentSpec, cited: list[ToolExecution]) -> None:
    item = cited[0]
    params = spec.params
    evidenced = (item.summary or {}).get("dataset") or item.arguments.get("dataset")
    if params.get("dataset") != evidenced:
        raise GroundingError("record_table dataset does not match evidence")
    _match_filters(params, item, keys=("utility", "county", "year"))


def _ground_point(spec: ComponentSpec, cited: list[ToolExecution]) -> None:
    item = cited[0]
    summary = item.summary or {}
    params = spec.params
    if not _numbers_equal(params.get("lat"), summary.get("lat")):
        raise GroundingError("spatial_context lat does not match evidence")
    if not _numbers_equal(params.get("lon"), summary.get("lon")):
        raise GroundingError("spatial_context lon does not match evidence")


def _match_filters(
    params: dict[str, Any],
    item: ToolExecution,
    *,
    keys: tuple[str, ...],
) -> None:
    args = item.arguments or {}
    filters = (item.summary or {}).get("filters") or {}
    for key in keys:
        spec_value = params.get(key)
        if spec_value in (None, "", []):
            continue
        evidenced = args.get(key)
        if evidenced in (None, "", []):
            evidenced = filters.get(key)
        if key == "year":
            if not _year_matches(spec_value, evidenced, args, filters):
                raise GroundingError(
                    f"{key}={spec_value!r} is not in evidence (got {evidenced!r})"
                )
            continue
        if evidenced in (None, "", []) and key in {"start_date", "end_date"}:
            continue
        if evidenced is None:
            raise GroundingError(f"{key}={spec_value!r} is not in evidence")
        if str(spec_value) != str(evidenced):
            raise GroundingError(
                f"{key}={spec_value!r} does not match evidence {evidenced!r}"
            )


def _year_matches(
    spec_year: Any,
    evidenced_year: Any,
    args: dict[str, Any],
    filters: dict[str, Any],
) -> bool:
    if evidenced_year is not None and int(spec_year) == int(evidenced_year):
        return True
    start = str(args.get("start_date") or filters.get("start_date") or "")
    return start.startswith(f"{int(spec_year)}-")


def _numbers_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left) - float(right)) < 1e-9
    except (TypeError, ValueError):
        return False


def _specs_for_execution(item: ToolExecution) -> list[ComponentSpec]:
    tool = item.tool
    args = item.arguments or {}
    summary = item.summary or {}
    ref = (item.artifact or {}).get("ref")
    refs = [ref] if ref else []
    evidence = [item.evidence_id]

    if tool == "visualization_create" and args.get("kind") == "map":
        dataset = str(args.get("dataset"))
        utility = args.get("utility")
        county = args.get("county")
        year = _year_from_args(args, summary)
        return [
            ComponentSpec(
                type="map",
                params=MapViewParams(
                    datasets=[dataset],
                    year=year,
                    start_date=_date_str(args.get("start_date")),
                    end_date=_date_str(args.get("end_date")),
                    utility=utility,
                    county=county,
                    extent=_map_extent(dataset, utility, county),
                    show_territory=bool(utility),
                    show_hftd=dataset == "hftd",
                ).model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
    if tool == "visualization_create" and args.get("kind") == "time_series":
        year = _year_from_args(args, summary)
        return [
            ComponentSpec(
                type="time_series",
                params=TimeSeriesViewParams(
                    dataset=str(args.get("dataset")),
                    interval=args.get("interval") or "weekly",
                    year=year,
                    start_date=_date_str(args.get("start_date")),
                    end_date=_date_str(args.get("end_date")),
                    utility=args.get("utility"),
                    county=args.get("county"),
                    incident_type_mode=args.get("incident_type_mode"),
                ).model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
    if tool == "visualization_inspect" and args.get("kind") == "utility_territory":
        utility = args.get("utility")
        return [
            ComponentSpec(
                type="map",
                params=MapViewParams(
                    datasets=[],
                    utility=utility,
                    extent="territory",
                    show_territory=True,
                ).model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
    if tool == "data_query_rank":
        results = summary.get("results") or []
        if not results:
            return []
        year = args.get("year")
        start = _date_str(args.get("start_date"))
        end = _date_str(args.get("end_date"))
        if year and not start:
            start = f"{year}-01-01"
            end = f"{year}-12-31"
        params = ComparisonViewParams(
            kind="ranking",
            metric=summary.get("canvas_metric") or "ignition_count",
            dataset=summary.get("dataset") or args.get("dataset"),
            group_by=summary.get("group_by") or args.get("group_by"),
            year=year,
            limit=args.get("limit") or summary.get("limit"),
            start_date=start,
            end_date=end,
            utilities=[str(args["utility"])] if args.get("utility") else None,
        )
        return [
            ComponentSpec(
                type="comparison",
                params=params.model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
    if tool == "comparison_run":
        kind = args.get("kind") or summary.get("kind")
        if kind == "periods":
            kind = "periods"
        params = ComparisonViewParams(
            kind=kind,
            metric=args.get("metric") or summary.get("metric") or "ignition_count",
            normalize=args.get("normalize") or summary.get("normalize") or "none",
            ignition_definition=args.get("ignition_definition"),
            utilities=_str_list(args.get("utilities")),
            region_type=args.get("region_type"),
            regions=_str_list(args.get("regions")),
            scope_type=args.get("scope_type") or summary.get("scope_type"),
            scope=args.get("scope") or summary.get("scope"),
            start_date=_date_str(args.get("start_date")),
            end_date=_date_str(args.get("end_date")),
            period_a_start=_date_str(args.get("period_a_start")),
            period_a_end=_date_str(args.get("period_a_end")),
            period_b_start=_date_str(args.get("period_b_start")),
            period_b_end=_date_str(args.get("period_b_end")),
        )
        return [
            ComponentSpec(
                type="comparison",
                params=params.model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
    if tool == "data_query_spatial" and summary.get("kind") == "point":
        iou = summary.get("iou") or {}
        cell = summary.get("grid_cell") or {}
        return [
            ComponentSpec(
                type="spatial_context",
                params=SpatialContextViewParams(
                    lat=float(summary["lat"]),
                    lon=float(summary["lon"]),
                    iou=iou.get("utility_name") or iou.get("utility"),
                    hftd_tier=summary.get("hftd_tier"),
                    county=summary.get("county"),
                    cell_id=cell.get("cell_id"),
                ).model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
    if tool == "data_query_spatial" and summary.get("kind") == "summary":
        counts = summary.get("counts") or {}
        period = _period_label(args, summary)
        scope = _spatial_scope_label(args, summary)
        specs: list[ComponentSpec] = []
        for key, label in _SPATIAL_COUNT_LABELS.items():
            if key not in counts:
                continue
            specs.append(
                ComponentSpec(
                    type="stat_card",
                    params=StatCardViewParams(
                        kind="spatial_metric",
                        value=float(counts[key]),
                        label=label,
                        scope=scope,
                        period=period,
                        source_dataset=key,
                        unit="events",
                    ).model_dump(mode="json"),
                    evidence_ids=evidence,
                    artifact_refs=refs,
                )
            )
        return specs
    if tool == "risk_forecast":
        date_value = _date_str(args.get("date") or summary.get("date"))
        scope = summary.get("scope") or {}
        place_name = str(
            scope.get("name")
            or (f"cell {summary.get('cell_id')}" if summary.get("cell_id") is not None else "place")
        )
        specs = [
            ComponentSpec(
                type="stat_card",
                params=StatCardViewParams(
                    kind="risk",
                    value=float(summary.get("risk")),
                    label="P(≥1 ignition)",
                    scope=place_name,
                    period=date_value or "",
                    source_dataset="cnhpp",
                    unit="risk",
                ).model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
        local_p = summary.get("local_percentile")
        if local_p is not None:
            period = summary.get("local_period") or date_value or ""
            specs.append(
                ComponentSpec(
                    type="stat_card",
                    params=StatCardViewParams(
                        kind="risk",
                        value=float(round(float(local_p))),
                        label="High for this place",
                        scope=place_name,
                        period=period,
                        source_dataset="cnhpp",
                        unit="percentile",
                    ).model_dump(mode="json"),
                    evidence_ids=evidence,
                    artifact_refs=refs,
                )
            )
        state_p = summary.get("statewide_percentile")
        if state_p is not None:
            specs.append(
                ComponentSpec(
                    type="stat_card",
                    params=StatCardViewParams(
                        kind="risk",
                        value=float(round(float(state_p))),
                        label="High for California this date",
                        scope=place_name,
                        period=date_value or "",
                        source_dataset="cnhpp",
                        unit="percentile",
                    ).model_dump(mode="json"),
                    evidence_ids=evidence,
                    artifact_refs=refs,
                )
            )
        return specs[:_MAX_STATS]
    if tool == "data_query_records" and summary.get("result_mode") == "count":
        dataset = str(summary.get("dataset") or args.get("dataset"))
        year = _year_from_args(args, summary)
        specs: list[ComponentSpec] = [
            ComponentSpec(
                type="stat_card",
                params=StatCardViewParams(
                    kind="count",
                    value=float(summary.get("total") or 0),
                    label=_STAT_LABELS.get(dataset, dataset),
                    scope=_scope_label(args, summary),
                    period=_period_label(args, summary, year=year),
                    source_dataset=dataset,
                    unit="events",
                ).model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
        map_spec = _map_spec_from_count(item)
        if map_spec:
            specs.append(map_spec)
        series_spec = _series_spec_from_count(item)
        if series_spec:
            specs.append(series_spec)
        return specs
    if tool == "data_query_records" and summary.get("result_mode") == "records":
        dataset = str(summary.get("dataset") or args.get("dataset"))
        year = _year_from_args(args, summary)
        returned = int(summary.get("returned") or 0)
        return [
            ComponentSpec(
                type="record_table",
                params=RecordTableViewParams(
                    dataset=dataset,
                    row_limit=min(max(returned, 1), 25),
                    year=year,
                    start_date=_date_str(args.get("start_date")),
                    end_date=_date_str(args.get("end_date")),
                    utility=args.get("utility"),
                    county=args.get("county"),
                ).model_dump(mode="json"),
                evidence_ids=evidence,
                artifact_refs=refs,
            )
        ]
    return []


def _cap_stats(stats: list[ComponentSpec]) -> list[ComponentSpec]:
    """Keep every count card; cap the other stat kinds at _MAX_STATS.

    A multi-entity count answer (PG&E and SCE in 2020 and 2023) has one count
    per entity and period. The website renders only ranking comparisons, so
    there is no utility-by-year view to fold them into, and a global cap
    silently dropped the fourth count.
    """
    kept: list[ComponentSpec] = []
    others = 0
    for spec in stats:
        if spec.params.get("kind") == "count":
            kept.append(spec)
        elif others < _MAX_STATS:
            kept.append(spec)
            others += 1
    return kept


def _cap_visuals(visuals: list[ComponentSpec]) -> list[ComponentSpec]:
    """At most two visual components; never overlay CPUC and US on one chart.

    v1: one TimeSeries component (one dataset). Two TimeSeries of
    incomparable datasets: keep the first only. A second same-family series
    is also dropped — there is one chart slot, not a multi-trace overlay.
    """
    filtered: list[ComponentSpec] = []
    seen_time_series_family: str | None = None
    seen_time_series = False
    for spec in visuals:
        if spec.type == "time_series":
            family = _series_family(spec.params.get("dataset"))
            if seen_time_series_family is None:
                seen_time_series_family = family
            elif family != seen_time_series_family:
                continue
            if seen_time_series:
                continue
            seen_time_series = True
        filtered.append(spec)
    if len(filtered) <= _MAX_VISUAL:
        return filtered
    types = [item.type for item in filtered]
    if "map" in types and "time_series" in types:
        return [
            next(item for item in filtered if item.type == "map"),
            next(item for item in filtered if item.type == "time_series"),
        ]
    return filtered[:_MAX_VISUAL]


def _drop_derived_series_if_explicit(visuals: list[ComponentSpec]) -> list[ComponentSpec]:
    """Prefer visualization_create series over a count-derived window chart."""
    series = [item for item in visuals if item.type == "time_series"]
    if len(series) <= 1:
        return visuals
    explicit = [item for item in series if item.artifact_refs]
    derived = [item for item in series if not item.artifact_refs]
    if not explicit or not derived:
        return visuals
    return [item for item in visuals if item not in derived]


def _drop_derived_maps_if_conflicting(visuals: list[ComponentSpec]) -> list[ComponentSpec]:
    """Keep at most one map. Prefer visualization_create; drop count-derived maps
    when more than one would overlay incomparable datasets."""
    maps = [item for item in visuals if item.type == "map"]
    if len(maps) <= 1:
        return visuals
    explicit = [item for item in maps if item.artifact_refs]
    derived = [item for item in maps if not item.artifact_refs]
    skip = derived if explicit or len(derived) > 1 else []
    if not skip:
        return visuals
    return [item for item in visuals if item not in skip]


def _series_family(dataset: str | None) -> str:
    if dataset in {"ignitions", "cpuc_ignitions"}:
        return "cpuc"
    if dataset in {"us_ignitions"}:
        return "us"
    return dataset or "other"


def _map_extent(dataset: str, utility: str | None, county: str | None) -> str:
    """National zoom lives here (existing US-ignitions CONUS rule), not in the map UI."""
    if dataset == "us_ignitions" and not utility and not county:
        return "conus"
    if county:
        return "auto_fit"
    if utility:
        return "territory"
    return "statewide"


def _count_window_is_full_year(args: dict[str, Any], summary: dict[str, Any]) -> bool:
    """Map chrome is year-granular; a month window must not paint a year of points."""
    filters = summary.get("filters") or {}
    start = _date_str(args.get("start_date")) or _date_str(filters.get("start_date"))
    end = _date_str(args.get("end_date")) or _date_str(filters.get("end_date"))
    if not start and not end:
        return True
    if not start or not end:
        return False
    return (
        start.endswith("-01-01")
        and end.endswith("-12-31")
        and start[:4] == end[:4]
    )


def _map_spec_from_count(item: ToolExecution) -> ComponentSpec | None:
    """Retarget the existing Leaflet layer from a year-scoped geographic count.

    No second agent tool: visualization :8002 already serves the same
    attribute-filtered points the count used.
    """
    args = item.arguments or {}
    summary = item.summary or {}
    dataset = str(summary.get("dataset") or args.get("dataset") or "")
    viz = _COUNT_MAP_DATASETS.get(dataset)
    if not viz:
        return None
    if not _count_window_is_full_year(args, summary):
        return None
    year = _year_from_args(args, summary)
    if year is None:
        return None
    utility = args.get("utility") or (summary.get("filters") or {}).get("utility")
    county = args.get("county") or (summary.get("filters") or {}).get("county")
    # Territory polygons (and the zoom-6 floor) read as western US on the
    # full-width count+map pane. Fit the filtered points instead.
    if viz == "us_ignitions" and not utility and not county:
        extent = "conus"
    elif county or utility:
        extent = "auto_fit"
    else:
        extent = "statewide"
    return ComponentSpec(
        type="map",
        params=MapViewParams(
            datasets=[viz],
            year=year,
            utility=utility,
            county=county,
            extent=extent,
            show_territory=bool(utility),
        ).model_dump(mode="json"),
        evidence_ids=[item.evidence_id],
        artifact_refs=[],
    )


def _series_spec_from_count(item: ToolExecution) -> ComponentSpec | None:
    """Asked series for a partial-year count, same filters, no extra agent tool.

    Frontend GET /time-series (already the artifact-less fallback). Weekly bins
    are year-granular, so short windows use daily and longer ones monthly.
    """
    args = item.arguments or {}
    summary = item.summary or {}
    dataset = str(summary.get("dataset") or args.get("dataset") or "")
    viz = _COUNT_MAP_DATASETS.get(dataset)
    if not viz:
        return None
    if _count_window_is_full_year(args, summary):
        return None
    start, end = _count_window_dates(args, summary)
    if not start or not end:
        return None
    interval = _series_interval_for_window(start, end)
    if interval is None:
        return None
    utility = args.get("utility") or (summary.get("filters") or {}).get("utility")
    county = args.get("county") or (summary.get("filters") or {}).get("county")
    year = _year_from_args(args, summary)
    return ComponentSpec(
        type="time_series",
        params=TimeSeriesViewParams(
            dataset=viz,
            interval=interval,
            year=year,
            start_date=start,
            end_date=end,
            utility=utility,
            county=county,
            incident_type_mode=args.get("incident_type_mode"),
        ).model_dump(mode="json"),
        evidence_ids=[item.evidence_id],
        artifact_refs=[],
    )


def _count_window_dates(
    args: dict[str, Any], summary: dict[str, Any]
) -> tuple[str | None, str | None]:
    filters = summary.get("filters") or {}
    start = _date_str(args.get("start_date")) or _date_str(filters.get("start_date"))
    end = _date_str(args.get("end_date")) or _date_str(filters.get("end_date"))
    return start, end


def _series_interval_for_window(start: str, end: str) -> str | None:
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError:
        return None
    days = (last - first).days + 1
    if days <= 0:
        return None
    if days <= 62:
        return "daily"
    return "monthly"


def _year_from_args(args: dict[str, Any], summary: dict[str, Any]) -> int | None:
    if args.get("year") is not None:
        return int(args["year"])
    filters = summary.get("filters") or {}
    if filters.get("year") is not None:
        return int(filters["year"])
    start = str(args.get("start_date") or filters.get("start_date") or "")
    if len(start) >= 4 and start[:4].isdigit():
        return int(start[:4])
    return None


def _date_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _str_list(value: Any) -> list[str] | None:
    if not value:
        return None
    return [str(item) for item in value]


def _scope_label(args: dict[str, Any], summary: dict[str, Any]) -> str:
    filters = summary.get("filters") or {}
    county = args.get("county") or filters.get("county")
    utility = args.get("utility") or filters.get("utility")
    if county and utility:
        return f"{utility}, {county} County"
    if county:
        return f"{county} County"
    if utility:
        return str(utility)
    return "statewide"


def _spatial_scope_label(args: dict[str, Any], summary: dict[str, Any]) -> str:
    region = summary.get("region") or {}
    if isinstance(region, dict):
        name = region.get("utility_name") or region.get("utility") or region.get("tier")
        if name:
            return str(name)
    utility = args.get("utility")
    if utility:
        return str(utility)
    tier = args.get("hftd_tier")
    if tier:
        return str(tier)
    return "region"


def _period_label(
    args: dict[str, Any],
    summary: dict[str, Any],
    *,
    year: int | None = None,
) -> str:
    start, end = _count_window_dates(args, summary)
    if start and end:
        if _count_window_is_full_year(args, summary):
            return start[:4]
        if _is_calendar_month(start, end):
            first = date.fromisoformat(start)
            return f"{_MONTH_NAMES[first.month]} {first.year}"
        return f"{start} → {end}"
    if year is not None:
        return str(year)
    date_value = _date_str(args.get("date") or summary.get("date"))
    return date_value or ""


def _is_calendar_month(start: str, end: str) -> bool:
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except ValueError:
        return False
    if first.day != 1 or first.year != last.year or first.month != last.month:
        return False
    if first.month == 12:
        month_end = date(first.year, 12, 31)
    else:
        month_end = date(first.year, first.month + 1, 1) - timedelta(days=1)
    return last == month_end
