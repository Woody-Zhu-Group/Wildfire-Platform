"""Table-driven checks that jev_policy mirrors routing policies."""

from services.agent.decisions.jev_policy import JevFacts, covered_rule_ids, derive_outcome
from services.agent.decisions.mapping import policy_rule_ids
from services.agent.decisions.schemas import POLICY_SENTENCES


def test_every_policy_rule_is_covered_or_regex_only(capsys):
    from services.agent.decisions.jev_policy import REGEX_ONLY

    covered = covered_rule_ids()
    regex_only = set(REGEX_ONLY)
    rules = policy_rule_ids()
    print("rule_id | where")
    for rule_id in sorted(rules):
        if rule_id in covered:
            where = "jev_policy"
        elif rule_id in regex_only:
            where = "regex-only: " + REGEX_ONLY[rule_id]
        else:
            where = "MISSING"
        print(f"{rule_id} | {where}")
    missing = rules - covered - regex_only
    assert not missing, sorted(missing)
    assert set(POLICY_SENTENCES) <= covered | regex_only
    overlap = covered & regex_only
    assert not overlap, sorted(overlap)


CASES = [
    (
        "ambiguous_risk_metric",
        JevFacts(asks_risk=0.9, names_risk_metric=0.1),
        "clarify",
        "ambiguous_risk_metric",
        None,
    ),
    (
        "missing_location",
        JevFacts(vague_proximity=0.8, names_specific_place=0.1),
        "clarify",
        "missing_location",
        None,
    ),
    (
        "undefined_spatial_scope",
        JevFacts(vague_proximity=0.8, names_specific_place=0.9),
        "clarify",
        "undefined_spatial_scope",
        None,
    ),
    (
        "undefined_region",
        JevFacts(broad_region=0.9),
        "clarify",
        "undefined_region",
        None,
    ),
    (
        "ambiguous_relative_time",
        JevFacts(vague_time=0.9, has_time_scope=0.1),
        "clarify",
        "ambiguous_relative_time",
        None,
    ),
    (
        "time_out_of_coverage",
        JevFacts(has_time_scope=0.9),
        "clarify",
        "time_out_of_coverage",
        None,
    ),
    (
        "risk_future_date",
        JevFacts(asks_risk=0.9, names_risk_metric=0.9, names_specific_place=0.9, has_time_scope=0.9, future_time=0.9),
        "clarify",
        "risk_future_date",
        None,
    ),
    (
        "risk_missing_place",
        JevFacts(asks_risk=0.9, names_risk_metric=0.9, names_specific_place=0.1, has_time_scope=0.9),
        "clarify",
        "risk_missing_place",
        None,
    ),
    (
        "forecast_missing_date",
        JevFacts(asks_risk=0.9, names_risk_metric=0.9, names_specific_place=0.9, has_time_scope=0.1),
        "clarify",
        "forecast_missing_date",
        None,
    ),
    (
        "ambiguous_risk_place",
        JevFacts(
            asks_risk=0.9,
            names_risk_metric=0.9,
            names_specific_place=0.9,
            has_time_scope=0.9,
            county="sacramento",
            utilities={"PGE": 0.9},
        ),
        "clarify",
        "ambiguous_risk_place",
        None,
    ),
    (
        "unsupported_cpz",
        JevFacts(off_topic="cpz"),
        "unsupported",
        None,
        "unsupported_cpz",
    ),
    (
        "unsupported_cost",
        JevFacts(off_topic="cost_or_budget"),
        "unsupported",
        None,
        "unsupported_cost",
    ),
    (
        "unsupported_optimization",
        JevFacts(off_topic="optimization_or_scheduling"),
        "unsupported",
        None,
        "unsupported_optimization",
    ),
    (
        "unsupported_damage",
        JevFacts(off_topic="damage_or_loss"),
        "unsupported",
        None,
        "unsupported_damage",
    ),
    (
        "unsupported_live_web",
        JevFacts(off_topic="live_or_web"),
        "unsupported",
        None,
        "unsupported_live_web",
    ),
    (
        "unsupported_rank_cross_dataset",
        JevFacts(intent="rank", dataset="multiple", has_time_scope=0.9, rank_dimension="county", mentions_multiple_datasets=0.9),
        "unsupported",
        None,
        "unsupported_rank_cross_dataset",
    ),
    (
        "unsupported_rank_us_state",
        JevFacts(intent="rank", dataset="us_ignitions", has_time_scope=0.9, rank_dimension="state"),
        "unsupported",
        None,
        "unsupported_rank_us_state",
    ),
    (
        "unsupported_rank_epss_utility",
        JevFacts(intent="rank", dataset="epss_outages", has_time_scope=0.9, rank_dimension="utility"),
        "unsupported",
        None,
        "unsupported_rank_epss_utility",
    ),
    (
        "unsupported_ranking",
        JevFacts(intent="rank", dataset="cpuc_ignitions", has_time_scope=0.9, rank_dimension="cell"),
        "unsupported",
        None,
        "unsupported_ranking",
    ),
    (
        "unexpressable_county_filter",
        JevFacts(intent="count", dataset="hftd", county="sacramento", has_time_scope=0.9),
        "unsupported",
        None,
        "unexpressable_county_filter",
    ),
    (
        "map_missing_year",
        JevFacts(intent="map", dataset="cpuc_ignitions", has_time_scope=0.1),
        "clarify",
        "map_missing_year",
        None,
    ),
    (
        "trend_missing_year",
        JevFacts(intent="trend", has_time_scope=0.2),
        "clarify",
        "trend_missing_year",
        None,
    ),
    (
        "map_plus_trend_missing_year",
        JevFacts(intent="map_plus_trend", has_time_scope=0.0),
        "clarify",
        "map_plus_trend_missing_year",
        None,
    ),
    (
        "spatial_missing_year",
        JevFacts(intent="spatial_context", measure="event_count", has_time_scope=0.1),
        "clarify",
        "spatial_missing_year",
        None,
    ),
    (
        "records_missing_year",
        JevFacts(intent="count", dataset="cpuc_ignitions", has_time_scope=0.1),
        "clarify",
        "records_missing_year",
        None,
    ),
    (
        "ranking_missing_year",
        JevFacts(intent="rank", dataset="cpuc_ignitions", has_time_scope=0.1, rank_dimension="county"),
        "clarify",
        "ranking_missing_year",
        None,
    ),
    (
        "ranking_missing_slots",
        JevFacts(intent="rank", has_time_scope=0.9, rank_dimension="none", dataset="none"),
        "clarify",
        "ranking_missing_slots",
        None,
    ),
    (
        "ranking_county_contradiction",
        JevFacts(intent="rank", dataset="cpuc_ignitions", has_time_scope=0.9, rank_dimension="county", county="alameda"),
        "clarify",
        "ranking_county_contradiction",
        None,
    ),
    (
        "multi_intent_stays_answer",
        JevFacts(intent="count", dataset="cpuc_ignitions", has_time_scope=0.9, is_multi_intent=0.9),
        "answer",
        None,
        None,
    ),
    (
        "unsupported_other_measure",
        JevFacts(measure="other_measure", intent="count"),
        "unsupported",
        None,
        "unsupported_other_measure",
    ),
    (
        "measure_is_judgment",
        JevFacts(measure="other_measure", intent="rank"),
        "clarify",
        "ambiguous_risk_metric",
        None,
    ),
]


def test_policy_table():
    from services.agent.decisions.jev_policy import REGEX_ONLY

    names = {row[0] for row in CASES}
    missing = set(POLICY_SENTENCES) - names - set(REGEX_ONLY)
    assert not missing, sorted(missing)
    questions = {
        "time_out_of_coverage": "How many ignitions in 2030?",
        "risk_future_date": "",
        "measure_is_judgment": "Which utility had the most dangerous fires last year?",
        "ambiguous_risk_metric": "Which utility is riskiest?",
        "ambiguous_relative_time": "What were recent ignitions?",
        "forecast_missing_date": "What is the fitted risk for Sacramento?",
        "map_missing_year": "Show me the map of PG&E outages",
        "trend_missing_year": "Show the monthly trend of CAL FIRE incidents",
        "map_plus_trend_missing_year": "Map CAL FIRE incidents and show the trend",
        "spatial_missing_year": "How many ignitions happened inside SCE territory?",
        "records_missing_year": "How many PG&E ignitions were there?",
        "ranking_missing_year": "Which county had the most CPUC ignitions?",
    }
    for name, facts, disposition, reason, topic in CASES:
        outcome = derive_outcome(facts, question=questions.get(name, "How many PGE ignitions in 2024?"))
        assert outcome.disposition == disposition, name
        assert outcome.clarify_reason == reason, (name, outcome.clarify_reason)
        assert outcome.unsupported_topic == topic, (name, outcome.unsupported_topic)
        assert name.replace("multi_intent_stays_answer", "multi_intent_count_and_trend") in outcome.trace


def test_other_measure_never_declines_map_or_spatial_intents():
    for intent in ("map", "territory_boundary", "spatial_context"):
        outcome = derive_outcome(
            JevFacts(
                measure="other_measure",
                intent=intent,
                has_time_scope=0.9,
                names_specific_place=0.9,
            ),
            question="Show the SCE utility territory boundary",
        )
        assert outcome.unsupported_topic != "unsupported_other_measure", intent
        assert "unsupported_other_measure" not in outcome.trace, intent
        assert "measure_is_judgment" not in outcome.trace, intent


def test_other_measure_declines_only_gated_intents():
    for intent in ("count", "trend", "records_list"):
        outcome = derive_outcome(
            JevFacts(measure="other_measure", intent=intent, has_time_scope=0.9),
            question="What was the average response time for PG&E in 2020?",
        )
        assert outcome.disposition == "unsupported", intent
        assert outcome.unsupported_topic == "unsupported_other_measure", intent


def test_other_measure_on_a_ranking_or_comparison_asks_which_measure():
    # A ranking or comparison orders by one of the registry's measures, so an
    # other_measure answer asks which one, whatever the wording; no word list.
    from services.agent.decisions.jev_policy import MEASURE_CLARIFY_INTENTS

    assert MEASURE_CLARIFY_INTENTS == {"rank", "compare"}
    for question in (
        "Which utility had the most dangerous fires in 2023?",
        "Which utility had the highest tally of ignitions in 2023?",
        "What was the average response time for PG&E in 2020?",
        "Which utility had the highest ignition rate per customer in 2022?",
    ):
        for intent in MEASURE_CLARIFY_INTENTS:
            outcome = derive_outcome(
                JevFacts(measure="other_measure", intent=intent, has_time_scope=0.9),
                question=question,
            )
            assert outcome.disposition == "clarify", (question, intent)
            assert outcome.clarify_reason == "ambiguous_risk_metric", (question, intent)
            assert "measure_is_judgment" in outcome.trace


def test_rank_triples_match_routing_source():
    import inspect
    import re

    from services.agent.decisions.jev_policy import ALLOWED_RANK_TRIPLES
    from services.agent.routing import _route_ranking

    found = set(re.findall(r'\("(\w+)", "(\w+)", "(\w+)"\)', inspect.getsource(_route_ranking)))
    assert found == ALLOWED_RANK_TRIPLES


def test_compare_without_a_year_clarifies():
    missing = derive_outcome(
        JevFacts(intent="compare", dataset="cpuc_ignitions", has_time_scope=0.2),
        question="Compare utility ignition totals",
    )
    assert missing.disposition == "clarify"
    assert missing.clarify_reason == "records_missing_year"
    answered = derive_outcome(
        JevFacts(intent="compare", dataset="cpuc_ignitions", has_time_scope=0.9),
        question="Compare utility ignition totals in 2024",
    )
    assert answered.disposition == "answer"


def test_acres_metric_is_computed_in_code():
    outcome = derive_outcome(
        JevFacts(
            intent="rank",
            dataset="calfire_incidents",
            rank_dimension="county",
            has_time_scope=0.9,
        ),
        question="Which county had the most acres burned in 2023?",
    )
    assert outcome.disposition == "answer"


def test_riskiest_regex_clarifies_and_named_ignition_risk_answers():
    clarify = derive_outcome(
        JevFacts(asks_risk=0.32, names_risk_metric=0.05, intent="compare"),
        question="Which utility is riskiest?",
    )
    assert clarify.clarify_reason == "ambiguous_risk_metric"
    answer = derive_outcome(
        JevFacts(
            asks_risk=0.92,
            names_risk_metric=0.09,
            names_specific_place=0.84,
            has_time_scope=0.98,
            intent="risk",
        ),
        question="Predict historical ignition risk for cell 400 on 2024-08-15.",
    )
    assert answer.disposition == "answer"


def test_resolved_year_blocks_a_missing_year_clarify_and_a_bare_map_still_asks():
    counted = derive_outcome(
        JevFacts(intent="count", dataset="cpuc_ignitions", has_time_scope=0.41, utilities={"PGE": 0.97}),
        question="How many PG&E utility-attributed ignitions were there this year?",
    )
    assert counted.disposition == "answer"
    mapped = derive_outcome(
        JevFacts(intent="map", dataset="epss_outages", has_time_scope=0.65, utilities={"PGE": 0.97}),
        question="Show me the map of PG&E outages",
    )
    assert mapped.clarify_reason == "map_missing_year"
    dated = derive_outcome(
        JevFacts(intent="map", dataset="epss_outages", has_time_scope=0.65, utilities={"PGE": 0.97}),
        question="Show me the map of PG&E outages in 2024",
    )
    assert dated.disposition == "answer"


def test_coordinate_lookup_skips_spatial_missing_year():
    lookup = derive_outcome(
        JevFacts(intent="spatial_context", has_time_scope=0.23, names_specific_place=0.97),
        question="Which IOU, HFTD tier, and grid cell contain 38.58,-121.49?",
    )
    assert lookup.disposition == "answer"
    territory = derive_outcome(
        JevFacts(intent="spatial_context", measure="event_count", has_time_scope=0.1, utilities={"SCE": 0.9}),
        question="How many ignitions happened inside SCE territory?",
    )
    assert territory.clarify_reason == "spatial_missing_year"
    # A point lookup (what contains this city) takes no time window, so no year is asked.
    contains = derive_outcome(
        JevFacts(intent="spatial_context", measure="other_measure", has_time_scope=0.1, names_specific_place=0.95),
        question="What utility service territory contains Modesto?",
    )
    assert contains.disposition == "answer"


def test_utility_noul_counts_as_a_risk_place():
    placed = derive_outcome(
        JevFacts(
            asks_risk=0.93,
            names_risk_metric=0.73,
            names_specific_place=0.11,
            has_time_scope=0.96,
            intent="risk",
            utilities={"PGE": 0.97},
        ),
        question="What was PGE fitted ignition risk on 2024-08-15?",
    )
    assert placed.disposition == "answer"
    missing = derive_outcome(
        JevFacts(
            asks_risk=0.93,
            names_risk_metric=0.73,
            names_specific_place=0.11,
            has_time_scope=0.96,
            intent="risk",
            utilities={"PGE": 0.4},
        ),
        question="What was the fitted ignition risk on 2024-08-15?",
    )
    assert missing.clarify_reason == "risk_missing_place"


def test_cross_dataset_rank_does_not_require_intent_rank():
    mixed = derive_outcome(
        JevFacts(
            intent="multi_intent",
            dataset="multiple",
            rank_dimension="county",
            mentions_multiple_datasets=0.98,
            has_time_scope=0.96,
            is_multi_intent=0.64,
        ),
        question="Which county had the most CAL FIRE incidents and CPUC ignitions in 2023?",
    )
    assert mixed.unsupported_topic == "unsupported_rank_cross_dataset"
    trend = derive_outcome(
        JevFacts(
            intent="multi_intent",
            dataset="cpuc_ignitions",
            rank_dimension="none",
            mentions_multiple_datasets=0.1,
            has_time_scope=0.96,
            is_multi_intent=0.9,
        ),
        question="Give me the SCE ignition count and its weekly trend for 2024.",
    )
    assert trend.disposition == "answer"
    assert "unsupported_rank_cross_dataset" not in trend.trace


def test_near_me_is_not_a_live_web_refusal():
    outcome = derive_outcome(
        JevFacts(
            off_topic="live_or_web",
            live_web_probability=0.82,
            vague_proximity=0.91,
            names_specific_place=0.02,
            vague_time=0.99,
            clarify_reason="missing_location",
            clarify_reason_confidence=0.89,
        ),
        question="Show recent fires near me.",
    )
    assert outcome.disposition == "clarify"
    assert outcome.clarify_reason == "missing_location"


def test_which_fires_overrides_a_weak_live_web_label():
    outcome = derive_outcome(
        JevFacts(
            off_topic="live_or_web",
            live_web_probability=0.72,
            vague_proximity=0.13,
            names_specific_place=0.03,
            clarify_reason="missing_location",
            clarify_reason_confidence=0.75,
        ),
        question="Which fires should I look at?",
    )
    assert outcome.disposition == "clarify"
    assert outcome.clarify_reason == "missing_location"


def test_fires_burning_right_now_still_refuse():
    outcome = derive_outcome(
        JevFacts(
            off_topic="live_or_web",
            live_web_probability=0.95,
            vague_proximity=0.1,
            names_specific_place=0.05,
            clarify_reason="not_applicable",
            clarify_reason_confidence=0.9,
        ),
        question="What fires are burning right now?",
    )
    assert outcome.disposition == "unsupported"
    assert outcome.unsupported_topic == "unsupported_live_web"
    strong = derive_outcome(
        JevFacts(
            off_topic="live_or_web",
            live_web_probability=0.9,
            vague_proximity=0.1,
            names_specific_place=0.05,
            clarify_reason="missing_location",
            clarify_reason_confidence=0.8,
        ),
        question="What fires are burning right now?",
    )
    assert strong.unsupported_topic == "unsupported_live_web"


def test_hftd_map_does_not_need_a_year():
    outcome = derive_outcome(JevFacts(intent="map", dataset="hftd", has_time_scope=0.0))
    assert outcome.disposition == "answer"


def test_deciding_margin_is_distance_from_threshold():
    outcome = derive_outcome(JevFacts(broad_region=0.8))
    assert abs(outcome.deciding_margins["broad_region"] - 0.3) < 1e-9
    assert abs(outcome.confidence - 0.3) < 1e-9
