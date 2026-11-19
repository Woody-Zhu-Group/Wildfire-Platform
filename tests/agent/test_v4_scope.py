from services.agent.decisions import v3, v4, v4_scope
from services.agent.decisions.jev_first import decide_from_answers
from tests.agent.test_jev_decide import _choice, _noul
from tests.agent.test_jev_first import facts


def test_payload_replaces_one_fact_and_preserves_other_calls():
    question = "Show the statewide fitted risk grid for 2024-08-15."
    old = v4.calls_for(question, "2026-09-29")
    new = v4_scope.calls_for(question, "2026-09-29")
    assert old[1:] == new[1:]
    old_facts, new_facts = old[0]["questions"], new[0]["questions"]
    assert set(old_facts) - set(new_facts) == {"broad_region"}
    assert set(new_facts) - set(old_facts) == {v4_scope.SCOPE_FACT}
    for key in old_facts.keys() & new_facts.keys():
        assert old_facts[key] == new_facts[key]
    assert new_facts[v4_scope.SCOPE_FACT].kind == "noul"
    assert "broad_region" in v3.fact_questions()
    assert v4.calls_for(question, "2026-09-29") == old


def _scope_facts(intent="count", missing=0.0):
    values = facts(intent)
    del values["broad_region"]
    values[v4_scope.SCOPE_FACT] = _noul(missing)
    return values


def test_defined_statewide_grid_does_not_need_a_county():
    values = _scope_facts("risk_surface")
    values["dataset"] = _choice("none")
    values["measure"] = _choice("risk_grid")
    values["risk_map_kind"] = _choice("risk")
    decision = decide_from_answers(
        "Show the statewide fitted risk grid for 2024-08-15.",
        values,
        use_confidence=False,
        geography_fact=v4_scope.SCOPE_FACT,
    )
    assert decision.path == "deterministic"
    assert decision.tool_calls == [("risk_surface", {"date": "2024-08-15"})]


def test_missing_required_county_clarifies():
    decision = decide_from_answers(
        "How many CPUC ignitions occurred in the county in 2024?",
        _scope_facts(missing=0.9),
        use_confidence=False,
        geography_fact=v4_scope.SCOPE_FACT,
    )
    assert decision.rule == "missing_geographic_scope"
    assert decision.path == "clarification" and not decision.tool_calls


def test_no_requested_geographic_filter_can_use_full_coverage():
    decision = decide_from_answers(
        "Count CPUC ignitions in 2024.",
        _scope_facts(),
        use_confidence=False,
        geography_fact=v4_scope.SCOPE_FACT,
    )
    assert decision.path == "model"


def test_a_missing_new_fact_is_not_replaced_with_the_old_broadness_value():
    values = facts()
    decision = decide_from_answers(
        "Count CPUC ignitions in 2024.",
        values,
        use_confidence=False,
        geography_fact=v4_scope.SCOPE_FACT,
    )
    assert decision.path == "clarification"


def test_pairing_shares_the_unchanged_calls():
    from services.agent.eval.v4_scope_compare import load_cases, paired_calls

    cases = load_cases()
    assert len(cases) == len({case["id"] for case in cases}) == 50
    calls = paired_calls(cases[0]["question"], "2026-09-29")
    assert set(calls) == {"facts_old", "facts_new", "topic", "places"}
    assert (
        calls["facts_old"]["questions"]["future_time"]
        == calls["facts_new"]["questions"]["future_time"]
    )


def test_paired_report_rejects_a_changed_request():
    import pytest
    from services.agent.eval.v4_scope_compare import evaluate

    with pytest.raises(ValueError):
        evaluate(
            [
                {
                    "case": {"id": "test", "question": "Count CPUC ignitions in 2024."},
                    "today": "2026-09-29",
                    "model": "test",
                    "requests": {},
                    "payload_hash": "wrong",
                    "error": None,
                }
            ]
        )
