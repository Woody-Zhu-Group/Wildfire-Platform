"""Harness-computed changes, every count card, and caveats for every period checked.

The question is the production one that stated no changes, showed 3 of 4
stat cards, and listed only the 2020 ignition-definition companions. Counts
here are fixture values, not warehouse figures.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.derived import DERIVED_TOOL, derive_arithmetic
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import ModelReply
from services.agent.tools import ToolExecution, ToolExecutor
from services.agent.views import plan_views

QUESTION = (
    "Were there more CPUC ignitions in PG&E or SCE territory in 2020 compared to 2023, "
    "and by how much did each change?"
)
ATTRIBUTED = {("PGE", "2020"): 402, ("PGE", "2023"): 374, ("SCE", "2020"): 75, ("SCE", "2023"): 90}
SPATIAL = {("PGE", "2020"): 410, ("PGE", "2023"): 381, ("SCE", "2020"): 79, ("SCE", "2023"): 93}


def _year(params: httpx.QueryParams) -> str:
    return str(params.get("year") or params.get("start_date") or "")[:4]


def _handler(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    utility = params.get("utility")
    year = _year(params)
    if "spatial" in request.url.path:
        return httpx.Response(
            200,
            json={
                "region": {"kind": "utility", "id": utility},
                "start_date": params.get("start_date"),
                "end_date": params.get("end_date"),
                "counts": {"ignitions": SPATIAL[(utility, year)]},
                "meta": {},
            },
        )
    return httpx.Response(
        200,
        json={
            "data": [],
            "meta": {"total": ATTRIBUTED[(utility, year)], "returned": 0, "filters": {"utility": utility}},
        },
    )


def _call(index: int, utility: str, year: int) -> dict:
    return {
        "id": f"call_{index}",
        "type": "function",
        "function": {
            "name": "data_query_records",
            "arguments": json.dumps(
                {"dataset": "cpuc_ignitions", "result_mode": "count", "utility": utility, "year": year}
            ),
        },
    }


class ScriptedProvider:
    """Routes with four counts, then writes a brief that states the derived changes."""

    def __init__(self, brief: str | None = None) -> None:
        self.brief = brief
        self.synthesis_payloads: list[dict] = []

    async def complete(self, **kwargs):
        if kwargs.get("tools"):
            return ModelReply(
                content="",
                tool_calls=[
                    _call(1, "PGE", 2020),
                    _call(2, "PGE", 2023),
                    _call(3, "SCE", 2020),
                    _call(4, "SCE", 2023),
                ],
                raw={"choices": [{"finish_reason": "tool_calls"}]},
                latency_ms=1.0,
                usage={},
            )
        content = kwargs["messages"][-1]["content"]
        payload = json.loads(content.split("Evidence and caveats (JSON):\n", 1)[1])
        self.synthesis_payloads.append(payload)
        derived = next(item for item in payload["evidence"] if item["summary"].get("kind") == "derived_arithmetic")
        brief = self.brief or (
            "PG&E had more utility-attributed CPUC ignitions than SCE in both 2020 and 2023. "
            "PG&E went from 402 in 2020 to 374 in 2023, a decrease of 28, and SCE went "
            "from 75 to 90, an increase of 15."
        )
        return ModelReply(
            content=json.dumps(
                {
                    "status": "answer",
                    "answer": brief,
                    "claims": [{"text": brief, "evidence_ids": [derived["evidence_id"]]}],
                }
            ),
            tool_calls=[],
            raw={"choices": [{"finish_reason": "stop"}]},
            latency_ms=1.0,
            usage={},
        )


def _ask(provider: ScriptedProvider, *, jev_intent: tuple[str, float] | None = ("compare", 0.95)) -> dict:
    """Decide mode with Jev's intent fact; ``jev_intent=None`` runs with Jev off.

    Change figures attach only when Jev reads compare or trend at the gate.
    """
    from dataclasses import replace

    from tests.agent.test_jev_decide import FakeBackend, _answer_facts, _choice

    settings = AgentSettings(max_tool_steps=3)
    backend = None
    if jev_intent is not None:
        settings = replace(settings, jev_mode="decide", jev_backend="typesafe", jev_decide_min_confidence=0.8)
        backend = FakeBackend(_answer_facts(intent=_choice(*jev_intent)))
    executor = ToolExecutor(settings, ArtifactStore(60), transport=httpx.MockTransport(_handler))

    async def run():
        try:
            orchestrator = AgentOrchestrator(settings, provider, executor, decide_backend=backend)
            return (await orchestrator.ask(QUESTION)).response
        finally:
            await executor.close()

    return asyncio.run(run())


def test_synthesis_states_the_harness_computed_changes_and_cites_them():
    provider = ScriptedProvider()
    response = _ask(provider)
    assert response["status"] == "answer"
    assert response["route"]["answer_origin"] == "model"
    assert "decrease of 28" in response["answer_text"]
    assert "increase of 15" in response["answer_text"]
    assert not [e for e in response["trajectory"] if e.get("type") == "grounding_error"]

    derived = [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL]
    assert len(derived) == 1
    counts = {
        (e["arguments"]["utility"], e["arguments"]["year"]): e["id"]
        for e in response["evidence"]
        if e["tool"] == "data_query_records"
    }
    rows = derived[0]["summary"]["derivations"]
    changes = {row["to"]["entity"]: row for row in rows if row["basis"] == "change_over_time"}
    assert changes["utility=PGE"]["difference"] == -28
    assert changes["utility=PGE"]["absolute_difference"] == 28
    assert changes["utility=PGE"]["direction"] == "decrease"
    assert changes["utility=PGE"]["source_evidence_ids"] == sorted(
        [counts[("PGE", 2020)], counts[("PGE", 2023)]]
    )
    assert changes["utility=SCE"]["difference"] == 15
    # Four calls over two periods: each utility's change, and no cross-entity
    # rows the question did not ask for. The structure of the calls decides.
    assert [row["basis"] for row in rows] == ["change_over_time", "change_over_time"]
    assert set(derived[0]["arguments"]["source_evidence_ids"]) == set(counts.values())

    # The model saw the derived evidence in its synthesis payload.
    kinds = [item["summary"].get("kind") for item in provider.synthesis_payloads[0]["evidence"]]
    assert "derived_arithmetic" in kinds


def test_without_jev_s_change_reading_no_change_is_derived_or_shown():
    # Jev off, a count reading, or compare below the gate: the four counts are
    # answered, and the harness withholds the difference and percent change.
    for intent in (None, ("count", 0.95), ("compare", 0.6)):
        provider = ScriptedProvider()
        response = _ask(provider, jev_intent=intent)
        assert not [e for e in response["evidence"] if e["tool"] == DERIVED_TOOL], intent
        assert [e for e in response["trajectory"] if e.get("type") == "derived_evidence_withheld"], intent
        kinds = [item["summary"].get("kind") for item in provider.synthesis_payloads[0]["evidence"]]
        assert "derived_arithmetic" not in kinds, intent


def test_a_change_the_harness_did_not_compute_is_rejected():
    # 49 is not a derived value (PG&E fell by 28); the model must not do arithmetic.
    provider = ScriptedProvider(brief="PG&E ignitions fell by 49 between 2020 and 2023.")
    response = _ask(provider)
    errors = [e for e in response["trajectory"] if e.get("type") == "grounding_error"]
    assert errors and "49" in errors[0]["unsupported_numbers"]
    assert "49" not in response["answer_text"]


def test_every_count_gets_a_stat_card():
    response = _ask(ScriptedProvider())
    cards = [view for view in response["views"] if view["type"] == "stat_card"]
    assert len(cards) == 4
    shown = sorted((card["params"]["period"], card["params"]["value"]) for card in cards)
    assert shown == sorted((period, float(value)) for (_utility, period), value in ATTRIBUTED.items())


def test_the_ignition_definition_caveat_covers_every_period_and_utility():
    response = _ask(ScriptedProvider())
    by_id = {item["id"]: item["text"] for item in response["qualifications"]}
    for utility in ("PGE", "SCE"):
        text = by_id[f"ignition_definition_{utility.lower()}"]
        for year in ("2020", "2023"):
            assert f"{ATTRIBUTED[(utility, year)]:,} in {year}" in text
            assert f"{SPATIAL[(utility, year)]:,} in {year}" in text
    companions = [e for e in response["evidence"] if e["qualification_call"] and e["tool"] == "data_query_spatial"]
    assert len(companions) == 4


def _count(evidence_id: str, utility: str, year: int, total: int, *, qualification: bool = False) -> ToolExecution:
    return ToolExecution(
        tool="data_query_records",
        arguments={"dataset": "cpuc_ignitions", "result_mode": "count", "utility": utility, "year": year},
        ok=True,
        summary={"dataset": "cpuc_ignitions", "result_mode": "count", "total": total},
        raw={},
        error=None,
        artifact=None,
        latency_ms=1,
        evidence_id=evidence_id,
        qualification_call=qualification,
    )


def test_percent_change_and_ratio_with_a_zero_base_are_undefined_not_numbers():
    derived = derive_arithmetic(
        "What was the percent change and ratio of SCE ignitions from 2020 to 2023?",
        [_count("evidence_a", "SCE", 2020, 0), _count("evidence_b", "SCE", 2023, 12)],
    )
    row = derived.summary["derivations"][0]
    assert row["difference"] == 12
    assert row["percent_change"] is None and "0" in row["percent_change_reason"]
    assert row["ratio"] is None


def test_percent_change_is_rounded_and_signed():
    derived = derive_arithmetic(
        "What was the percent change in PG&E ignitions from 2020 to 2023?",
        [_count("evidence_b", "PGE", 2023, 374), _count("evidence_a", "PGE", 2020, 402)],
    )
    row = derived.summary["derivations"][0]
    assert row["from"]["period"] == "2020" and row["to"]["period"] == "2023"
    assert row["percent_change"] == -7.0
    assert row["absolute_percent_change"] == 7


def test_nothing_is_derived_from_companions():
    executions = [
        _count("evidence_a", "PGE", 2020, 402),
        _count("evidence_b", "PGE", 2023, 374, qualification=True),
    ]
    assert derive_arithmetic("By how much did PG&E ignitions change?", executions) is None


def test_the_same_entity_in_two_periods_is_derived_whatever_the_question_says():
    # No change word at all: the structure of the calls decides.
    derived = derive_arithmetic(
        "How many PG&E ignitions in 2020 and 2023?",
        [_count("evidence_a", "PGE", 2020, 402), _count("evidence_b", "PGE", 2023, 374)],
    )
    row = derived.summary["derivations"][0]
    assert row["basis"] == "change_over_time"
    assert row["difference"] == -28
    assert row["percent_change"] == -7.0
    assert row["ratio"] == 0.93
    assert derived.arguments["operations"] == ["difference", "percent_change", "ratio"]


def test_two_entities_in_one_shared_period_get_their_difference():
    derived = derive_arithmetic(
        "How many more ignitions did PG&E have than SCE in 2023?",
        [_count("evidence_a", "PGE", 2023, 374), _count("evidence_b", "SCE", 2023, 90)],
    )
    rows = derived.summary["derivations"]
    assert len(rows) == 1
    row = rows[0]
    assert row["basis"] == "difference_between_entities"
    assert row["from"]["entity"] == "utility=PGE" and row["to"]["entity"] == "utility=SCE"
    assert row["difference"] == 90 - 374
    assert row["larger"] == "utility=PGE"
    assert "direction" not in row


def test_cross_entity_rows_need_every_call_in_one_period():
    # One utility read twice and another once: the change over time only.
    derived = derive_arithmetic(
        "Did PG&E ignitions fall more than SCE's between 2020 and 2023?",
        [
            _count("evidence_a", "PGE", 2020, 402),
            _count("evidence_b", "PGE", 2023, 374),
            _count("evidence_c", "SCE", 2023, 90),
        ],
    )
    rows = derived.summary["derivations"]
    assert [row["basis"] for row in rows] == ["change_over_time"]
    assert rows[0]["to"]["entity"] == "utility=PGE"


def test_count_cards_are_not_capped_but_other_stats_are():
    executions = [_count(f"evidence_{i}", "PGE", 2016 + i, i) for i in range(5)]
    planned = plan_views(executions, status="answer", slots={})
    assert len([v for v in planned.views if v.type == "stat_card"]) == 5
