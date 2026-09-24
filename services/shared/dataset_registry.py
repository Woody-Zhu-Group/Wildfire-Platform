"""Single catalog of warehouse datasets used by the backend services.

Values are copied from the live service modules (visualization styles, data_query
aggregates/rank, agent Dataset/routes/views, comparison reasons). New facts are
not invented here. Disagreements between sources are commented, not averaged.

Caveat *text* stays in ``services.agent.caveats``; this module stores caveat ids
and lazy-imports the strings so the catalog is not duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
REASON_EPSS_PGE_ONLY = "EPSS is PG&E-only in this warehouse"
REASON_NO_COUNTY = "No county attribute/polygon for this metric in the warehouse"
REASON_NO_COUNTY_AREA = "No county polygon layer; per_km2 unavailable for county regions"
REASON_CIRCUITS_PGE = (
    "per_circuit uses the PGE EPSS circuits inventory; not meaningful for this scope"
)
REASON_ZERO_IGNITIONS = "Ignition count is zero; ratio undefined"
REASON_COMPONENT_NULL = "One or more component metrics are null"

# Discrepancy: services.agent.caveats epss_pge_only text is longer and is not
# this REASON_EPSS_PGE_ONLY string. Do not collapse them.


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
    "Not utility-attributed — do not compare counts to California CPUC ignitions. "
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
        extra={"empty_reason_non_pge": REASON_EPSS_PGE_ONLY},
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
