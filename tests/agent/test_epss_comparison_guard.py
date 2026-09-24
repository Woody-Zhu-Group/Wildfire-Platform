"""Label rule I on comparison routes, and comparison answers that never say None.

The production question below was a period_comparison on epss_outage_count for
SCE and rendered "period A=None, period B=None, delta=None". EPSS is PG&E-only,
so a comparison where no named utility is PG&E clarifies; one that includes
PG&E runs and states the non-PG&E side's reason in a sentence. Service values
here are fixture numbers, not warehouse figures.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.decisions.tool_pick_mode import arguments_for_tool
from services.agent.eval.slot_plan import fallback_reason, slot_tool_calls
from services.agent.orchestrator import AgentOrchestrator, _render_comparison_answer
from services.agent.provider import ModelReply
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


# ---------------------------------------------------------------------------
# Paraphrases of the production question, written for this change.
# ---------------------------------------------------------------------------

ROUTER_PARAPHRASES = [
    ("Southern California Edison: 2020 versus 2021 fast-trip outage totals?", ["SCE"]),
    ("compare how many EPSS trips SCE logged in 2021 and in 2022", ["SCE"]),
    ("outages for SCE vs SDG&E last year", ["SCE", "SDGE"]),
]
# No comparison word, so the router defers it to the free model path.
MODEL_PATH_PARAPHRASE = "Edison and outages: was 2021 rougher than 2020 for their customers?"


@pytest.mark.parametrize("question,utilities", ROUTER_PARAPHRASES)
def test_router_paraphrases_of_the_production_question_clarify(question, utilities):
    _assert_rule_i_comparison_clarification(question, utilities)


def test_the_model_path_paraphrase_really_reaches_the_model():
    decision = route_question(MODEL_PATH_PARAPHRASE)
    assert decision.path == "model", decision.rule
    assert decision.slots["utilities"] == ["SCE"]


# ---------------------------------------------------------------------------
# The executor is the coverage guarantee on every path.
# ---------------------------------------------------------------------------


class _RecordingBackend:
    """The fixture comparison service, recording every request that reaches it."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        return _comparison_service(request)


def _executor(backend: _RecordingBackend, settings: AgentSettings | None = None) -> ToolExecutor:
    return ToolExecutor(
        settings or AgentSettings(max_tool_steps=3),
        ArtifactStore(60),
        transport=httpx.MockTransport(backend),
    )


def _execute(tool: str, args: dict):
    backend = _RecordingBackend()
    executor = _executor(backend)

    async def run():
        try:
            # The utilities the question names, as the orchestrator passes them.
            named = list(args.get("utilities") or []) + [
                str(value) for value in (args.get("utility"), args.get("scope")) if value
            ]
            return await executor.execute(
                tool, args, request_id="t", attempt=1, harness_call=True, utilities=named
            )
        finally:
            await executor.close()

    return asyncio.run(run()), backend


_SCE_PERIODS = {
    "kind": "periods",
    "scope_type": "utility",
    "scope": "SCE",
    "metric": "epss_outage_count",
    "period_a_start": "2020-01-01",
    "period_a_end": "2020-12-31",
    "period_b_start": "2021-01-01",
    "period_b_end": "2021-12-31",
}


@pytest.mark.parametrize(
    "tool,args,dataset,utilities",
    [
        ("comparison_run", _SCE_PERIODS, "epss_outages", ["SCE"]),
        (
            "comparison_run",
            {"kind": "utilities", "utilities": ["SCE", "SDGE"], "metric": "epss_to_ignition_ratio",
             "start_date": "2023-01-01", "end_date": "2023-12-31"},
            "epss_outages",
            ["SCE", "SDGE"],
        ),
        (
            "data_query_records",
            {"dataset": "epss_outages", "result_mode": "count", "utility": "SCE", "year": 2022},
            "epss_outages",
            ["SCE"],
        ),
        (
            "visualization_create",
            {"kind": "map", "dataset": "epss", "utility": "SDGE", "year": 2023},
            "epss_outages",
            ["SDGE"],
        ),
        (
            "data_query_records",
            {"dataset": "us_ignitions", "result_mode": "count", "utility": "PGE", "year": 2023},
            "us_ignitions",
            ["PGE"],
        ),
    ],
)
def test_the_executor_returns_not_covered_and_never_calls_the_service(tool, args, dataset, utilities):
    result, backend = _execute(tool, args)
    assert backend.requests == []
    assert not result.ok
    assert result.error["code"] == "not_covered"
    assert result.error["recoverable"] is False
    gap = result.error["not_covered"]
    assert gap["dataset"] == dataset and gap["utilities"] == utilities
    assert gap["reason"] and gap["alternatives"]
    assert "absent, not zero" in result.error["message"]


def test_the_executor_runs_a_comparison_that_names_a_covered_utility():
    result, backend = _execute(
        "comparison_run",
        {"kind": "utilities", "utilities": ["PGE", "SCE"], "metric": "epss_outage_count",
         "start_date": "2022-01-01", "end_date": "2022-12-31"},
    )
    assert result.ok and len(backend.requests) == 1


class _ScriptedModel:
    """Emits fixed tool calls; synthesis must never run after a not-covered read."""

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.calls = calls
        self.routing_turns = 0

    async def complete(self, **kwargs):
        if kwargs.get("tools"):
            self.routing_turns += 1
            return ModelReply(
                content="",
                tool_calls=[
                    {"id": f"call_{i}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}
                    for i, (name, args) in enumerate(self.calls, start=1)
                ],
                raw={"choices": [{"finish_reason": "tool_calls"}]},
                latency_ms=1.0,
                usage={},
            )
        raise AssertionError("synthesis must not run after a not-covered read")


@pytest.mark.parametrize(
    "calls",
    [
        [("comparison_run", _SCE_PERIODS)],
        [
            ("data_query_records", {"dataset": "epss_outages", "result_mode": "count", "utility": "SCE", "year": 2020}),
            ("data_query_records", {"dataset": "epss_outages", "result_mode": "count", "utility": "SCE", "year": 2021}),
        ],
    ],
)
def test_the_free_model_path_paraphrase_clarifies_instead_of_reporting_zero(calls):
    backend = _RecordingBackend()
    settings = AgentSettings(max_tool_steps=3)
    executor = _executor(backend, settings)
    model = _ScriptedModel(calls)

    async def run():
        try:
            return (await AgentOrchestrator(settings, model, executor).ask(MODEL_PATH_PARAPHRASE)).response
        finally:
            await executor.close()

    response = asyncio.run(run())
    text = response["answer_text"]
    assert model.routing_turns == 1
    assert backend.requests == []
    assert response["status"] == "clarification", text
    assert "EPSS is PG&E-only" in text and "absent, not zero" in text
    assert "PSPS events and CPUC ignitions" in text
    assert " 0 " not in f" {text} " and "None" not in text


def _ask_with(decision_or_settings, question: str, backend: _RecordingBackend, settings: AgentSettings):
    executor = _executor(backend, settings)

    async def run():
        try:
            return (await AgentOrchestrator(settings, NoModel(), executor).ask(question)).response
        finally:
            await executor.close()

    return asyncio.run(run())


def test_a_deterministic_call_the_router_let_through_still_hits_the_executor(monkeypatch):
    from services.agent.routing import RouteDecision

    decision = RouteDecision(
        "deterministic",
        "filtered_records",
        "simulated router gap",
        tool_calls=[
            ("data_query_records", {"dataset": "epss_outages", "result_mode": "count", "utility": "SCE", "year": 2022})
        ],
        slots={"utilities": ["SCE"], "year": 2022, "years": [2022]},
    )
    monkeypatch.setattr("services.agent.orchestrator.route_question", lambda *a, **k: decision)
    backend = _RecordingBackend()
    response = _ask_with(decision, "SCE EPSS outages 2022", backend, AgentSettings(max_tool_steps=3))
    assert backend.requests == []
    assert response["status"] == "clarification"
    assert "absent, not zero" in response["answer_text"]


def test_a_jev_template_that_built_a_non_pge_epss_call_still_hits_the_executor(tmp_path, monkeypatch):
    from dataclasses import replace

    from services.agent.decisions.tool_pick_mode import ToolPickDecision

    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.decide_tool_pick",
        lambda *a, **k: ToolPickDecision("data_query_records", 0.95, "jev", "above_threshold", 1.0),
    )
    # A template without the rule I guard, so the executor is all that is left.
    monkeypatch.setattr(
        "services.agent.decisions.tool_pick_mode.arguments_for_tool",
        lambda *a, **k: {"dataset": "epss_outages", "result_mode": "count", "utility": "SCE", "year": 2021},
    )
    settings = replace(
        AgentSettings(max_tool_steps=3),
        jev_mode="tool_pick_template",
        jev_log_path=str(tmp_path / "jev.jsonl"),
    )
    backend = _RecordingBackend()
    response = _ask_with(settings, MODEL_PATH_PARAPHRASE, backend, settings)
    assert backend.requests == []
    assert response["status"] == "clarification"
    assert "absent, not zero" in response["answer_text"]


# ---------------------------------------------------------------------------
# A comparison never drops a named utility, county, tier, or month.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question,dropped",
    [
        ("Compare SCE ignitions tier 2 vs tier 3 in 2023", "utility"),
        ("Compare PG&E and SCE ignitions in Butte County in 2023", "county"),
        ("Compare PG&E ignitions in tier 3 2021 vs 2022", "HFTD tier"),
        ("Compare PG&E and SCE CAL FIRE incidents in July 2023", "month"),
    ],
)
def test_a_comparison_that_would_drop_a_named_constraint_clarifies(question, dropped):
    decision = route_question(question)
    assert decision.path == "clarification", decision.rule
    assert decision.rule == "unexpressed_filter_constraints"
    assert decision.tool_calls == []
    assert dropped in decision.answer


@pytest.mark.parametrize(
    "question,rule",
    [
        ("Compare ignitions in tier 2 vs tier 3 in 2023", "hftd_comparison"),
        # EPSS covers PG&E only, so the tier comparison already is PG&E's.
        ("Compare PG&E EPSS outages tier 2 vs tier 3 in 2023", "hftd_comparison"),
        ("Compare PG&E and SCE ignitions in 2023", "utility_comparison"),
    ],
)
def test_comparisons_that_carry_every_named_constraint_still_run(question, rule):
    assert route_question(question).rule == rule


def test_a_jev_comparison_template_never_drops_a_named_county():
    question = "Compare PG&E and SCE ignitions in Butte County in 2023"
    slots = route_question(question).slots
    assert arguments_for_tool("comparison_run", slots, question) is None
