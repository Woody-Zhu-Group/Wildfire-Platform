"""Label rule I on comparison routes, and comparison answers that never say None.

The production question below was a period_comparison on epss_outage_count for
SCE and rendered "period A=None, period B=None, delta=None". EPSS is PG&E-only,
so a comparison where no named utility is PG&E clarifies; one that includes
PG&E runs and states the non-PG&E side's reason in a sentence. Service values
here are fixture numbers, not warehouse figures.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.decisions.tool_pick_mode import arguments_for_tool
from services.agent.eval.slot_plan import fallback_reason, slot_tool_calls
from services.agent.orchestrator import AgentOrchestrator, _render_comparison_answer
from services.agent.routing import route_question
from services.agent.tools import ToolExecutor
from services.shared.dataset_registry import REASON_EPSS_PGE_ONLY, REASON_NO_COUNTY

PRODUCTION_QUESTION = (
    "hey how bad was fire season for Edison customers in 2020 vs 2021, outage-wise?"
)
PGE_VS_SCE = "Compare PG&E and SCE EPSS outages in 2022"
PGE_PERIODS = "Compare PG&E EPSS outages 2021 vs 2022"
FIXTURE_PGE = {"2021": 812, "2022": 1024}


def _assert_rule_i_comparison_clarification(question: str, utilities: list[str]) -> None:
    decision = route_question(question)
    assert decision.path == "clarification", (question, decision.rule)
    assert decision.rule == "epss_non_pge_utility", (question, decision.rule)
    assert decision.tool_calls == []
    assert "PG&E-only" in decision.answer
    assert "absent, not zero" in decision.answer
    assert "PSPS events or CPUC ignitions" in decision.answer
    for utility in utilities:
        assert utility in decision.answer


def test_the_production_question_clarifies_instead_of_comparing_nulls():
    _assert_rule_i_comparison_clarification(PRODUCTION_QUESTION, ["SCE"])
    decision = route_question(PRODUCTION_QUESTION)
    assert "compare SCE's PSPS events or CPUC ignitions" in decision.answer
    assert decision.slots["years"] == [2020, 2021]


@pytest.mark.parametrize(
    "question,utilities",
    [
        ("Compare SCE and SDG&E EPSS outages in 2022", ["SCE", "SDGE"]),
        ("Compare SCE EPSS-to-ignition ratio 2021 vs 2022", ["SCE"]),
        # hftd_comparison would drop the utility and return PG&E's tier counts.
        ("Compare SCE EPSS outages tier 2 vs tier 3 in 2023", ["SCE"]),
        # Not fully specified, so it used to defer to the model with SCE on EPSS.
        ("Compare SCE EPSS outages", ["SCE"]),
    ],
)
def test_every_comparison_route_without_pge_clarifies_on_epss(question, utilities):
    _assert_rule_i_comparison_clarification(question, utilities)


def test_a_pge_versus_sce_epss_comparison_still_runs():
    decision = route_question(PGE_VS_SCE)
    assert decision.path == "deterministic"
    assert decision.rule == "utility_comparison"
    assert decision.tool_calls == [
        (
            "comparison_run",
            {
                "kind": "utilities",
                "utilities": ["PGE", "SCE"],
                "metric": "epss_outage_count",
                "start_date": "2022-01-01",
                "end_date": "2022-12-31",
                "normalize": "none",
                "ignition_definition": "attribute",
            },
        )
    ]


def test_a_pge_only_period_comparison_is_unchanged():
    decision = route_question(PGE_PERIODS)
    assert decision.path == "deterministic"
    assert decision.rule == "period_comparison"
    assert decision.tool_calls == [
        (
            "comparison_run",
            {
                "kind": "periods",
                "scope_type": "utility",
                "scope": "PGE",
                "metric": "epss_outage_count",
                "period_a_start": "2021-01-01",
                "period_a_end": "2021-12-31",
                "period_b_start": "2022-01-01",
                "period_b_end": "2022-12-31",
                "ignition_definition": "attribute",
            },
        )
    ]


def test_an_epss_region_comparison_with_no_utility_still_runs():
    decision = route_question("Compare EPSS outages in tier 2 vs tier 3 in 2023")
    assert decision.rule == "hftd_comparison"


def test_a_psps_outage_comparison_is_not_read_as_epss():
    decision = route_question("Compare SCE PSPS outages 2020 vs 2021")
    assert decision.rule != "epss_non_pge_utility"
    assert all(
        args.get("metric") != "epss_outage_count" for _tool, args in decision.tool_calls
    )


# ---------------------------------------------------------------------------
# Jev tool_pick templates and the slot planner never build a non-PG&E EPSS call.
# ---------------------------------------------------------------------------


def test_tool_pick_templates_refuse_non_pge_epss():
    slots = {"utilities": ["SCE"], "years": [2020, 2021], "year": None}
    assert arguments_for_tool("comparison_run", slots, PRODUCTION_QUESTION) is None
    epss_slots = {"dataset": "epss_outages", "utilities": ["SCE"], "year": 2022}
    assert arguments_for_tool("data_query_records", epss_slots, "EPSS outages for SCE in 2022") is None
    assert (
        arguments_for_tool("visualization_create", epss_slots, "Map SCE EPSS outages in 2022")
        is None
    )
    pge_slots = {"dataset": "epss_outages", "utilities": ["PGE"], "year": 2022}
    assert arguments_for_tool("data_query_records", pge_slots, "EPSS outages for PG&E in 2022")


def test_the_slot_planner_refuses_a_non_pge_epss_count():
    question = "How many EPSS outages did PG&E and SCE have in 2022?"
    assert route_question(question).rule == "multi_entity_deferred"
    assert slot_tool_calls(question) is None
    assert fallback_reason(question) == "epss_non_pge_utility"


# ---------------------------------------------------------------------------
# Rendering: null comparison values are sentences with the reason, never None.
# ---------------------------------------------------------------------------


def _period(value, start, end, reason=None):
    return {"value": value, "reason": reason, "start_date": start, "end_date": end}


def test_a_both_null_period_comparison_names_the_reason_and_what_exists():
    text = _render_comparison_answer(
        {},
        {
            "kind": "periods",
            "metric": "epss_outage_count",
            "scope_type": "utility",
            "scope": "SCE",
            "period_a": _period(None, "2020-01-01", "2020-12-31", REASON_EPSS_PGE_ONLY),
            "period_b": _period(None, "2021-01-01", "2021-12-31", REASON_EPSS_PGE_ONLY),
            "delta": {"value": None, "reason": "Cannot compute delta when either period value is null"},
        },
    )
    assert "None" not in text and "=" not in text
    assert "SCE has no EPSS outage count for 2020 or 2021" in text
    assert REASON_EPSS_PGE_ONLY in text
    assert "no change can be computed" in text
    assert "PSPS events and CPUC ignitions do exist" in text


def test_a_one_null_period_comparison_keeps_the_known_value():
    text = _render_comparison_answer(
        {},
        {
            "kind": "periods",
            "metric": "ignition_count",
            "scope_type": "county",
            "scope": "Butte",
            "period_a": _period(5, "2021-06-01", "2021-08-31"),
            "period_b": _period(None, "2022-06-01", "2022-08-31", "One or more component metrics are null"),
            "delta": {"value": None, "reason": "Cannot compute delta when either period value is null"},
        },
    )
    assert "None" not in text
    assert "5 in 2021-06-01 to 2021-08-31" in text
    assert "There is no value for 2022-06-01 to 2022-08-31: one or more component metrics are null" in text


def test_a_null_region_row_names_the_reason_and_the_datasets_that_have_counties():
    text = _render_comparison_answer(
        {},
        {
            "kind": "regions",
            "metric": "psps_event_count",
            "results": [{"key": "Butte", "value": None, "reason": REASON_NO_COUNTY}],
        },
    )
    assert "None" not in text and "unavailable (" not in text
    assert "Butte has no PSPS event count: no county attribute" in text
    assert "CPUC ignitions, CAL FIRE incidents, and EPSS outages do carry a county" in text


# ---------------------------------------------------------------------------
# End to end through the orchestrator with a fixture comparison service.
# ---------------------------------------------------------------------------


def _comparison_service(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    meta = {"epss_scope": "PGE-only"}
    if request.url.path.endswith("/compare-utilities"):
        results = [
            {"key": "PGE", "value": FIXTURE_PGE["2022"], "raw_value": FIXTURE_PGE["2022"], "reason": None},
            {"key": "SCE", "value": None, "raw_value": None, "reason": REASON_EPSS_PGE_ONLY},
        ]
        return httpx.Response(
            200, json={"metric": params.get("metric"), "normalize": "none", "results": results, "meta": meta}
        )
    if request.url.path.endswith("/compare-periods"):
        a, b = FIXTURE_PGE["2021"], FIXTURE_PGE["2022"]
        return httpx.Response(
            200,
            json={
                "metric": params.get("metric"),
                "normalize": "none",
                "scope_type": "utility",
                "scope": "PGE",
                "period_a": {"key": "period_a", "value": a, "reason": None,
                             "start_date": "2021-01-01", "end_date": "2021-12-31"},
                "period_b": {"key": "period_b", "value": b, "reason": None,
                             "start_date": "2022-01-01", "end_date": "2022-12-31"},
                "delta": {"value": float(b - a), "reason": None},
                "meta": meta,
            },
        )
    raise AssertionError(f"unexpected request {request.url}")


class NoModel:
    async def complete(self, **kwargs):
        raise AssertionError("a deterministic comparison must not call the model")


def _ask(question: str) -> dict:
    settings = AgentSettings(max_tool_steps=3)
    executor = ToolExecutor(settings, ArtifactStore(60), transport=httpx.MockTransport(_comparison_service))

    async def run():
        try:
            return (await AgentOrchestrator(settings, NoModel(), executor).ask(question)).response
        finally:
            await executor.close()

    return asyncio.run(run())


def test_the_production_question_end_to_end_is_a_clarification_with_no_tool_call():
    response = _ask(PRODUCTION_QUESTION)
    assert response["status"] == "clarification"
    assert "None" not in response["answer_text"]
    assert "absent, not zero" in response["answer_text"]


def test_pge_versus_sce_end_to_end_shows_pge_and_says_why_sce_is_missing():
    response = _ask(PGE_VS_SCE)
    text = response["answer_text"]
    assert response["status"] == "answer", text
    assert "None" not in text and "unavailable (" not in text
    assert "PG&E 1,024" in text
    assert f"SCE has no EPSS outage count: {REASON_EPSS_PGE_ONLY}" in text
    assert "SCE's PSPS events and CPUC ignitions do exist" in text


def test_pge_only_period_comparison_end_to_end_states_both_years_and_the_change():
    response = _ask(PGE_PERIODS)
    text = response["answer_text"]
    assert response["status"] == "answer", text
    assert "None" not in text
    assert "PG&E EPSS outage count: 812 in 2021 and 1,024 in 2022, a change of 212." in text
