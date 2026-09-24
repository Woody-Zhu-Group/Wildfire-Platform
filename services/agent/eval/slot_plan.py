"""Plans built only from router slots. Jev is not called.

One invariant decides whether a plan stands: every slot and constraint the
router resolved (each utility, each county, the date window including a
sub-year window, the dataset, and list, records, map, or series wording) must
be represented in the planned calls, and each call must be able to apply it.
When anything would be dropped, the plan is refused and the deferral stands,
so the plan never answers a narrower question than the one asked.
"""

from __future__ import annotations

import itertools
import re
from datetime import date
from typing import Any

from services.agent.routing import NOT_COVERED_RULES, RouteDecision, route_question
from services.agent.time_resolve import months_from_text
from services.shared.dataset_registry import DATASETS, LAYER_VIZ_KEYS, utility_coverage_gap

MAX_ENTITY_CALLS = 10
_VIZ = LAYER_VIZ_KEYS
_RANK_DATASETS = {"cpuc_ignitions", "calfire_incidents"}

# Wording that picks which calls to build.
_PER_MONTH = re.compile(
    r"\b(?:each|every|per)\s+months?\b|\bby\s+months?\b|\bmonthly\b|"
    r"\bmonth by month\b|\bin each month\b",
    re.I,
)
_BY_COUNTY = re.compile(r"\b(?:by|per|each)\s+count(?:y|ies)\b", re.I)
_ALSO_TOTAL = re.compile(
    r"\byearly\s+total\b|\bplus\s+(?:the\s+)?(?:yearly\s+)?total\b|"
    r"\bcounts?\b.+\b(?:monthly|by month|each month)\b|"
    r"\b(?:monthly|by month|each month)\b.+\b(?:and|plus)\b.+\btotal\b",
    re.I,
)

# Wording that names an output form or a measure the calls must carry.
_MAP_ASK = re.compile(r"\bmaps?\b|\bmapped\b|\bwhere\b|\blocations?\s+of\b", re.I)
_LIST_ASK = re.compile(
    r"\blist(?:s|ed|ing)?\b|\brecords?\b|\bdetails?\b|\bwhich\s+(?:ones|ignitions|"
    r"events|outages|incidents|fires)\b|\bshow\s+(?:me\s+)?(?:the\s+)?(?:individual|each)\b",
    re.I,
)
_SERIES_ASK = re.compile(
    _PER_MONTH.pattern + r"|\btrends?\b|\bover\s+time\b|\btime\s+series\b|"
    r"\bchart\b|\bgraph\b|\bplot\b",
    re.I,
)
# Wording that names the US ignitions sample, whatever dataset the router chose.
_US_SAMPLE = re.compile(
    r"\bsampled?\b|\ball[\s-]+causes?\b|\ball-cause\b|\bnational\b|\bus\s+ignitions?\b|"
    r"\bu\.s\.\s+ignitions?\b|\bfirecast",
    re.I,
)
_US_STATE = re.compile(
    r"\b(?:alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|"
    r"kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
    r"mississippi|missouri|montana|nebraska|nevada|new\s+hampshire|new\s+jersey|"
    r"new\s+mexico|new\s+york|north\s+carolina|north\s+dakota|ohio|oklahoma|"
    r"oregon|pennsylvania|rhode\s+island|south\s+carolina|south\s+dakota|"
    r"tennessee|texas|utah|vermont|virginia|washington|west\s+virginia|"
    r"wisconsin|wyoming)\b",
    re.I,
)
_OTHER_METRIC = re.compile(
    r"\bacres?\b|\bacreage\b|\bcustomers?\b|\brate\b|\bratio\b|"
    r"\bper\s+(?:circuit|customer|mile|km|kilometer|square)\b|"
    r"\bstructures?\b|\bdestroyed\b|\bdamaged?\b|\bdeaths?\b|\bfatalit\w*|"
    r"\binjur\w*|\bcosts?\b|\blosses\b|\bloss\b",
    re.I,
)
# Sub-year windows the router does not resolve to dates.
_UNRESOLVED_WINDOW = re.compile(
    r"\b(?:q[1-4]|quarters?|week\s+of|first\s+half|second\s+half|"
    r"spring|summer|fall|autumn|winter)\b",
    re.I,
)
def _dataset_filters(dataset: str) -> set[str]:
    spec = DATASETS.get(dataset) or next(
        (item for item in DATASETS.values() if item.agent_key == dataset), None
    )
    return set(spec.allowed_filters) if spec else set()


def _months_named(lower: str) -> set[int]:
    """Calendar months the question names, by the shared date parser's rule."""
    return {number for number, _word in months_from_text(lower)}


def _resolved_window(slots: dict[str, Any]) -> tuple[str, str] | None:
    start, end = slots.get("start_date"), slots.get("end_date")
    if start and end:
        return start, end
    year = slots.get("year")
    if isinstance(year, int):
        return f"{year}-01-01", f"{year}-12-31"
    return None


def _call_window(args: dict[str, Any]) -> tuple[str, str] | None:
    start, end = args.get("start_date"), args.get("end_date")
    if start and end:
        return str(start), str(end)
    year = args.get("year")
    if isinstance(year, int):
        return f"{year}-01-01", f"{year}-12-31"
    return None


def _call_dataset(name: str, args: dict[str, Any]) -> str | None:
    if name == "visualization_create":
        return next((key for key, viz in _VIZ.items() if viz == args.get("dataset")), None)
    return args.get("dataset")


def _enumerated_years(lower: str, slots: dict[str, Any]) -> list[int]:
    """Years the question names separately, which get one call each."""
    years = [int(item) for item in (slots.get("years") or [])]
    explicit = set(re.findall(r"\b20\d{2}\b", lower))
    return years if len(years) > 1 and len(explicit) > 1 else []


def _unrepresented(
    question: str, slots: dict[str, Any], calls: list[tuple[str, dict[str, Any]]]
) -> str | None:
    """The first resolved slot or constraint the calls drop, or None when all hold."""
    lower = " ".join(question.lower().split())
    dataset = slots.get("dataset")
    if not dataset:
        return "dataset"
    if not calls:
        return "no calls"
    if len(calls) > MAX_ENTITY_CALLS:
        return "more than the call limit"
    filters = _dataset_filters(dataset)
    names = [name for name, _ in calls]

    # Dataset: every call reads the resolved dataset.
    if any(_call_dataset(name, args) != dataset for name, args in calls):
        return "dataset"
    # A count for a utility the dataset holds no rows for (label rule I: EPSS
    # is PG&E only) would read as zero when the data is absent. The plan is
    # refused and the deferral stands.
    for _name, args in calls:
        if args.get("utility"):
            gap = utility_coverage_gap(dataset, [str(args["utility"])])
            if gap is not None:
                return NOT_COVERED_RULES[gap["dataset"]]

    # Output form and measure.
    if _MAP_ASK.search(lower) and not any(
        name == "visualization_create" and args.get("kind") == "map" for name, args in calls
    ):
        return "map wording"
    if _LIST_ASK.search(lower) and any(
        args.get("result_mode") != "records"
        for name, args in calls
        if name == "data_query_records"
    ):
        return "list or records wording"
    has_series = any(
        name == "visualization_create" and args.get("kind") == "time_series"
        for name, args in calls
    )
    if bool(_SERIES_ASK.search(lower)) != has_series:
        return "series wording"
    if bool(_BY_COUNTY.search(lower)) != ("data_query_rank" in names):
        return "by-county wording"
    if _OTHER_METRIC.search(lower):
        return "a measure other than a count"
    us_sample_wording = bool(_US_SAMPLE.search(lower))
    if us_sample_wording and dataset != "us_ignitions":
        return "dataset (US-sample wording resolved to another dataset)"
    if (dataset == "us_ignitions" or us_sample_wording) and _US_STATE.search(lower):
        # Label rule H: the US sample has no state filter the calls can carry.
        return "a state (the US sample cannot filter by state)"

    # Each utility and each county: every call carries one, and each is covered.
    for key, slot_key in (("utility", "utilities"), ("county", "counties")):
        wanted = list(slots.get(slot_key) or [])
        if not wanted:
            continue
        if key not in filters:
            return f"{key} (the dataset cannot filter on it)"
        carried = [args.get(key) for _, args in calls]
        if any(value not in wanted for value in carried) or set(carried) != set(wanted):
            return f"each {key}"

    # Date window, including a sub-year window.
    if _UNRESOLVED_WINDOW.search(lower):
        return "a sub-year window the router did not resolve"
    months = _months_named(lower)
    years = _enumerated_years(lower, slots)
    if years:
        if months:
            return "a month window across several years"
        if any(args.get("start_date") or args.get("end_date") for _, args in calls):
            return "the date window"
        call_years = [args.get("year") for _, args in calls]
        if any(value not in years for value in call_years) or set(call_years) != set(years):
            return "each year"
        return None
    window = _resolved_window(slots)
    if window is None or any(_call_window(args) != window for _, args in calls):
        return "the date window"
    if months:
        start, end = date.fromisoformat(window[0]), date.fromisoformat(window[1])
        covered = (
            set(range(start.month, end.month + 1))
            if start.year == end.year
            else set(range(1, 13))
        )
        if not months <= covered:
            return "the date window (a named month is outside it)"
    return None


def _time_fields(slots: dict[str, Any]) -> dict[str, Any]:
    window = _resolved_window(slots)
    year = slots.get("year")
    if window is None:
        return {}
    if isinstance(year, int) and window == (f"{year}-01-01", f"{year}-12-31"):
        return {"year": year}
    fields: dict[str, Any] = {"start_date": window[0], "end_date": window[1]}
    if isinstance(year, int):
        fields["year"] = year
    return fields


def _candidate_calls(question: str, slots: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Calls built from slots. The invariant decides whether they stand."""
    lower = " ".join(question.lower().split())
    dataset = slots.get("dataset")
    if not dataset:
        return []
    utilities = list(slots.get("utilities") or [])
    counties = list(slots.get("counties") or [])
    one = {
        **({"utility": utilities[0]} if len(utilities) == 1 else {}),
        **({"county": counties[0]} if len(counties) == 1 else {}),
    }
    count = {"dataset": dataset, "result_mode": "count"}
    if _PER_MONTH.search(lower):
        calls = []
        if _ALSO_TOTAL.search(lower):
            calls.append(("data_query_records", {**count, **one, **_time_fields(slots)}))
        calls.append(
            (
                "visualization_create",
                {
                    "kind": "time_series",
                    "dataset": _VIZ.get(dataset, dataset),
                    "interval": "monthly",
                    **one,
                    **_time_fields(slots),
                },
            )
        )
        return calls
    if _BY_COUNTY.search(lower):
        if dataset not in _RANK_DATASETS:
            return []
        rank = {"dataset": dataset, "group_by": "county", "metric": "count"}
        return [("data_query_rank", {**rank, **one, **_time_fields(slots)})]
    years = _enumerated_years(lower, slots)
    time_options = [{"year": year} for year in years] or [_time_fields(slots)]
    calls = []
    for utility, county, when in itertools.product(
        utilities or [None], counties or [None], time_options
    ):
        args = dict(count)
        if utility:
            args["utility"] = utility
        if county:
            args["county"] = county
        args.update(when)
        calls.append(("data_query_records", args))
    return calls


def fallback_reason(question: str) -> str | None:
    """What a slot plan would drop for this question, or None when it plans."""
    slots = route_question(question).slots
    return _unrepresented(question, slots, _candidate_calls(question, slots))


def slot_tool_calls(question: str) -> list[tuple[str, dict[str, Any]]] | None:
    """Concrete calls for a slot plan. None means fall back to the model path."""
    slots = route_question(question).slots
    calls = _candidate_calls(question, slots)
    if _unrepresented(question, slots, calls) is not None:
        return None
    return calls


def slot_plan(question: str) -> list[str] | None:
    """Tool names of the slot plan, or None when the invariant refuses it."""
    calls = slot_tool_calls(question)
    return [name for name, _ in calls] if calls else None


def apply_slot_plan(decision: RouteDecision, question: str) -> RouteDecision:
    """Replace a deferred multi-entity route when the slot rule can plan it."""
    if decision.path in {"clarification", "unsupported"}:
        return decision
    if decision.rule != "multi_entity_deferred":
        return decision
    calls = slot_tool_calls(question)
    if not calls:
        return decision
    return RouteDecision(
        "deterministic",
        "slot_plan",
        "Router slots name every entity, so the slot rule plans the calls",
        tool_calls=calls,
        slots=decision.slots,
    )
