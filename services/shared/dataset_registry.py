"""Single catalog of warehouse datasets used by the backend services.

Values are copied from the live service modules (visualization styles, data_query
aggregates/rank, agent Dataset/routes/views, comparison reasons). New facts are
not invented here. Disagreements between sources are commented, not averaged.

Caveat *text* stays in ``services.agent.caveats``; this module stores caveat ids
and lazy-imports the strings so the catalog is not duplicated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping

# Naming conventions (utilities, counties, tiers, causes, incident types, and
# question wording) are defined in services.shared.naming and exported here.
# Import them from this module.
from services.shared.naming import *  # noqa: F401,F403


# ---------------------------------------------------------------------------
# Cross-cutting constants (verbatim from the modules named in comments)
# ---------------------------------------------------------------------------

# data_query/queries.py grouped-counts / regional-series
NOT_RECORDED = "Not recorded"
# data_query/queries.py /rank: a different missing label than grouped-counts.
MISSING_LABEL_RANK = "(unknown)"
# data_query/queries.py: the grouped-counts allow-list is global, not per-column
GROUP_BY_FIELDS = frozenset({"cause", "utility", "county"})

# visualization/styles.py (not in DATASET_STYLES)
IOU_STYLE = {
    "color": "#334155",
    "weight": 1.25,
    "opacity": 0.85,
    "fillOpacity": 0.05,
    "fillColor": "#64748b",
    "geometry_type": "MultiPolygon",
}
STATEWIDE_CENTER = [37.6, -120.8]

# Years with a daily HDW playback file under docs/assets/data/weather_anim.
HDW_YEARS = frozenset(range(2020, 2026))

# comparison/metrics.py
REASON_NO_COUNTY = "No county attribute/polygon for this metric in the warehouse"
REASON_NO_COUNTY_AREA = "No county polygon layer; per_km2 unavailable for county regions"
REASON_ZERO_IGNITIONS = "Ignition count is zero; ratio undefined"
REASON_COMPONENT_NULL = "One or more component metrics are null"
REASON_US_NO_UTILITY = "The US ignitions sample has no utility column"



def _us_ignitions_meta(*, notes: str) -> dict[str, Any]:
    """Shared US ignitions envelope fields.

    Discrepancy: ``notes`` differs between data_query/app.py and
    visualization/app.py (see US_IGNITIONS_NOTES_*). Callers must pick the
    service-specific notes string; other keys were identical.
    """
    return {
        "source": "firecastrl_irwin_sample",
        "utility_attributed": False,
        "census": False,
        "coverage": "CONUS",
        "not_comparable_to": "cpuc_ignitions",
        "sample_geography": {
            "method": "point-in-polygon vs Census-derived state boundaries",
            "california_share_overall": 0.4015,
            "california_share_2024": 0.5872,
            "west_region_share_overall": 0.7343,
            "west_region_share_2024": 0.7828,
            "note": (
                "Sample is California-heavy (≈40% of all rows; ≈59% of 2024). "
                "A national map view overstates geographic balance."
            ),
        },
        "notes": notes,
    }


US_IGNITIONS_NOTES_DATA_QUERY = (
    "All-cause IRWIN-derived ignitions from FireCastRL Kaggle dataset. "
    "Classification sample (event windows), not a complete census. "
    "Not utility-attributed; do not compare counts to California CPUC ignitions. "
    "Geographically skewed: California ≈40% overall / ≈59% of 2024 "
    "(Census region West ≈73% / ≈78%)."
)
US_IGNITIONS_NOTES_VISUALIZATION = (
    "All-cause IRWIN-derived ignitions (FireCastRL sample). "
    "Not comparable to California CPUC utility-caused ignitions. "
    "Geographically skewed: California ≈40% overall / ≈59% of 2024 "
    "(Census region West ≈73% / ≈78%)."
)
US_IGNITIONS_META_DATA_QUERY = _us_ignitions_meta(notes=US_IGNITIONS_NOTES_DATA_QUERY)
US_IGNITIONS_META_VISUALIZATION = _us_ignitions_meta(
    notes=US_IGNITIONS_NOTES_VISUALIZATION
)


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    table: str | None
    aliases: tuple[str, ...]
    viz_key: str | None
    agent_key: str | None
    style: dict[str, Any] | None
    allowed_group_by: tuple[str, ...]
    allowed_summary_metrics: tuple[str, ...]
    allowed_filters: tuple[str, ...]
    rank_pairs: tuple[tuple[str, str], ...]
    routes: Mapping[str, str]
    caveat_ids: tuple[str, ...]
    stat_label: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Which utilities and dates the dataset covers is measured by the loaders
    # (shared/dataset_coverage.json, read by dataset_coverage below), never
    # declared here. These two fields are wording only.
    # Why a dataset with no utility dimension cannot answer for a utility.
    no_utility_reason: str | None = None
    # Datasets to offer when a read is not covered, in order. Each is offered
    # only where its measured coverage includes the utilities and the period.
    not_covered_alternatives: tuple[str, ...] = ()


def _spec(**kwargs: Any) -> DatasetSpec:
    return DatasetSpec(**kwargs)


# Styles copied from visualization/styles.py DATASET_STYLES (viz_key keyed there).
_STYLE_IGNITIONS = {
    "color": "#c0440e",
    "opacity": 0.9,
    "fillOpacity": 0.6,
    "weight": 1,
    "geometry_type": "Point",
    "label": "CPUC Ignition Events",
    "chart_short_label": "CPUC Ignitions",
}
_STYLE_EPSS = {
    "color": "#7c3aed",
    "highlight_color": "#5b21b6",
    "opacity": 0.9,
    "weight": 2.5,
    "geometry_type": "MultiLineString",
    "label": "EPSS Outage Events",
    "chart_short_label": "EPSS",
    "render_as": "circuit_lines",
}
_STYLE_CALFIRE = {
    "color": "#b91c1c",
    "opacity": 0.85,
    "fillOpacity": 0.55,
    "weight": 1,
    "geometry_type": "Point",
    "label": "CAL FIRE Incidents",
    "chart_short_label": "CAL FIRE",
    "bubble_by_acres": True,
}
_STYLE_US = {
    "color": "#dc2626",
    "opacity": 0.9,
    "fillOpacity": 0.65,
    "weight": 1,
    "geometry_type": "Point",
    "label": "US Ignitions (IRWIN / all-cause)",
    "chart_short_label": "US Ignitions",
}
_STYLE_PSPS = {
    "color": "#1d6fa5",
    "highlight_color": "#155a85",
    "opacity": 0.9,
    "fillColor": "#1d6fa5",
    "fillOpacity": 0.25,
    "weight": 1.5,
    "geometry_type": "MultiPolygon",
    "label": "PSPS Event Areas",
}
_STYLE_HFTD = {
    "color": "#d97706",
    "geometry_type": "MultiPolygon",
    "label": "CPUC HFTD",
    "tiers": {
        "Tier 2": {
            "color": "#d97706",
            "weight": 0.75,
            "opacity": 0.45,
            "fillColor": "#d97706",
            "fillOpacity": 0.16,
        },
        "Tier 3": {
            "color": "#d97706",
            "weight": 0.9,
            "opacity": 0.55,
            "fillColor": "#d97706",
            "fillOpacity": 0.38,
        },
    },
}

# Discrepancy: agent/views.py _STAT_LABELS uses "CPUC ignitions" etc.; visualization
# styles use "CPUC Ignition Events". Both are stored (style.label vs stat_label).

DATASETS: dict[str, DatasetSpec] = {
    "cpuc_ignitions": _spec(
        key="cpuc_ignitions",
        table="wildfire.cpuc_ignitions",
        # viz _parse_dataset: ignition, cpuc → ignitions
        # agent argument_normalize: ignitions, cpuc_ignitions
        aliases=(
            "cpuc_ignitions",
            "ignitions",
            "ignition",
            "cpuc",
        ),
        viz_key="ignitions",
        agent_key="cpuc_ignitions",
        style=_STYLE_IGNITIONS,
        allowed_group_by=("cause", "utility", "county"),
        allowed_summary_metrics=("events", "counties", "utilities"),
        allowed_filters=(
            "utility",
            "include_untagged",
            "county",
            "year",
            "start_date",
            "end_date",
            "bbox",
        ),
        rank_pairs=(("county", "count"), ("utility", "count")),
        routes={
            "data_query": "/ignitions",
            "visualization": "/map-layer",
        },
        caveat_ids=("cpuc_utility_caused",),
        stat_label="CPUC ignitions",
        not_covered_alternatives=("calfire_incidents",),
    ),
    "calfire_incidents": _spec(
        key="calfire_incidents",
        table="wildfire.calfire_incidents",
        # viz: cal_fire, calfire_incidents → calfire
        # agent: cal fire, wildfire_incidents → calfire / calfire_incidents
        # Discrepancy: viz has cal_fire; agent has wildfire_incidents and
        # "cal fire" (space). All listed so neither map is dropped.
        aliases=(
            "calfire_incidents",
            "calfire",
            "cal_fire",
            "cal fire",
            "wildfire_incidents",
        ),
        viz_key="calfire",
        agent_key="calfire_incidents",
        style=_STYLE_CALFIRE,
        allowed_group_by=("cause", "utility", "county"),
        allowed_summary_metrics=("events", "acres", "counties"),
        allowed_filters=(
            "utility",
            "include_untagged",
            "county",
            "year",
            "start_date",
            "end_date",
            "min_acres",
            "incident_type",
        ),
        rank_pairs=(("county", "count"), ("county", "acres_burned")),
        routes={
            "data_query": "/calfire/incidents",
            "visualization": "/map-layer",
        },
        # calfire_missingness is formatted at collect time from warehouse
        # counts; it is not in CAVEAT_TEXT and is omitted from the static JSON.
        caveat_ids=("calfire_missingness", "calfire_map_feed_counts"),
        stat_label="CAL FIRE incidents",
        not_covered_alternatives=("cpuc_ignitions",),
    ),
    "epss_outages": _spec(
        key="epss_outages",
        table="wildfire.epss_outages",
        aliases=("epss_outages", "epss"),
        viz_key="epss",
        agent_key="epss_outages",
        style=_STYLE_EPSS,
        allowed_group_by=("cause", "utility", "county"),
        allowed_summary_metrics=("events", "circuits", "counties"),
        allowed_filters=(
            "circuit_id",
            "utility",
            "county",
            "year",
            "start_date",
            "end_date",
            "outage_type",
            "cause",
            "bbox",
        ),
        rank_pairs=(("circuit", "count"),),
        routes={
            "data_query": "/epss/outages",
            "visualization": "/map-layer",
        },
        caveat_ids=("epss_pge_only",),
        stat_label="EPSS outages",
        not_covered_alternatives=("psps_events", "cpuc_ignitions"),
    ),
    "psps_events": _spec(
        key="psps_events",
        table="wildfire.psps_events",
        aliases=("psps_events", "psps"),
        viz_key="psps",
        agent_key="psps_events",
        style=_STYLE_PSPS,
        allowed_group_by=("cause", "utility", "county"),
        allowed_summary_metrics=("events", "customers", "utilities"),
        # data_query /psps/events has no county=. Agent DataQueryRecordsArgs
        # allows county on any dataset except us_ignitions; grouped-counts
        # rejects PSPS+county. Flagged, not "fixed" by hiding county.
        allowed_filters=("utility", "year", "start_date", "end_date"),
        rank_pairs=(),
        routes={
            "data_query": "/psps/events",
            "visualization": "/map-layer",
        },
        caveat_ids=(),
        stat_label="PSPS events",
        not_covered_alternatives=("epss_outages", "cpuc_ignitions", "calfire_incidents"),
    ),
    "us_ignitions": _spec(
        key="us_ignitions",
        table="wildfire.us_ignitions",
        # viz: national_ignitions, usignitions
        # agent: us ignition, us ignitions, national ignitions
        aliases=(
            "us_ignitions",
            "national_ignitions",
            "usignitions",
            "us ignition",
            "us ignitions",
            "national ignitions",
        ),
        viz_key="us_ignitions",
        agent_key="us_ignitions",
        style=_STYLE_US,
        allowed_group_by=("cause", "utility", "county"),
        allowed_summary_metrics=("events",),
        allowed_filters=("year", "start_date", "end_date", "bbox"),
        rank_pairs=(),
        routes={
            "data_query": "/us-ignitions",
            "visualization": "/map-layer",
        },
        caveat_ids=("us_ignitions_sample",),
        stat_label="US ignitions",
        extra={
            "meta_data_query": US_IGNITIONS_META_DATA_QUERY,
            "meta_visualization": US_IGNITIONS_META_VISUALIZATION,
        },
        no_utility_reason=REASON_US_NO_UTILITY,
        not_covered_alternatives=("cpuc_ignitions", "calfire_incidents"),
    ),
    "circuits": _spec(
        key="circuits",
        table="wildfire.circuits",
        aliases=("circuits",),
        viz_key="circuits",
        agent_key="circuits",
        # Missing from visualization DATASET_STYLES; _parse_dataset still allows it.
        style=None,
        allowed_group_by=(),
        allowed_summary_metrics=(),
        allowed_filters=("circuit_id", "division", "substation"),
        rank_pairs=(),
        routes={
            "data_query": "/circuits",
            "visualization": "/event-detail",
        },
        caveat_ids=(),
        stat_label="Circuits",
    ),
    "hftd_tiers": _spec(
        key="hftd_tiers",
        table="wildfire.hftd_tiers",
        # Discrepancy: agent Dataset enum value is "hftd", not "hftd_tiers".
        # visualization style/parse key is "hftd". Warehouse table is hftd_tiers.
        aliases=("hftd_tiers", "hftd"),
        viz_key="hftd",
        agent_key="hftd",
        style=_STYLE_HFTD,
        allowed_group_by=(),
        allowed_summary_metrics=(),
        allowed_filters=("tier",),
        rank_pairs=(),
        routes={
            "data_query": "/hftd",
            "visualization": "/map-layer",
        },
        caveat_ids=(),
        stat_label=None,
    ),
    "iou_territories": _spec(
        key="iou_territories",
        table="wildfire.iou_territories",
        aliases=("iou_territories",),
        viz_key=None,
        agent_key="iou_territories",
        # Missing from DATASET_STYLES; visualization uses module-level IOU_STYLE.
        style=dict(IOU_STYLE),
        allowed_group_by=(),
        allowed_summary_metrics=(),
        allowed_filters=("utility",),
        rank_pairs=(),
        routes={
            "data_query": "/iou-territories",
            "visualization": "/utility-territory",
        },
        caveat_ids=(),
        stat_label=None,
    ),
    "counties": _spec(
        key="counties",
        table="wildfire.counties",
        aliases=("counties",),
        viz_key=None,
        agent_key=None,
        style=None,
        allowed_group_by=(),
        allowed_summary_metrics=(),
        allowed_filters=(),
        rank_pairs=(),
        routes={},
        caveat_ids=(),
    ),
    "grid_cells": _spec(
        key="grid_cells",
        table="wildfire.grid_cells",
        aliases=("grid_cells",),
        viz_key=None,
        agent_key=None,
        style=None,
        allowed_group_by=(),
        allowed_summary_metrics=(),
        allowed_filters=(),
        rank_pairs=(),
        routes={},
        caveat_ids=(),
    ),
    "cpuc_ignitions_with_time": _spec(
        key="cpuc_ignitions_with_time",
        table="wildfire.cpuc_ignitions_with_time",
        aliases=("cpuc_ignitions_with_time",),
        viz_key=None,
        agent_key=None,
        style=None,
        allowed_group_by=(),
        allowed_summary_metrics=(),
        allowed_filters=(),
        rank_pairs=(),
        routes={},
        caveat_ids=(),
    ),
    "psps_event_circuits": _spec(
        key="psps_event_circuits",
        table="wildfire.psps_event_circuits",
        aliases=("psps_event_circuits",),
        viz_key=None,
        agent_key=None,
        style=None,
        allowed_group_by=(),
        allowed_summary_metrics=(),
        allowed_filters=(),
        rank_pairs=(),
        routes={"data_query": "/psps/events/{event_name}/circuits"},
        caveat_ids=(),
    ),
}

CANONICAL_KEYS = frozenset(DATASETS)

ALIASES: dict[str, str] = {}
for _entry in DATASETS.values():
    for _alias in _entry.aliases:
        if _alias in ALIASES and ALIASES[_alias] != _entry.key:
            raise RuntimeError(
                f"alias collision: {_alias!r} -> {ALIASES[_alias]} and {_entry.key}"
            )
        ALIASES[_alias] = _entry.key

# visualization/app.py _parse_dataset identity keys (DATASET_STYLES + circuits).
# Does not include warehouse keys except those listed as aliases mapping *to* viz keys.
_VIZ_PARSE_ALIASES = {
    "ignition": "ignitions",
    "cpuc": "ignitions",
    "epss_outages": "epss",
    "psps_events": "psps",
    "cal_fire": "calfire",
    "calfire_incidents": "calfire",
    "national_ignitions": "us_ignitions",
    "usignitions": "us_ignitions",
}

ALLOWED_RANK_PAIRS = frozenset(
    (entry.key, group_by, metric)
    for entry in DATASETS.values()
    for group_by, metric in entry.rank_pairs
)

# Measures a ranking or comparison can order by, keyed by comparison metric
# name (services/comparison/metrics.py METRICS); labels are MEASURE_LABELS.
# The dataset each measure reads.
MEASURE_DATASETS: dict[str, str] = {
    "ignition_count": "cpuc_ignitions",
    "calfire_incident_count": "calfire_incidents",
    "acres_burned": "calfire_incidents",
    "psps_event_count": "psps_events",
    "customers_deenergized": "psps_events",
    "epss_outage_count": "epss_outages",
    "epss_to_ignition_ratio": "epss_outages",
}
# Measures that exist for some utilities only (EPSS is PG&E only).
MEASURE_UTILITIES: dict[str, frozenset[str]] = {
    "epss_outage_count": frozenset({"PGE"}),
    "epss_to_ignition_ratio": frozenset({"PGE"}),
}
# A data_query_rank (dataset, metric) pair as its measure.
_RANK_PAIR_MEASURES = {
    ("cpuc_ignitions", "count"): "ignition_count",
    ("calfire_incidents", "count"): "calfire_incident_count",
    ("calfire_incidents", "acres_burned"): "acres_burned",
    ("epss_outages", "count"): "epss_outage_count",
}
# data_query_rank: the measures each grouping can be ranked by.
RANK_MEASURES: dict[str, tuple[str, ...]] = {}
for _dataset, _group_by, _metric in sorted(ALLOWED_RANK_PAIRS):
    RANK_MEASURES.setdefault(_group_by, ())
    RANK_MEASURES[_group_by] += (_RANK_PAIR_MEASURES[(_dataset, _metric)],)
for _group_by, _measures in RANK_MEASURES.items():
    RANK_MEASURES[_group_by] = tuple(sorted(_measures, key=list(MEASURE_DATASETS).index))
# comparison_run: the measures each scope returns a value for
# (services/comparison/queries.py). EPSS is PG&E only; PSPS has no county.
COMPARE_MEASURES: dict[str, tuple[str, ...]] = {
    "utility": (
        "ignition_count",
        "calfire_incident_count",
        "acres_burned",
        "psps_event_count",
        "customers_deenergized",
        "epss_outage_count",
    ),
    "county": (
        "ignition_count",
        "calfire_incident_count",
        "acres_burned",
        "epss_outage_count",
    ),
}

# Datasets a yearly or seasonal chart (the router's series_mode yearly and
# seasonal) can read when the question does not fix one. Cumulative acres,
# customer events, and regional series fix their own dataset.
SERIES_DATASETS: tuple[str, ...] = ("cpuc_ignitions", "calfire_incidents", "epss_outages")

SUMMARY_METRIC_IDS = {
    key: entry.allowed_summary_metrics
    for key, entry in DATASETS.items()
    if entry.allowed_summary_metrics
}

GROUPED_DATASETS = frozenset(SUMMARY_METRIC_IDS)

# agent/views.py _STAT_LABELS (includes both warehouse and viz keys)
STAT_LABELS = {
    key: entry.stat_label
    for key, entry in DATASETS.items()
    if entry.stat_label
}
STAT_LABELS.update(
    {
        entry.viz_key: entry.stat_label
        for entry in DATASETS.values()
        if entry.viz_key and entry.stat_label
    }
)

# agent/views.py _DQ_TO_VIZ (includes hftd; count-map subset omits hftd)
DQ_TO_VIZ = {
    entry.agent_key: entry.viz_key
    for entry in DATASETS.values()
    if entry.agent_key and entry.viz_key
}

COUNT_MAP_DATASETS = {
    key: viz
    for key, viz in DQ_TO_VIZ.items()
    if key
    in {
        "cpuc_ignitions",
        "us_ignitions",
        "epss_outages",
        "psps_events",
        "calfire_incidents",
    }
}

DATASET_STYLES = {
    entry.viz_key: dict(entry.style)
    for entry in DATASETS.values()
    if entry.viz_key and entry.style and entry.viz_key != "circuits"
    # circuits: viz parse allows it but DATASET_STYLES historically omitted it.
}

# iou is not in DATASET_STYLES; keep that gap. Style lives on the spec + IOU_STYLE.

AGENT_DATASET_VALUES = tuple(
    entry.agent_key for entry in DATASETS.values() if entry.agent_key
)

# Agent keys that name a map layer, to the layer's viz key. DQ_TO_VIZ without
# circuits: circuits have a viz key for /event-detail but no map layer.
LAYER_VIZ_KEYS = {key: viz for key, viz in DQ_TO_VIZ.items() if key != "circuits"}

# Agent harness model-argument repairs (services/agent/argument_normalize.py).
# Discrepancy: these predate ALIASES and differ from it (no "ignition",
# "cpuc", "cal_fire", "usignitions"; the viz map also lacks "national
# ignitions", which the records map has). Kept as is: widening them would
# change which model arguments the harness repairs.
ARGUMENT_VIZ_DATASET_ALIASES: dict[str, str] = {
    "cpuc_ignitions": "ignitions",
    "ignitions": "ignitions",
    "us_ignitions": "us_ignitions",
    "us ignition": "us_ignitions",
    "us ignitions": "us_ignitions",
    "epss_outages": "epss",
    "epss": "epss",
    "psps_events": "psps",
    "psps": "psps",
    "calfire_incidents": "calfire",
    "calfire": "calfire",
    "cal fire": "calfire",
    "wildfire_incidents": "calfire",
    "hftd": "hftd",
}
ARGUMENT_RECORDS_DATASET_ALIASES: dict[str, str] = {
    "cpuc_ignitions": "cpuc_ignitions",
    "ignitions": "cpuc_ignitions",
    "us_ignitions": "us_ignitions",
    "us ignition": "us_ignitions",
    "us ignitions": "us_ignitions",
    "national ignitions": "us_ignitions",
    "epss_outages": "epss_outages",
    "epss": "epss_outages",
    "psps_events": "psps_events",
    "psps": "psps_events",
    "calfire_incidents": "calfire_incidents",
    "calfire": "calfire_incidents",
    "cal fire": "calfire_incidents",
    "wildfire_incidents": "calfire_incidents",
    "circuits": "circuits",
    "hftd": "hftd",
    "iou_territories": "iou_territories",
}

# Dataset nouns in clarification questions (services/agent/clarify_missing.py).
# Discrepancy with stat_label: "US ignition sample events", lowercase
# "circuits", and "HFTD areas" where HFTD has no stat_label. Kept as written.
CLARIFY_DATASET_LABELS: dict[str, str] = {
    "cpuc_ignitions": "CPUC ignitions",
    "calfire_incidents": "CAL FIRE incidents",
    "epss_outages": "EPSS outages",
    "epss": "EPSS outages",
    "psps_events": "PSPS events",
    "us_ignitions": "US ignition sample events",
    "circuits": "circuits",
    "hftd": "HFTD areas",
}


def to_canonical(name: str) -> str:
    raw = name.strip().lower().replace("-", "_")
    # Keep spaced aliases ("cal fire") before underscore folding.
    spaced = name.strip().lower()
    if spaced in ALIASES:
        return ALIASES[spaced]
    if raw in ALIASES:
        return ALIASES[raw]
    raise KeyError(name)


def to_viz_key(name: str) -> str:
    entry = DATASETS[to_canonical(name)]
    if not entry.viz_key:
        raise KeyError(name)
    return entry.viz_key


def parse_viz_dataset(value: str) -> str:
    """Return visualization short keys (ignitions, epss, …), matching _parse_dataset."""
    ds = value.strip().lower().replace("-", "_")
    ds = _VIZ_PARSE_ALIASES.get(ds, ds)
    allowed = set(DATASET_STYLES) | {"circuits"}
    if ds not in allowed:
        # Every registry alias ("cal fire", "wildfire_incidents") as well.
        try:
            ds = to_viz_key(value)
        except KeyError:
            pass
    if ds not in allowed:
        raise ValueError(
            f"unknown dataset {value!r}; allowed: {', '.join(sorted(allowed))}"
        )
    return ds


def data_query_path(dataset: str) -> str:
    entry = DATASETS[to_canonical(dataset)]
    path = entry.routes.get("data_query")
    if not path:
        raise KeyError(dataset)
    return path


def style_for(dataset: str, *, tier: str | None = None) -> dict[str, Any]:
    """visualization/styles.py style_for, keyed by viz short name."""
    if dataset not in DATASET_STYLES:
        raise KeyError(dataset)
    base = dict(DATASET_STYLES[dataset])
    if dataset == "hftd" and tier:
        tier_style = base.get("tiers", {}).get(tier)
        if tier_style:
            out = {k: v for k, v in base.items() if k != "tiers"}
            out.update(tier_style)
            out["tier"] = tier
            return out
    return {k: v for k, v in base.items() if k != "tiers"}


def caveat_texts(dataset: str) -> list[str]:
    """Resolve static caveat strings from services.agent.caveats (no copies here)."""
    from services.agent.caveats import CAVEAT_TEXT

    entry = DATASETS[to_canonical(dataset)]
    return [CAVEAT_TEXT[cid] for cid in entry.caveat_ids if cid in CAVEAT_TEXT]


def __getattr__(name: str) -> Any:
    """COMPARISON_METRIC_DATASETS: comparison metric -> the dataset it reads.

    The comparison service owns its metrics, so the map is its own
    (``services.comparison.metrics.METRIC_DATASETS``), read on first use because
    that module imports this one.
    """
    if name == "COMPARISON_METRIC_DATASETS":
        from services.comparison.metrics import METRIC_DATASETS

        return METRIC_DATASETS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")




# ---------------------------------------------------------------------------
# Measured coverage (label rules I and J, and every count by utility or period)
# ---------------------------------------------------------------------------

# Written by db/loaders/coverage.py from the loaded warehouse; the website
# imports the same file. Coverage is measured, never declared: a dataset
# covers a utility only if it has rows for it, from that utility's first row
# date to the dataset's last row date. The window closes on the dataset's last
# date, not the utility's: in a sparse series (five SDG&E PSPS events) a
# utility's last event is not the end of the source's reporting.
COVERAGE_PATH = Path(__file__).resolve().parents[2] / "shared" / "dataset_coverage.json"


def _load_coverage() -> dict[str, Any]:
    try:
        payload = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{COVERAGE_PATH} is missing. Load the warehouse (python -m db.loaders) "
            "or regenerate it with: python -m db.loaders.coverage"
        ) from exc
    return payload["datasets"]


# dataset key -> {"first", "last", "rows", "years", "utility_dimension",
# "utilities": {code: {"first", "last", "rows", "years"}}, "untagged"}, where
# "years" maps a calendar year to its row count (a year with no rows is
# absent). Tests may swap entries (monkeypatch).
DATASET_COVERAGE: dict[str, dict[str, Any]] = _load_coverage()


def _day(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _measured(dataset: str) -> dict[str, Any] | None:
    try:
        key = to_canonical(dataset)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"coverage: unknown dataset {dataset!r}") from exc
    return DATASET_COVERAGE.get(key)


def coverage_window(dataset: str, utility: str | None = None) -> tuple[date, date] | None:
    """The measured dates a count can fall in, or None when there are none.

    With a utility: from its first row to the dataset's last row, or None when
    the dataset has no rows for it. Without one: the dataset's own span. A
    dataset with rows but no date column (circuits) has no window.
    """
    entry = _measured(dataset)
    if entry is None:
        return None
    last = _day(entry.get("last"))
    if utility is None:
        first = _day(entry.get("first"))
    elif utility == UNTAGGED_UTILITY:
        first = _day((entry.get("untagged") or {}).get("first"))
    else:
        span = (entry.get("utilities") or {}).get(utility)
        first = _day(span.get("first")) if span else None
    return (first, last) if first and last else None


def _utility_span(entry: dict[str, Any], utility: str | None) -> dict[str, Any] | None:
    """The measured span (first, last, rows, years) for one utility, or the dataset's."""
    if utility is None:
        return entry
    if utility == UNTAGGED_UTILITY:
        return entry.get("untagged")
    return (entry.get("utilities") or {}).get(utility)


def dataset_years(dataset: str, utility: str | None = None) -> list[int]:
    """Calendar years in which the dataset (or one utility in it) has rows, as measured."""
    entry = _measured(dataset)
    span = _utility_span(entry, utility) if entry else None
    return sorted(int(year) for year, rows in ((span or {}).get("years") or {}).items() if rows)


def warehouse_year_range() -> tuple[int, int]:
    """The first and last calendar year in which any dataset has rows, as measured.

    Time resolution refuses a year outside this range for every dataset; a year
    inside it is checked against the asked dataset's own coverage.
    """
    years = [
        int(year)
        for entry in DATASET_COVERAGE.values()
        for year, rows in (entry.get("years") or {}).items()
        if rows
    ]
    return min(years), max(years)


def rows_in_period(dataset: str, utility: str | None, start: Any, end: Any) -> bool:
    """True only when measured rows are known to exist for the utility in the period.

    Rows exist when the period holds a whole calendar year with rows, or holds
    the utility's first or last row date. A period that only touches part of a
    year with rows is not known to hold any, so this returns False: an offer
    must never lead to a count that may be zero.
    """
    entry = _measured(dataset)
    span = _utility_span(entry, utility) if entry else None
    if not span or not span.get("rows"):
        return False
    start, end = _day(start), _day(end)
    if start is None and end is None:
        return True
    years = {int(year): rows for year, rows in (span.get("years") or {}).items() if rows}
    if not years:
        return False
    low = start or date(min(years), 1, 1)
    high = end or date(max(years), 12, 31)
    known = [_day(span.get("first")), _day(span.get("last"))]
    if any(day is not None and low <= day <= high for day in known):
        return True
    return any(
        years.get(year) and low <= date(year, 1, 1) and high >= date(year, 12, 31)
        for year in range(low.year, high.year + 1)
    )


def _empty_years_reason(
    dataset: str, window: tuple[date, date], start: date | None, end: date | None
) -> str | None:
    """Why a period inside the window is still not covered: the dataset has no
    rows at all in any year the period touches (CAL FIRE between its one 2009
    row and 2013).

    A utility with no rows in a year the dataset reported is a real zero; a
    year in which the dataset itself has no rows is a gap in the source.
    """
    years = dataset_years(dataset)
    if not years:
        return None
    first = max(start or window[0], window[0]).year
    last = min(end or window[1], window[1]).year
    if any(first <= year <= last for year in years):
        return None
    label = STAT_LABELS.get(to_canonical(dataset), dataset)
    before = max((year for year in years if year < first), default=None)
    after = min((year for year in years if year > last), default=None)
    if before is not None and after is not None:
        return f"{label} have no rows between {before} and {after}"
    return f"{label} have no rows from {first} to {last}"


def covered_utilities(dataset: str) -> list[str]:
    """Utilities the dataset has rows for, as measured."""
    entry = _measured(dataset)
    return list((entry or {}).get("utilities") or {})


def single_utility_dataset(dataset: str | None) -> bool:
    """True when the dataset has rows for exactly one utility (EPSS: PG&E).

    Such a dataset has no utility dimension to rank or group by.
    """
    if not dataset:
        return False
    entry = _measured(dataset)
    return bool(
        entry and entry.get("utility_dimension") and len(entry.get("utilities") or {}) == 1
    )


def _utility_name(code: str) -> str:
    if code == UNTAGGED_UTILITY:
        return "untagged"
    return UTILITY_CLARIFY_LABELS.get(code) or UTILITY_DISPLAY_LABELS.get(code) or code


def _series(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _utility_names(codes: list[str]) -> str:
    return _series([_utility_name(code) for code in codes])


def _possessive(names: str) -> str:
    return f"{names}'" if names.endswith("s") else f"{names}'s"


def period_phrase(start: Any, end: Any) -> str:
    """' in 2019' for a calendar year, ' from A to B' otherwise, '' for no period."""
    start, end = _day(start), _day(end)
    if start is None and end is None:
        return ""
    if (
        start
        and end
        and start.year == end.year
        and (start.month, start.day, end.month, end.day) == (1, 1, 12, 31)
    ):
        return f" in {start.year}"
    if start and end:
        return f" from {start.isoformat()} to {end.isoformat()}"
    return f" from {start.isoformat()} on" if start else f" through {end.isoformat()}"


def coverage_summary(dataset: str) -> str:
    """Which utilities the dataset has rows for, as measured, in one clause."""
    key = to_canonical(dataset)
    label = STAT_LABELS.get(key, key)
    return f"{label} have rows only for {_utility_names(covered_utilities(key))}"


def _overlaps(window: tuple[date, date], start: date | None, end: date | None) -> bool:
    first, last = window
    return (end is None or end >= first) and (start is None or start <= last)


def _uncovered_reason(
    dataset: str, utility: str | None, start: date | None, end: date | None
) -> str | None:
    """Why the dataset has no rows for this utility and period, or None if it does."""
    entry = _measured(dataset)
    if entry is None:
        return None
    spec = DATASETS[to_canonical(dataset)]
    label = STAT_LABELS.get(spec.key, spec.key)
    if utility is not None and not entry.get("utility_dimension"):
        return spec.no_utility_reason or f"{label} have no utility column"
    if utility == UNTAGGED_UTILITY and not entry.get("untagged"):
        return f"{label} have no rows without a utility"
    if utility not in (None, UNTAGGED_UTILITY) and utility not in (entry.get("utilities") or {}):
        return coverage_summary(spec.key)
    window = coverage_window(spec.key, utility)
    if window is None:
        return None
    if _overlaps(window, start, end):
        return _empty_years_reason(spec.key, window, start, end)
    who = f" for {_utility_name(utility)}" if utility else ""
    if end is not None and end < window[0]:
        return f"{label}{who} start on {window[0].isoformat()}"
    return f"{label} end on {window[1].isoformat()}"


def partial_coverage_note(
    dataset: str, utility: str | None, start: Any, end: Any
) -> str | None:
    """A note when a covered period runs past the measured window on either side.

    The count is real for the covered part only; it must not be read as the
    whole period's.
    """
    start, end = _day(start), _day(end)
    window = coverage_window(dataset, utility)
    if window is None or start is None or end is None or not _overlaps(window, start, end):
        return None
    first, last = window
    if start >= first and end <= last:
        return None
    label = STAT_LABELS.get(to_canonical(dataset), dataset)
    who = f" for {_utility_name(utility)}" if utility else ""
    return (
        f"{label}{who} cover {first.isoformat()} to {last.isoformat()}, so the count"
        f"{period_phrase(start, end)} covers only {max(start, first).isoformat()} to "
        f"{min(end, last).isoformat()}."
    )


Period = tuple[Any, Any]


def _has_rows_all(
    dataset: str, utilities: list[str], periods: list[tuple[date | None, date | None]]
) -> bool:
    """True when the dataset has measured rows for every utility in every period.

    What an offer is held to: a window that merely overlaps the period is not
    enough (SDG&E's PSPS window spans 2022, but it has no 2022 rows).
    """
    return all(
        _uncovered_reason(dataset, utility, start, end) is None
        and rows_in_period(dataset, utility, start, end)
        for utility in (utilities or [None])
        for start, end in periods
    )


def dataset_coverage_gap(
    dataset: str | None,
    utilities: list[str],
    start: Any = None,
    end: Any = None,
    *,
    periods: list[Period] | None = None,
) -> dict[str, Any] | None:
    """The coverage gap a read would hit, or None when it is covered.

    A read is covered when the dataset has rows for at least one named utility
    in at least one period (``periods``, or the one period ``start`` to
    ``end``); the service returns an uncovered side as null with its reason, so
    only a read with nothing covered is refused. A read with no utility is
    checked against the dataset's own dates. What the gap offers instead is
    held to more: an alternative dataset, or another utility, is offered only
    where the dataset has measured rows for every named utility in every
    period (``rows_in_period``), never where its window merely overlaps. An
    unknown dataset raises: a coverage check that cannot find its dataset must
    not pass.
    """
    if not dataset:
        return None
    windows_asked = [(_day(a), _day(b)) for a, b in (periods or [(start, end)])]
    entry = _measured(dataset)
    no_period = all(a is None and b is None for a, b in windows_asked)
    if entry is None or (not utilities and no_period):
        return None
    spec = DATASETS[to_canonical(dataset)]
    named = list(utilities)
    reasons = [
        _uncovered_reason(spec.key, utility, a, b)
        for utility in named or [None]
        for a, b in windows_asked
    ]
    if any(reason is None for reason in reasons):
        return None
    others = [
        utility
        for utility in covered_utilities(spec.key)
        if utility not in named and _has_rows_all(spec.key, [utility], windows_asked)
    ]
    # A named utility with rows in the dataset at other dates: its own window.
    own = {utility: coverage_window(spec.key, utility) for utility in named}
    return {
        "dataset": spec.key,
        "utilities": named,
        "periods": [
            [a.isoformat() if a else None, b.isoformat() if b else None]
            for a, b in windows_asked
        ],
        "reason": "; ".join(dict.fromkeys(str(reason) for reason in reasons)),
        "covered_utilities": others,
        "utility_windows": {
            utility: [window[0].isoformat(), window[1].isoformat()]
            for utility, window in own.items()
            if window is not None
        },
        "alternatives": [
            other
            for other in spec.not_covered_alternatives
            if _has_rows_all(other, named, windows_asked)
        ],
    }


def _gap_parts(gap: dict[str, Any]) -> tuple[str, str, str, str]:
    label = STAT_LABELS.get(gap["dataset"], gap["dataset"])
    named = _utility_names(list(gap.get("utilities") or []))
    phrases = [period_phrase(a, b) for a, b in gap.get("periods") or []]
    phrases = [phrase for phrase in dict.fromkeys(phrases) if phrase]
    # " in 2020" and " in 2021" read as " in 2020 and 2021"; three or more
    # years as " in 2018, 2019, and 2020".
    if len(phrases) > 1 and all(phrase.startswith(" in ") for phrase in phrases):
        period = " in " + _series([phrase[4:] for phrase in phrases])
    else:
        period = " and".join(phrases)
    head = (
        f"{gap.get('reason') or label + ' do not cover this read'}, so there are "
        f"{'no ' + named + ' rows' if named else 'no rows'} in {label}{period}"
    )
    return label, named, period, head


def records_sentence(labels: list[str], named: str, period: str) -> str:
    """'PSPS events and CPUC ignitions have records for SCE in 2020', no final stop."""
    if not labels:
        return ""
    who = f" for {named}" if named else ""
    return f"{_series(labels)} have records{who}{period}"


def alternatives_records(gap: dict[str, Any]) -> str:
    """The alternatives a gap offers, as the records they hold for its utilities and period."""
    _label, named, period, _head = _gap_parts(gap)
    labels = [STAT_LABELS.get(item, item) for item in gap.get("alternatives") or []]
    return records_sentence(labels, named, period)


def dropped_filters_sentence(gap: dict[str, Any]) -> str:
    """Says that an offer keeps the utilities and period but not the other filters.

    Coverage is measured per dataset, utility, and year, so an offer cannot
    promise rows inside a county, tier, or area; it drops those filters.
    """
    dropped = list(gap.get("unmeasured_filters") or [])
    if not dropped:
        return ""
    noun = "filter" if len(dropped) == 1 else "filters"
    return f" That offer drops the {_series(dropped)} {noun}."


def not_covered_message(gap: dict[str, Any], *, subject: str = "result") -> str:
    """The reason, that the result is absent rather than zero, and what has records."""
    head = _gap_parts(gap)[3]
    text = f"{head}: that {subject} would be absent, not zero."
    records = alternatives_records(gap)
    if records:
        text += f" {records}.{dropped_filters_sentence(gap)}"
    return text


def not_covered_question(gap: dict[str, Any], *, comparison: bool = False) -> str:
    """Clarification for an uncovered read: the reason, then only offers the data covers."""
    label, named, period, head = _gap_parts(gap)
    head += f": that {'comparison' if comparison else 'result'} would be absent, not zero."
    alternatives = " or ".join(
        STAT_LABELS.get(item, item) for item in gap.get("alternatives") or []
    )
    # Another utility's rows are offered only when the dataset covers exactly
    # one in the period (EPSS: PG&E); a list of several is not an answer to a
    # question about the named utility.
    others = list(gap.get("covered_utilities") or [])
    covered = _utility_names(others) if named and len(others) == 1 else ""
    # Each offer is first stated as the records the data holds, in the order
    # the question then offers them.
    facts = [alternatives_records(gap)]
    if covered:
        other = records_sentence([label], covered, period)
        facts = facts + [other] if comparison else [other] + facts
    whose = f"{_possessive(named)} " if named else ""
    offers: list[str] = []
    if comparison and alternatives:
        offers.append(f"compare {whose}{alternatives}{period} instead")
    if covered:
        offers.append(f"{_possessive(covered)} {label}{period}")
    if not comparison and alternatives:
        offers.append(f"{whose}{alternatives}{period} instead")
    # A named utility the dataset covers at other dates: offer those dates.
    for utility, (first, _last) in (gap.get("utility_windows") or {}).items():
        offers.append(f"{_possessive(_utility_name(utility))} {label} from {first} on")
    if not offers:
        return head
    stated = "".join(f" {fact}." for fact in facts if fact) + dropped_filters_sentence(gap)
    verb = "Do you want to " if offers[0].startswith("compare ") else "Do you want "
    return f"{head}{stated} {verb}{', or '.join(offers)}?"


# comparison/metrics.py: the per-circuit denominator exists only for the
# utilities the circuits inventory has rows for, as measured.
REASON_CIRCUITS_SCOPE = (
    f"per_circuit uses the {_utility_names(covered_utilities('circuits'))} EPSS circuits "
    "inventory; not meaningful for this scope"
)
