"""Decide mode never asks for a time or place the question's route does not use.

Production bug (2026-09-24): "What utility service territory contains Modesto?" routes
to city_point_context, but Jev read the intent as spatial_context with no year, and
spatial_missing_year at or above the decline gate asked the user for a year that a
territory lookup never needs. Two fixes:

1. jev_policy.derive_outcome applies the missing-year gates only to intents whose tools
   take a time window (needs_time_window): counts, record lists, maps, trends, and
   spatial counts. Point context, a territory boundary, and circuit detail take none.
2. decide_mode ignores a Jev clarification asking for a time or place that the router's
   chosen deterministic calls do not take (why slot_unused), the way it already ignores
   one about an item the router resolved (contradicts_slot).

The smoke test questions (scripts/smoke_test.sh) are replayed here offline, from the
stored Jev calls where the store has them and against adverse synthetic Jev answers on
every one, so a decide-mode regression on a smoke question fails in pytest before it
reaches production. No test here makes a Jev call.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from services.agent.decisions.decide_mode import (
    ROUTER_DISPOSITION,
    decide_from_answers,
    exemption,
    route_uses,
)
from services.agent.decisions.jev_policy import (
    MISSING_YEAR_RULES,
    TIME_WINDOW_INTENTS,
    derive_outcome,
    facts_from_answers,
    needs_time_window,
)
from services.agent.eval.jev_decide_replay import (
    SMOKE_CHECKS,
    STORE,
    _answers,
    smoke_route_matches,
    stored_row,
)
from services.agent.routing import route_question
from tests.agent.test_jev_decide import _answer_facts, _choice, _noul

MODESTO_Q = "What utility service territory contains Modesto?"
TODAY = date(2026, 9, 23)


def _territory_lookup_facts(intent_confidence: float, **overrides):
    """Jev's reading of the Modesto question: spatial_context, a place, no year."""
    return _answer_facts(
        intent=_choice("spatial_context", intent_confidence),
        dataset=_choice("iou_territories"),
        measure=_choice("other_measure"),
        has_time_scope=_noul(0.1),
        names_specific_place=_noul(0.95),
        **overrides,
    )


# Part 1 fix 1: the policy no longer asks for a year on a lookup that takes none.


@pytest.mark.parametrize("intent_confidence", [0.79, 0.85])
def test_modesto_territory_lookup_answers_at_either_side_of_the_gate(intent_confidence):
    decision = route_question(MODESTO_Q)
    assert decision.path == "deterministic" and decision.rule == "city_point_context"
    answers = _territory_lookup_facts(intent_confidence)
    outcome = derive_outcome(facts_from_answers(answers), question=MODESTO_Q, today=TODAY)
    assert outcome.disposition == "answer", outcome.trace
    result = decide_from_answers(MODESTO_Q, decision, answers, gate=0.8, answer_gate=0.9, today=TODAY)
    assert result.winner == "router" and result.why == "agree"
    assert result.decision is decision
    assert result.decision.rule == "city_point_context"


@pytest.mark.parametrize(
    "intent,measure",
    [
        ("spatial_context", "other_measure"),
        ("spatial_context", None),
        ("territory_boundary", "other_measure"),
        ("circuit_detail", "other_measure"),
        ("risk", "historical_risk"),
        ("exploratory_overview", "other_measure"),
        ("other", "other_measure"),
    ],
)
def test_lookups_that_take_no_time_are_never_gated_on_a_missing_year(intent, measure):
    assert not needs_time_window(intent, measure)
    answers = _answer_facts(
        intent=_choice(intent, 0.95),
        measure=_choice(measure) if measure else _choice("other_measure"),
        has_time_scope=_noul(0.05),
        names_specific_place=_noul(0.95),
    )
    if measure is None:
        answers.pop("measure")
    outcome = derive_outcome(facts_from_answers(answers), question="Which county contains Modesto?", today=TODAY)
    assert outcome.clarify_reason not in MISSING_YEAR_RULES.values(), outcome.trace


@pytest.mark.parametrize("intent", sorted(TIME_WINDOW_INTENTS - {"rank"}))
def test_time_window_intents_still_clarify_a_missing_year(intent):
    assert needs_time_window(intent, "event_count")
    answers = _answer_facts(intent=_choice(intent, 0.95), has_time_scope=_noul(0.05))
    outcome = derive_outcome(
        facts_from_answers(answers), question="Show PG&E ignitions by county", today=TODAY
    )
    assert outcome.disposition == "clarify"
    assert outcome.clarify_reason == MISSING_YEAR_RULES[intent]


@pytest.mark.parametrize("measure", ["event_count", "record_list"])
def test_a_spatial_count_inside_a_territory_still_clarifies_a_missing_year(measure):
    question = "How many ignitions were spatially inside PG&E territory?"
    decision = route_question(question)
    assert decision.path == "clarification" and decision.rule == "spatial_missing_year"
    answers = _answer_facts(
        intent=_choice("spatial_context", 0.85),
        measure=_choice(measure),
        has_time_scope=_noul(0.1),
        names_specific_place=_noul(0.95),
    )
    outcome = derive_outcome(facts_from_answers(answers), question=question, today=TODAY)
    assert outcome.disposition == "clarify" and outcome.clarify_reason == "spatial_missing_year"
    result = decide_from_answers(question, decision, answers, gate=0.8, today=TODAY)
    assert result.winner == "router" and result.why == "agree"
    assert ROUTER_DISPOSITION[result.decision.path] == "clarify"


def test_ranking_missing_year_is_unchanged():
    answers = _answer_facts(
        intent=_choice("rank", 0.95),
        rank_dimension=_choice("county"),
        dataset=_choice("cpuc_ignitions"),
        has_time_scope=_noul(0.05),
    )
    outcome = derive_outcome(
        facts_from_answers(answers), question="Which counties had the most ignitions?", today=TODAY
    )
    assert outcome.clarify_reason == "ranking_missing_year"


# Part 1 fix 2: a Jev clarification for a slot the chosen route does not take is ignored.


def test_route_uses_reads_the_router_tool_calls():
    modesto = route_question(MODESTO_Q)
    assert route_uses("time", modesto) is False
    assert route_uses("place", modesto) is True
    count = route_question("How many PG&E utility-attributed ignitions were there in 2024?")
    assert route_uses("time", count) is True and route_uses("place", count) is True
    boundary = route_question("Show the SCE utility territory boundary")
    assert boundary.path == "deterministic"
    assert route_uses("time", boundary) is False
    # No chosen call: nothing can be said about what the eventual tool needs.
    declined = route_question("How many PG&E ignitions were there?")
    assert declined.path == "clarification"
    assert route_uses("time", declined) is None
    model = route_question("Compare PGE and SCE utility-caused ignition activity.")
    assert model.path == "model"
    assert route_uses("time", model) is None


@pytest.mark.parametrize("intent_confidence", [0.79, 0.85])
@pytest.mark.parametrize(
    "time_rule_facts",
    [
        dict(intent="count"),
        dict(intent="map"),
        dict(intent="spatial_context", measure="event_count"),
    ],
)
def test_a_jev_year_request_on_a_territory_lookup_is_ignored(intent_confidence, time_rule_facts):
    decision = route_question(MODESTO_Q)
    overrides = {
        "intent": _choice(time_rule_facts["intent"], intent_confidence),
        "has_time_scope": _noul(0.1),
        "names_specific_place": _noul(0.95),
    }
    if "measure" in time_rule_facts:
        overrides["measure"] = _choice(time_rule_facts["measure"])
    answers = _answer_facts(**overrides)
    result = decide_from_answers(MODESTO_Q, decision, answers, gate=0.8, answer_gate=0.9, today=TODAY)
    assert result.jev_disposition == "clarify"
    assert result.jev_rule in MISSING_YEAR_RULES.values()
    assert result.jev_confidence == pytest.approx(intent_confidence)
    assert result.winner == "router" and result.why == "slot_unused"
    assert result.decision is decision and result.decision.rule == "city_point_context"


def test_a_jev_year_request_on_a_territory_boundary_is_ignored():
    question = "Show the SCE utility territory boundary"
    decision = route_question(question)
    assert decision.path == "deterministic" and decision.rule == "utility_territory"
    answers = _answer_facts(intent=_choice("map", 0.9), has_time_scope=_noul(0.1))
    result = decide_from_answers(question, decision, answers, gate=0.8, today=TODAY)
    assert result.jev_rule == "map_missing_year"
    assert result.winner == "router" and result.why == "slot_unused" and result.decision is decision


def test_a_jev_place_request_on_a_statewide_call_is_ignored():
    question = "How many CPUC ignitions were there in 2024?"
    decision = route_question(question)
    assert decision.path == "deterministic"
    assert route_uses("place", decision) is False
    near = _answer_facts(vague_proximity=_noul(0.95), names_specific_place=_noul(0.05))
    result = decide_from_answers(question, decision, near, gate=0.8, today=TODAY)
    assert result.jev_rule == "missing_location"
    assert result.winner == "router" and result.why == "slot_unused" and result.decision is decision


def test_slot_unused_maps_to_a_verified_fact_for_users():
    from services.agent.decisions.provenance import decision_source

    decision = route_question(MODESTO_Q)
    slots = {
        **decision.slots,
        "jev_decide": {"winner": "router", "why": "slot_unused", "router_rule": decision.rule},
    }
    source = decision_source(path=decision.path, rule=decision.rule, slots=slots, jev_mode="decide")
    assert source == {"source": "router", "why": "verified_fact", "mode": "decide"}


# Controls: a missing year the route does need still clarifies.


@pytest.mark.parametrize(
    "question,router_rule",
    [
        ("Compare PGE and SCE utility-caused ignition activity.", "open_comparison"),
        ("how many psps events impacted Sonoma County and Napa County?", "multi_entity_deferred"),
    ],
)
def test_a_missing_year_on_a_model_path_question_still_clarifies(question, router_rule):
    decision = route_question(question)
    assert decision.path == "model" and decision.rule == router_rule
    answers = _answer_facts(intent=_choice("compare", 0.9), has_time_scope=_noul(0.1))
    result = decide_from_answers(question, decision, answers, gate=0.8, answer_gate=0.9, today=TODAY)
    assert result.jev_rule == "records_missing_year"
    assert result.winner == "jev" and result.why == "gate"
    assert result.decision.path == "clarification" and result.decision.rule == "records_missing_year"


def test_a_missing_year_below_the_gate_leaves_the_router_on_the_model_path():
    question = "Compare PGE and SCE utility-caused ignition activity."
    decision = route_question(question)
    answers = _answer_facts(intent=_choice("compare", 0.79), has_time_scope=_noul(0.1))
    result = decide_from_answers(question, decision, answers, gate=0.8, today=TODAY)
    assert result.jev_rule == "records_missing_year"
    assert result.winner == "router" and result.why == "below_gate" and result.decision is decision


def test_a_router_missing_year_still_stands_over_a_sure_jev_answer():
    question = "How many PG&E ignitions were there?"
    decision = route_question(question)
    assert decision.rule == "records_missing_year"
    sure = _answer_facts(has_time_scope=_noul(0.99), intent=_choice("count", 0.99))
    result = decide_from_answers(question, decision, sure, gate=0.8, answer_gate=0.9, today=TODAY)
    assert result.winner == "router" and result.why == "code_verified"


def test_a_resolved_place_is_still_contradicts_slot_not_slot_unused():
    question = "How many PG&E utility-attributed ignitions were there in 2024?"
    decision = route_question(question)
    assert route_uses("place", decision) is True
    near = _answer_facts(vague_proximity=_noul(0.95), names_specific_place=_noul(0.05))
    result = decide_from_answers(question, decision, near, gate=0.8, today=TODAY)
    assert result.jev_rule == "missing_location"
    assert result.why == "contradicts_slot"


# Part 1 fix 3: every smoke test question, replayed offline.


def _adverse_answers() -> dict[str, dict]:
    """Jev time and place requests that must not change a route that needs neither."""
    return {
        "year_request_above_gate": _answer_facts(
            intent=_choice("count", 0.85), has_time_scope=_noul(0.1), names_specific_place=_noul(0.95)
        ),
        "year_request_at_gate_edge": _answer_facts(
            intent=_choice("count", 0.79), has_time_scope=_noul(0.1), names_specific_place=_noul(0.95)
        ),
        "spatial_year_request": _territory_lookup_facts(0.85),
        "place_request": _answer_facts(vague_proximity=_noul(0.95), names_specific_place=_noul(0.05)),
    }


@pytest.fixture(scope="module")
def decide_store():
    return json.loads(STORE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("check", SMOKE_CHECKS, ids=[check["id"] for check in SMOKE_CHECKS])
def test_smoke_question_route_is_the_one_the_smoke_test_expects(check):
    decision = route_question(check["question"])
    assert smoke_route_matches(check, decision), (decision.path, decision.rule)


@pytest.mark.parametrize("check", SMOKE_CHECKS, ids=[check["id"] for check in SMOKE_CHECKS])
def test_smoke_question_decide_replay_from_the_store_keeps_the_route(check, decide_store):
    item = {"id": check["id"], "question": check["question"]}
    stored = stored_row(decide_store, "smoke", item)
    if stored is None:
        pytest.skip("no stored Jev call for this smoke question yet (captured on the next capture run)")
    decision = route_question(check["question"])
    answers = _answers(stored["answers"]) if stored.get("answers") else None
    result = decide_from_answers(
        check["question"], decision, answers, gate=0.8, answer_gate=0.9,
        error=stored.get("error"), today=date.fromisoformat(decide_store["today"]),
    )
    assert smoke_route_matches(check, result.decision), (result.decision.path, result.decision.rule, result.why)


@pytest.mark.parametrize("check", SMOKE_CHECKS, ids=[check["id"] for check in SMOKE_CHECKS])
@pytest.mark.parametrize("gate", [0.8, 0.5])
def test_smoke_question_survives_adverse_jev_answers(check, gate):
    """The smoke route stands at the default gate and at a gate low enough to let any Jev decline through.

    A Jev year request cannot win on any smoke question: the year is either in the
    question (contradicts_slot) or not used by the chosen call (slot_unused). A Jev
    place request is checked only where the router chose a call or a backstop fired;
    on a refusal the router made from the question wording (the EPSS utility
    ranking) Jev owns the disposition by design and may clarify instead.
    """
    decision = route_question(check["question"])
    for name, answers in _adverse_answers().items():
        result = decide_from_answers(check["question"], decision, answers, gate=gate, answer_gate=0.9, today=TODAY)
        if name == "place_request" and decision.path == "unsupported" and exemption(decision) is None:
            continue
        assert smoke_route_matches(check, result.decision), (
            name, gate, result.decision.path, result.decision.rule, result.winner, result.why,
        )
