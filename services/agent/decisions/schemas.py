"""Jev question catalog, versioned so logs stay comparable across schema changes.

Choice allows at most 255 options (TypeSafe API). The county question is the
58 California counties plus none (59), which is inside that limit, so v1 asks
county in the same call as the rest of set A. No documented per-call question
count was found; set A is one system_one call.
"""

from __future__ import annotations

from services.agent.decisions.backend import QuestionSpec
from services.agent.routing import (
    ALL_MODEL_TOOLS,
    UNSUPPORTED,
    UTILITY_PATTERNS,
    _CA_COUNTIES,
)
from services.agent.schemas import TOOL_DESCRIPTIONS

SCHEMA_VERSION = "v2"

UTILITY_NAMES = {
    "PGE": "Pacific Gas and Electric",
    "SCE": "Southern California Edison",
    "SDGE": "San Diego Gas and Electric",
    "PACIFICORP": "PacifiCorp",
    "Liberty": "Liberty Utilities",
    "BVES": "Bear Valley Electric Service",
}

# Clarification rule ids produced by route_question, plus not_applicable.
CLARIFY_REASONS = (
    "ambiguous_risk_metric",
    "missing_location",
    "undefined_spatial_scope",
    "undefined_region",
    "ambiguous_relative_time",
    "time_out_of_coverage",
    "ambiguous_risk_place",
    "forecast_missing_date",
    "risk_future_date",
    "risk_missing_place",
    "map_plus_trend_missing_year",
    "map_missing_year",
    "trend_missing_year",
    "spatial_missing_year",
    "records_missing_year",
    "medical_exposure_missing_year",
    "series_mode_missing_year",
    "series_mode_missing_dataset",
    "ranking_missing_slots",
    "ranking_missing_year",
    "ranking_county_contradiction",
    "unexpressed_filter_constraints",
    "not_applicable",
)

# Unsupported rule ids, including ranking refusals that are not in UNSUPPORTED.
UNSUPPORTED_TOPICS = tuple(
    f"unsupported_{key}" for key in UNSUPPORTED
) + (
    "unsupported_rank_cross_dataset",
    "unsupported_rank_us_state",
    "unsupported_rank_epss_utility",
    "unsupported_ranking",
    "unexpressable_county_filter",
    "not_applicable",
)

INTENTS = (
    "count",
    "records_list",
    "map",
    "trend",
    "map_plus_trend",
    "compare",
    "rank",
    "risk",
    "spatial_context",
    "territory_boundary",
    "circuit_detail",
    "exploratory_overview",
    "multi_intent",
    "other",
)

# comparison_run.kind values. HFTD is region_type=hftd, not a separate kind.
COMPARISON_KINDS = ("utilities", "regions", "periods", "not_applicable")

# One sentence per clarify or refuse rule. The context string is built from these
# so a test can prove every routing policy is actually sent to Jev.
POLICY_SENTENCES: dict[str, str] = {
    "ambiguous_risk_metric": "Riskiest, most risky, or highest risk without a named metric (fitted cell risk, ignition count, CAL FIRE incidents, or EPSS outages) must be clarified.",
    "missing_location": "Near me with no latitude, longitude, or bounding box must be clarified.",
    "undefined_spatial_scope": "Near, around, or close to a place with no radius, coordinates, or county or utility polygon must be clarified.",
    "undefined_region": "Northern or southern California is not a warehouse region and must be clarified.",
    "ambiguous_relative_time": "Recent, lately, and currently must be clarified; a named year, an exact date, or a simple relative year such as last year is enough.",
    "time_out_of_coverage": "A period outside the years stored for that dataset must be clarified rather than guessed.",
    "ambiguous_risk_place": "Fitted risk accepts one place, so a county and a utility named together must be clarified.",
    "forecast_missing_date": "A risk score needs one historical calendar day through 2025-12-31; if none is given, ask for one.",
    "risk_future_date": "Fitted risk stops on 2025-12-31, so tomorrow, a forward phrase, or any later date must be clarified.",
    "risk_missing_place": "Fitted risk needs a grid cell, county, PGE/SCE/SDGE territory, or coordinates; otherwise ask which place.",
    "map_plus_trend_missing_year": "A map plus a trend with no year or date range must be clarified.",
    "map_missing_year": "A map of events with no year or date range must be clarified, except a timeless HFTD layer.",
    "trend_missing_year": "A time series with no year or date range must be clarified.",
    "spatial_missing_year": "A count inside a territory with no year or date range must be clarified.",
    "records_missing_year": "A count or list that names a dataset but no year or date range must be clarified.",
    "medical_exposure_missing_year": "Medical baseline, life support, or medically vulnerable customers need a year or date range; otherwise ask for one.",
    "series_mode_missing_year": "A yearly, seasonal, cumulative-acres, customer-event, or regional series needs a year or date range; otherwise ask for one.",
    "series_mode_missing_dataset": "A yearly or seasonal chart needs CPUC ignitions, EPSS outages, or CAL FIRE incidents; otherwise ask which dataset.",
    "ranking_missing_slots": "A ranking needs one dataset and one grouping: CPUC county or utility, CAL FIRE county, or EPSS circuit.",
    "ranking_missing_year": "A ranking with no year or date range must be clarified.",
    "ranking_county_contradiction": "Do not rank counties and also filter to one named county; ask which was meant.",
    "unexpressed_filter_constraints": "If a named county or month cannot be applied by the matched read, ask instead of dropping it.",
    "unsupported_cpz": "Circuit protection zones (CPZ) are not in this warehouse, so refuse.",
    "unsupported_cost": "Cost, price, budget, dollars, and economic impact are out of scope, so refuse.",
    "unsupported_optimization": "Requests to optimize, schedule, or allocate resources are out of scope, so refuse.",
    "unsupported_damage": "Property damage, insured or expected loss, and fatalities are out of scope, so refuse.",
    "unsupported_live_web": "Fires burning right now, live status, and web search are out of scope, so refuse.",
    "unsupported_air_quality": "Air quality is not in this warehouse, so refuse.",
    "unsupported_evacuation": "Evacuation routes are out of scope, so refuse.",
    "unsupported_translation": "Translation is out of scope, so refuse.",
    "unsupported_personnel": "Personnel and firefighter deployment are out of scope, so refuse.",
    "unsupported_satellite": "Satellite imagery is out of scope, so refuse.",
    "unsupported_leadership": "Company leadership is out of scope, so refuse.",
    "unsupported_rank_cross_dataset": "A ranking that mixes datasets such as CAL FIRE and CPUC must be refused.",
    "unsupported_rank_us_state": "US ignitions have no state column, so ranking by state must be refused.",
    "unsupported_rank_epss_utility": "EPSS is PG&E only, so ranking it by utility must be refused.",
    "unsupported_ranking": "Any ranking other than CPUC by county or utility, CAL FIRE by county (count or acres), or EPSS by circuit must be refused.",
    "unexpressable_county_filter": "County filters exist only on calfire_incidents, cpuc_ignitions, epss_outages, psps_events, and circuits. Never infer a utility from a place name, and do not answer a statewide count that drops a named county.",
    "unsupported_other_measure": "A measure no warehouse dataset stores, such as response time, smoke, cause, or a rate per customer or per mile, must be refused.",
    "city_needs_place": "A city that is not also a county name is not a query layer, so ask for coordinates, a county, or a utility territory instead of a statewide or county answer.",
    "unknown_county": "A municipality written with the word County is not that city, and if it is not a warehouse county, ask which county was meant.",
    "hftd_constraint_unavailable": "No tool intersects circuits with an HFTD tier or measures HFTD area or acreage, so ask instead of dropping that constraint. A map of one HFTD tier is still allowed.",
}

_CONTEXT_INTRO = (
    "This assistant counts or lists warehouse records, draws a map, charts a time series, ranks inside one dataset, "
    "compares utilities, regions, or two periods, looks up a point or a count inside a territory, shows a utility boundary or one circuit, "
    "and scores historical ignition risk for one place on one past day. "
    "cpuc_ignitions are utility-caused. calfire_incidents are the CAL FIRE map feed, not the Redbook census. "
    "epss_outages are PG&E only. psps_events are shutoffs. us_ignitions is an all-cause sample, not a census. "
    "circuits are inventory lines. hftd is Tier 2 and Tier 3 only. iou_territories are service polygons. "
    "Utilities are PGE, SCE, SDGE, PACIFICORP, Liberty, and BVES. "
    "An attribute utility filter and a count inside that territory are different numbers. House policies:"
)

# Router rules that are deliberately not yet described to Jev. Adding a
# sentence changes DOMAIN_CONTEXT and therefore every payload hash, so each
# entry waits for the next planned context change and its confidence report.
CONTEXT_DEFERRED_RULES: dict[str, str] = {
    "county_place_ambiguous": (
        "A county word used as a different place, or a cue-required county word "
        "without County, is clarified by the router before Jev runs."
    ),
    "epss_non_pge_utility": (
        "An EPSS read for a utility other than PG&E is clarified by the router "
        "before Jev runs, since EPSS rows exist only for PG&E."
    ),
    "unsupported_future_prediction": (
        "A forward modal, expectation, or forecast of events or counts is refused "
        "by the router before Jev runs."
    ),
}

DOMAIN_CONTEXT = _CONTEXT_INTRO + "\n" + "\n".join(POLICY_SENTENCES.values()) + "\n"

# Sentences whose only consumer is the topic call (off_topic, rank_dimension,
# dataset, or the hybrid clarify_reason Choice). Facts, places, and tool pick
# do not return a field these sentences change.
_TOPIC_ONLY_POLICIES = frozenset({
    "medical_exposure_missing_year",
    "series_mode_missing_year",
    "series_mode_missing_dataset",
    "ranking_missing_slots",
    "unsupported_cpz",
    "unsupported_cost",
    "unsupported_optimization",
    "unsupported_damage",
    "unsupported_live_web",
    "unsupported_rank_cross_dataset",
    "unsupported_rank_us_state",
    "unsupported_rank_epss_utility",
    "unsupported_ranking",
})
# Tool pick keeps the intro plus the sentences whose removal dropped a scored
# tool pick. The rest were removed one at a time and the 15 scored cases stayed
# 15/15. Facts and places are unchanged.
_TOOL_PICK_POLICIES = frozenset({
    "ambiguous_risk_metric",
    "missing_location",
    "undefined_spatial_scope",
    "undefined_region",
    "ambiguous_relative_time",
    "ambiguous_risk_place",
    "map_missing_year",
})
_FACT_POLICIES = frozenset({
    "ambiguous_risk_metric",
    "missing_location",
    "undefined_spatial_scope",
    "undefined_region",
    "ambiguous_relative_time",
    "time_out_of_coverage",
    "forecast_missing_date",
    "risk_future_date",
    "risk_missing_place",
    "ambiguous_risk_place",
    "map_plus_trend_missing_year",
    "map_missing_year",
    "trend_missing_year",
    "spatial_missing_year",
    "records_missing_year",
    "ranking_missing_year",
})
_PLACE_POLICIES = frozenset({
    "ambiguous_risk_place",
    "risk_missing_place",
    "ranking_county_contradiction",
    "unexpressed_filter_constraints",
    "unexpressable_county_filter",
})

_INTRO_TASK = (
    "This assistant counts or lists warehouse records, draws a map, charts a time series, ranks inside one dataset, "
    "compares utilities, regions, or two periods, looks up a point or a count inside a territory, shows a utility boundary or one circuit, "
    "and scores historical ignition risk for one place on one past day."
)
_INTRO_DATASETS = (
    "cpuc_ignitions are utility-caused. calfire_incidents are the CAL FIRE map feed, not the Redbook census. "
    "epss_outages are PG&E only. psps_events are shutoffs. us_ignitions is an all-cause sample, not a census. "
    "circuits are inventory lines. hftd is Tier 2 and Tier 3 only. iou_territories are service polygons."
)
_INTRO_UTILITIES = "Utilities are PGE, SCE, SDGE, PACIFICORP, Liberty, and BVES."
_INTRO_ATTRIBUTE = (
    "An attribute utility filter and a count inside that territory are different numbers."
)


def context_for_call(call_name: str) -> str:
    """Policy text for one v3 call. DOMAIN_CONTEXT remains the full string."""
    if call_name == "topic":
        keys = set(POLICY_SENTENCES)
        intro = _CONTEXT_INTRO
    elif call_name == "facts":
        keys = set(_FACT_POLICIES)
        intro = _INTRO_TASK + " House policies:"
    elif call_name == "places":
        keys = set(_PLACE_POLICIES)
        intro = " ".join((_INTRO_TASK, _INTRO_UTILITIES, "House policies:"))
    elif call_name == "tool_pick":
        keys = set(_TOOL_PICK_POLICIES)
        intro = _CONTEXT_INTRO
    else:
        keys = set(POLICY_SENTENCES)
        intro = _CONTEXT_INTRO
    lines = [POLICY_SENTENCES[key] for key in POLICY_SENTENCES if key in keys]
    return intro + "\n" + "\n".join(lines) + "\n"


def _choice(instructions: str, criteria: dict[str, str]) -> QuestionSpec:
    return QuestionSpec(kind="choice", instructions=instructions, criteria=criteria)


def _noul(instructions: str) -> QuestionSpec:
    return QuestionSpec(kind="noul", instructions=instructions, criteria=None)


def county_option_id(name: str) -> str:
    return name.lower().replace(" ", "_")


def routing_questions() -> dict[str, QuestionSpec]:
    """Question set A. One batch, independent of the regex router."""
    questions: dict[str, QuestionSpec] = {
        "disposition": _choice(
            "What should the assistant do with this question?",
            {
                "answer": "The question can be answered from the warehouse, maps, comparisons, or historical risk model.",
                "clarify": "A required place, time, dataset, or metric is missing or ambiguous, so the assistant should ask rather than guess.",
                "unsupported": "The question is outside the assistant's data and tools, including cost, live fires, optimization, or an unavailable ranking.",
            },
        ),
        "clarify_reason": _choice(
            "If the question needs clarification, which single gap is it?",
            {
                "ambiguous_risk_metric": "Risk could mean fitted cell intensity, ignition counts, incidents, or outages, and the question does not say which.",
                "missing_location": "The question says near me but gives no coordinates or bounding box.",
                "undefined_spatial_scope": "Near, around, or close to is used without a radius, polygon, or county boundary.",
                "undefined_region": "A region such as northern or southern California is named but has no warehouse polygon.",
                "ambiguous_relative_time": "A vague time such as recent, lately, or currently is used and cannot be mapped to a year.",
                "time_out_of_coverage": "The named period is outside the years the warehouse or risk model covers.",
                "ambiguous_risk_place": "Both a county and a utility are named, but fitted risk accepts exactly one place.",
                "forecast_missing_date": "A risk question has no single historical calendar day to score.",
                "risk_future_date": "The risk question asks about tomorrow or a date after covariate coverage.",
                "risk_missing_place": "Fitted risk is requested without a cell, county, utility, or coordinates.",
                "map_plus_trend_missing_year": "Both a map and a trend are requested but no year or date range is given.",
                "map_missing_year": "A map is requested but no year or date range is given.",
                "trend_missing_year": "A time series is requested but no year or date range is given.",
                "spatial_missing_year": "A count inside a territory is requested but no year or date range is given.",
                "records_missing_year": "A count or list is requested but no year or date range is given.",
                "medical_exposure_missing_year": "Medical baseline, life support, or medically vulnerable customers are requested but no year or date range is given.",
                "series_mode_missing_year": "A series panel is requested but no year or date range is given.",
                "series_mode_missing_dataset": "A yearly or seasonal chart is requested without CPUC, EPSS, or CAL FIRE.",
                "ranking_missing_slots": "A ranking is requested without one dataset and one grouping such as county or circuit.",
                "ranking_missing_year": "A ranking is requested but no year or date range is given.",
                "ranking_county_contradiction": "The question both names one county and asks to rank counties.",
                "unexpressed_filter_constraints": "A county or month is named that the matched read cannot apply, so the assistant must not drop it silently.",
                "not_applicable": "The question does not need a clarification.",
            },
        ),
        "unsupported_topic": _choice(
            "If the question is out of scope, which unavailable topic is it?",
            {
                "unsupported_cpz": "The question asks about circuit protection zones, which this warehouse does not have.",
                "unsupported_cost": "The question asks about cost, price, budget, or economic impact.",
                "unsupported_optimization": "The question asks to optimize, schedule, or allocate resources.",
                "unsupported_damage": "The question asks about property damage, insured loss, or fatalities.",
                "unsupported_live_web": "The question asks about fires burning right now or wants a live web search.",
                "unsupported_air_quality": "The question asks about air quality.",
                "unsupported_evacuation": "The question asks for an evacuation route.",
                "unsupported_translation": "The question asks for a translation.",
                "unsupported_personnel": "The question asks about personnel or firefighters.",
                "unsupported_satellite": "The question asks for satellite imagery.",
                "unsupported_leadership": "The question asks about company leadership.",
                "unsupported_rank_cross_dataset": "The question ranks or mixes two warehouse datasets in one ranking.",
                "unsupported_rank_us_state": "The question ranks US ignitions by state, and that table has no state column.",
                "unsupported_rank_epss_utility": "The question ranks EPSS outages by utility, but EPSS is PG&E only.",
                "unsupported_ranking": "The question ranks a grouping this warehouse cannot rank, such as grid cell or division.",
                "unexpressable_county_filter": "A county is named on a dataset that has no county column, such as US ignitions.",
                "not_applicable": "The question is in scope for the assistant.",
            },
        ),
        "intent": _choice(
            "What is the single main thing the question is asking for?",
            {
                "count": "A scalar how-many or total for one dataset and period.",
                "records_list": "A list of individual events or records, not a single total.",
                "map": "A geographic map of events, with no separate trend chart requested.",
                "trend": "A time series or trend chart, with no map requested.",
                "map_plus_trend": "Both a map and a time series of the same events.",
                "compare": "A comparison of utilities, regions, or two time periods on one metric.",
                "rank": "A top-N ranking inside one dataset, such as counties or circuits.",
                "risk": "Fitted historical ignition risk for one place and one day.",
                "spatial_context": "What contains a point, or how many events fall inside a territory polygon.",
                "territory_boundary": "The utility service-area polygon itself, not the events inside it.",
                "circuit_detail": "Detail for one named circuit identifier.",
                "exploratory_overview": "An open overview such as tell me about a dataset, with no single metric.",
                "multi_intent": "Two explicit requests that are not just a map plus its trend, such as a count and a trend.",
                "other": "None of the intents above fit.",
            },
        ),
        "dataset": _choice(
            "Which warehouse dataset is the question about?",
            {
                "cpuc_ignitions": "Utility-caused CPUC ignition records, including a named IOU's ignitions when no other catalog is named.",
                "us_ignitions": "The national all-cause FireCastRL ignition sample, not CPUC and not CAL FIRE.",
                "epss_outages": "PG&E EPSS outage records.",
                "psps_events": "Public-safety power shutoff events.",
                "calfire_incidents": "CAL FIRE incident-map records, including acres burned.",
                "circuits": "The circuit inventory, including a 9-digit circuit identifier.",
                "hftd": "High Fire-Threat District Tier 2 or Tier 3 polygons, not ignition events.",
                "iou_territories": "An IOU service-territory boundary, not the events inside it.",
                "multiple": "The question names two or more of these datasets and needs more than one.",
                "none": "No warehouse dataset is named or clearly implied.",
            },
        ),
        "county": _choice(
            "Which California county, if any, does the question name as a place filter?",
            {
                **{
                    county_option_id(name): f"The question names {name} County as the place, not merely a similar word."
                    for name in _CA_COUNTIES
                },
                "none": "The question does not name a California county as a place filter.",
            },
        ),
        "wants_risk": _noul(
            "The question asks for fitted ignition risk or how risky a place was, not merely for ignition counts."
        ),
        "wants_map": _noul(
            "The question asks to see locations on a map, not merely uses the word where in a non-map sense."
        ),
        "is_exploratory": _noul(
            "The question asks for an open overview rather than one specific count, map, ranking, comparison, or risk score."
        ),
    }
    for utility_id, pattern_name in UTILITY_PATTERNS.items():
        full = UTILITY_NAMES[utility_id]
        questions[f"utility_{utility_id}"] = _noul(
            f"The question refers to {full} ({utility_id}), not merely a word that overlaps that name."
        )
        del pattern_name
    missing = [name for name in CLARIFY_REASONS if name not in questions["clarify_reason"].criteria]
    if missing:
        raise RuntimeError(f"clarify_reason criteria missing {missing}")
    missing = [
        name
        for name in UNSUPPORTED_TOPICS
        if name not in questions["unsupported_topic"].criteria
    ]
    if missing:
        raise RuntimeError(f"unsupported_topic criteria missing {missing}")
    return questions


def tool_pick_questions(candidate_tools: list[str]) -> dict[str, QuestionSpec]:
    """Question set B. Criteria are the candidate tools for this question only."""
    criteria: dict[str, str] = {}
    for name in candidate_tools:
        description = TOOL_DESCRIPTIONS.get(name, "").strip()
        sentence = description.split(". ")[0].rstrip(".")
        if sentence:
            sentence = sentence[0].upper() + sentence[1:] + "."
        else:
            sentence = f"Call {name}."
        criteria[name] = sentence
    criteria["clarify"] = (
        "Do not call a tool yet because a required place, time, or dataset is missing."
    )
    criteria["unsupported"] = (
        "Do not call a tool because the question is outside the available data."
    )
    unknown = [name for name in candidate_tools if name not in ALL_MODEL_TOOLS]
    if unknown:
        raise ValueError(f"unknown candidate tools: {unknown}")
    return {
        "tool_pick": _choice(
            "Which single tool should be called first for this question?",
            criteria,
        ),
        "comparison_kind": _choice(
            "If a comparison is required, which comparison_run kind is it?",
            {
                "utilities": "Compare two or more named utilities over one date range.",
                "regions": "Compare HFTD tiers or a list of regions over one date range.",
                "periods": "Compare two date ranges for one utility, county, or HFTD scope.",
                "not_applicable": "The question is not a comparison_run comparison.",
            },
        ),
    }


def state_for(question: str, today: str) -> dict[str, str]:
    return {
        "question": question,
        "today": today,
        "context": DOMAIN_CONTEXT.strip(),
    }
