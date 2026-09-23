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
        JevFacts(intent="spatial_context", has_time_scope=0.1),
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
        JevFacts(measure="other_measure"),
        "unsupported",
        None,
        "unsupported_other_measure",
    ),
    (
        "measure_is_judgment",
        JevFacts(measure="other_measure"),
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
    }
    for name, facts, disposition, reason, topic in CASES:
        outcome = derive_outcome(facts, question=questions.get(name, "How many PGE ignitions in 2024?"))
        assert outcome.disposition == disposition, name
        assert outcome.clarify_reason == reason, (name, outcome.clarify_reason)
        assert outcome.unsupported_topic == topic, (name, outcome.unsupported_topic)
        assert name.replace("multi_intent_stays_answer", "multi_intent_count_and_trend") in outcome.trace


def test_rank_triples_match_routing_source():
    import inspect
    import re

    from services.agent.decisions.jev_policy import ALLOWED_RANK_TRIPLES
    from services.agent.routing import _route_ranking

    found = set(re.findall(r'\("(\w+)", "(\w+)", "(\w+)"\)', inspect.getsource(_route_ranking)))
    assert found == ALLOWED_RANK_TRIPLES


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


def test_hftd_map_does_not_need_a_year():
    outcome = derive_outcome(JevFacts(intent="map", dataset="hftd", has_time_scope=0.0))
    assert outcome.disposition == "answer"


def test_deciding_margin_is_distance_from_threshold():
    outcome = derive_outcome(JevFacts(broad_region=0.8))
    assert abs(outcome.deciding_margins["broad_region"] - 0.3) < 1e-9
    assert abs(outcome.confidence - 0.3) < 1e-9
