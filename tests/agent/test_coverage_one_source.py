"""Dataset coverage from one source, per count, and months only where a date can stand.

Review of PR #93 found three gaps:

1. A spatial summary for SCE counted EPSS outages inside SCE territory and
   showed epss_outages=0 and a 0 stat card, though EPSS holds PG&E rows only.
   Any count in any tool result for a dataset that does not cover the named
   utility is now marked not covered, in the answer text and the views.
2. The shared month parser read "may" and "march" as months in "which may be
   higher" and "so I can march this to my boss", so comparisons clarified over
   a month nobody asked for.
3. Coverage was hardcoded as "PGE" in the router, the Jev templates, and the
   slot planner. They now read the registry, and the comparison metrics map is
   the comparison service's own.

Counts here are fixture values, not warehouse figures.
"""

from __future__ import annotations

import asyncio
import json
import re
import typing
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.decisions.tool_pick_mode import arguments_for_tool
from services.agent.eval.slot_plan import _months_named
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import ModelReply
from services.agent.routing import NOT_COVERED_RULES, route_question
from services.agent.time_resolve import month_from_text, months_from_text, resolve_time
from services.agent.tools import ToolExecution, ToolExecutor, mark_uncovered_counts
from services.agent.views import GroundingError, StatCardViewParams, ComponentSpec, ground_views
from services.shared import dataset_registry
from services.shared.dataset_registry import (
    COMPARISON_METRIC_DATASETS,
    DATASETS,
    utility_coverage_gap,
)

ROOT = Path(__file__).resolve().parents[2]
SCE_QUESTION = "How many events happened inside SCE territory in 2024?"
SDGE_MODEL_QUESTION = "How busy was SDG&E territory in 2024 for fire and outage events?"
# Service counts inside the territory. EPSS is 0 because EPSS holds PG&E
# circuits only, which is exactly the zero that must never be shown.
TERRITORY_COUNTS = {"ignitions": 7, "epss_outages": 0, "calfire_incidents": 3}


# ---------------------------------------------------------------------------
# 1. Coverage per number
# ---------------------------------------------------------------------------


def _backend(request: httpx.Request) -> httpx.Response:
    params = request.url.params
    if request.url.path.endswith("/spatial/summary"):
        return httpx.Response(
            200,
            json={
                "region": {"kind": "utility", "id": params.get("utility")},
                "start_date": params.get("start_date"),
                "end_date": params.get("end_date"),
                "counts": dict(TERRITORY_COUNTS),
                "meta": {},
            },
        )
    # The attribute companion of a utility ignition count.
    return httpx.Response(
        200,
        json={"data": [], "meta": {"total": 6, "returned": 0, "filters": {"utility": params.get("utility")}}},
    )


class _Model:
    """Routes with the scripted calls, then answers synthesis with ``brief``."""

    def __init__(self, calls: list[tuple[str, dict]] | None = None, brief: str | None = None) -> None:
        self.calls = list(calls or [])
        self.brief = brief
        self.synthesis_runs = 0

    async def complete(self, **kwargs):
        if kwargs.get("tools"):
            calls, self.calls = self.calls, []
            return ModelReply(
                content="" if calls else "I have what I need.",
                tool_calls=[
                    {"id": f"call_{i}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}
                    for i, (name, args) in enumerate(calls, start=1)
                ],
                raw={"choices": [{"finish_reason": "tool_calls" if calls else "stop"}]},
                latency_ms=1.0,
                usage={},
            )
        self.synthesis_runs += 1
        payload = json.loads(kwargs["messages"][-1]["content"].split("Evidence and caveats (JSON):\n", 1)[1])
        spatial = next(item for item in payload["evidence"] if "counts" in item["summary"])
        return ModelReply(
            content=json.dumps(
                {
                    "status": "answer",
                    "answer": self.brief,
                    "claims": [{"text": self.brief, "evidence_ids": [spatial["evidence_id"]]}],
                }
            ),
            tool_calls=[],
            raw={"choices": [{"finish_reason": "stop"}]},
            latency_ms=1.0,
            usage={},
        )


def _ask(question: str, model: _Model) -> dict:
    settings = AgentSettings(max_tool_steps=3)
    executor = ToolExecutor(settings, ArtifactStore(60), transport=httpx.MockTransport(_backend))

    async def run():
        try:
            return (await AgentOrchestrator(settings, model, executor).ask(question)).response
        finally:
            await executor.close()

    return asyncio.run(run())


def _spatial_evidence(response: dict) -> dict:
    return next(
        item["summary"]
        for item in response["evidence"]
        if item["tool"] == "data_query_spatial" and item["arguments"].get("utility")
    )


def _epss_card(response: dict) -> dict:
    return next(
        view["params"]
        for view in response["views"]
        if view["type"] == "stat_card" and view["params"]["source_dataset"] == "epss_outages"
    )


def _assert_epss_not_covered(response: dict, utility_label: str) -> None:
    text = response["answer_text"]
    summary = _spatial_evidence(response)
    # The count is gone from the evidence, with the reason in its place.
    assert summary["counts"]["epss_outages"] is None
    assert summary["not_covered"]["epss_outages"]["dataset"] == "epss_outages"
    # The answer names it as not covered, once, and never as a zero.
    assert "epss_outages=0" not in text
    assert text.count("EPSS is PG&E-only in this warehouse") == 1
    assert f"no {utility_label} rows in EPSS outages: that count would be absent, not zero" in text
    # The view says not covered instead of drawing a 0 card.
    card = _epss_card(response)
    assert card["value"] is None
    assert "absent, not zero" in card["not_covered_reason"]
    # Covered counts are untouched.
    assert summary["counts"]["ignitions"] == 7 and summary["counts"]["calfire_incidents"] == 3


def test_the_sce_territory_question_marks_epss_not_covered_in_text_and_views():
    assert route_question(SCE_QUESTION).rule == "spatial_utility_count"
    response = _ask(SCE_QUESTION, _Model())
    assert response["status"] == "answer", response["answer_text"]
    _assert_epss_not_covered(response, "SCE")
    assert "epss_outages=not covered" in response["answer_text"]
    ignitions = next(
        view["params"] for view in response["views"]
        if view["type"] == "stat_card" and view["params"]["source_dataset"] == "ignitions"
    )
    assert ignitions["value"] == 7


def test_the_sdge_model_path_variant_appends_the_note_to_a_synthesized_answer():
    assert route_question(SDGE_MODEL_QUESTION).path == "model"
    model = _Model(
        calls=[("data_query_spatial", {"kind": "summary", "utility": "SDGE",
                                       "start_date": "2024-01-01", "end_date": "2024-12-31"})],
        brief="SDG&E territory had 7 CPUC ignitions and 3 CAL FIRE incidents in 2024.",
    )
    response = _ask(SDGE_MODEL_QUESTION, model)
    assert model.synthesis_runs == 1
    assert response["status"] == "answer", response["answer_text"]
    assert response["answer_text"].startswith("SDG&E territory had 7 CPUC ignitions")
    _assert_epss_not_covered(response, "SDG&E")


def test_a_model_zero_for_the_uncovered_count_is_not_grounded():
    # The zero left the evidence, so a synthesis that states it fails grounding
    # and the answer falls back to the evidence, which says not covered.
    model = _Model(
        calls=[("data_query_spatial", {"kind": "summary", "utility": "SDGE",
                                       "start_date": "2024-01-01", "end_date": "2024-12-31"})],
        brief="SDG&E territory had 7 CPUC ignitions, 3 CAL FIRE incidents, and 0 EPSS outages in 2024.",
    )
    response = _ask(SDGE_MODEL_QUESTION, model)
    assert "0 EPSS outages" not in response["answer_text"]
    _assert_epss_not_covered(response, "SDG&E")


def test_the_uncovered_claim_check_names_numbers_for_that_dataset_only():
    from services.agent.orchestrator import _uncovered_count_claims

    args, summary = _summary_execution("SDGE")
    mark_uncovered_counts("data_query_spatial", args, summary)
    execution = ToolExecution(
        tool="data_query_spatial", arguments=args, ok=True, summary=summary,
        raw=None, error=None, artifact=None, latency_ms=0.0, evidence_id="ev",
    )
    for claim in ("0 EPSS outages", "0 EPSS fast-trip outages", "EPSS outages: 0", "epss_outages=0"):
        assert _uncovered_count_claims(f"In 2024 there were {claim}.", [execution]), claim
    for fine in (
        "SDG&E territory had 7 CPUC ignitions and 3 CAL FIRE incidents; EPSS outages are not covered.",
        "EPSS is PG&E-only, so SDG&E has no EPSS count for 2024.",
    ):
        assert _uncovered_count_claims(fine, [execution]) == [], fine


def _summary_execution(utility: str | None, region_kind: str = "utility") -> tuple[dict, dict]:
    args = {"kind": "summary", "start_date": "2024-01-01", "end_date": "2024-12-31"}
    if utility:
        args["utility"] = utility
    summary = {"kind": "summary", "region": {"kind": region_kind}, "counts": dict(TERRITORY_COUNTS)}
    return args, summary


def test_counts_are_marked_only_for_an_uncovered_named_utility():
    for utility in ("SCE", "SDGE", "PACIFICORP"):
        args, summary = _summary_execution(utility)
        mark_uncovered_counts("data_query_spatial", args, summary)
        assert summary["counts"] == {"ignitions": 7, "epss_outages": None, "calfire_incidents": 3}, utility
        assert set(summary["not_covered"]) == {"epss_outages"}
    # PG&E is covered, and a county or tier region names no utility.
    for utility in ("PGE", None):
        args, summary = _summary_execution(utility)
        mark_uncovered_counts("data_query_spatial", args, summary)
        assert summary["counts"] == TERRITORY_COUNTS and "not_covered" not in summary


def test_the_marking_follows_the_registry_not_a_utility_name(monkeypatch):
    # Give EPSS SCE rows in the registry: the SCE count is no longer marked.
    spec = DATASETS["epss_outages"]
    monkeypatch.setitem(DATASETS, "epss_outages", replace(spec, covered_utilities=("PGE", "SCE")))
    args, summary = _summary_execution("SCE")
    mark_uncovered_counts("data_query_spatial", args, summary)
    assert summary["counts"]["epss_outages"] == 0 and "not_covered" not in summary


def test_an_unknown_count_key_fails_loudly():
    args, summary = _summary_execution("SCE")
    summary["counts"]["tree_falls"] = 4
    with pytest.raises(ValueError, match="unknown dataset 'tree_falls'"):
        mark_uncovered_counts("data_query_spatial", args, summary)


def test_a_card_without_a_value_needs_a_reason_and_a_marked_count():
    base = dict(kind="spatial_metric", label="EPSS outages", scope="SCE territory",
                period="2024", source_dataset="epss_outages")
    with pytest.raises(ValueError):
        StatCardViewParams(**base, value=None)
    with pytest.raises(ValueError):
        StatCardViewParams(**base, value=0.0, not_covered_reason="not covered")
    with pytest.raises(ValueError):
        StatCardViewParams(**{**base, "kind": "risk"}, value=None, not_covered_reason="x")
    # A no-value card citing a result that did not mark that count is refused.
    execution = ToolExecution(
        tool="data_query_spatial",
        arguments={"kind": "summary", "utility": "PGE"},
        ok=True,
        summary={"kind": "summary", "counts": dict(TERRITORY_COUNTS)},
        raw=None, error=None, artifact=None, latency_ms=0.0, evidence_id="ev_pge",
    )
    card = ComponentSpec(
        type="stat_card",
        params=StatCardViewParams(**base, value=None, not_covered_reason="made up").model_dump(mode="json"),
        evidence_ids=["ev_pge"],
    )
    with pytest.raises(GroundingError):
        ground_views([card], [execution])


# ---------------------------------------------------------------------------
# 2. A month word counts only where a date can stand
# ---------------------------------------------------------------------------

ORDINARY_WORDS = [
    "Compare PG&E and SCE ignitions in 2023, which may be higher?",
    "PG&E vs SCE ignitions 2024, may I see both?",
    "compare SCE vs SDG&E ignitions in 2022 so I can march this to my boss",
]


@pytest.mark.parametrize("question", ORDINARY_WORDS)
def test_may_and_march_as_ordinary_words_are_not_months_and_the_comparison_answers(question):
    assert month_from_text(question) is None
    decision = route_question(question)
    assert decision.path == "deterministic", (decision.rule, decision.answer)
    assert decision.rule == "utility_comparison"
    assert decision.tool_calls and decision.tool_calls[0][0] == "comparison_run"


@pytest.mark.parametrize(
    "question,months",
    [
        ("How many CAL FIRE wildfire incidents were there in Sacramento County in August 2023?", [8]),
        ("how many wildfires in sacramento 2023 cpuc august", [8]),
        ("For Mendocino County on August 1, 2020, what is the highest ignition-risk value?", [8]),
        ("for july 14 2019, map the modeled ignition risk grid around Redding", [7]),
        ("Find all utility-caused ignitions reported after January 1, 2024.", [1]),
        ("How many EPSS outages did PG&E have in May 2023?", [5]),
        ("How many EPSS outages in May?", [5]),
        ("ignitions during the month of March 2022", [3]),
        ("PG&E outages for early June 2021", [6]),
        ("ignitions on 15 March 2021", [3]),
        ("outages Sept 2022", [9]),
        ("risk near Visalia on June 15, 2019 and June 15, 2021", [6, 6]),
    ],
)
def test_real_month_questions_still_name_their_month(question, months):
    assert [number for number, _word in months_from_text(question)] == months
    # The slot planner reads the same rule.
    assert _months_named(question.lower()) == set(months)


def test_a_month_before_a_comma_is_an_ordinary_word():
    assert month_from_text("How many ignitions in 2023? You may, if needed, split by utility.") is None


def test_real_month_questions_still_resolve_to_the_month():
    resolved = resolve_time("How many EPSS outages did PG&E have in May 2023?")
    assert (resolved.start_date, resolved.end_date) == ("2023-05-01", "2023-05-31")
    resolved = resolve_time("how many wildfires in sacramento 2023 cpuc august")
    assert (resolved.start_date, resolved.end_date) == ("2023-08-01", "2023-08-31")


# ---------------------------------------------------------------------------
# 3. One source for coverage
# ---------------------------------------------------------------------------


def test_comparison_metric_datasets_are_the_comparison_services_own():
    from services.agent.schemas import Metric
    from services.comparison import metrics, queries

    assert COMPARISON_METRIC_DATASETS is metrics.METRIC_DATASETS
    assert set(COMPARISON_METRIC_DATASETS) == metrics.METRICS
    assert set(typing.get_args(metrics.MetricName)) == metrics.METRICS
    assert set(typing.get_args(Metric)) == metrics.METRICS
    for metric, dataset in COMPARISON_METRIC_DATASETS.items():
        assert dataset in DATASETS, metric
        # Each metric other than the ratio has its own query; the ratio's
        # dataset is its numerator's.
        if not metric.endswith("_ratio"):
            assert callable(getattr(queries, metric, None)), metric
    assert COMPARISON_METRIC_DATASETS["epss_to_ignition_ratio"] == COMPARISON_METRIC_DATASETS["epss_outage_count"]


def test_utility_coverage_gap_raises_on_an_unknown_dataset():
    with pytest.raises(ValueError, match="unknown dataset"):
        utility_coverage_gap("epss_outage", ["SCE"])
    # No dataset or no utility is not a coverage question.
    assert utility_coverage_gap(None, ["SCE"]) is None
    assert utility_coverage_gap("epss_outages", []) is None


def test_every_dataset_with_limited_coverage_has_a_clarification_rule():
    limited = {key for key, spec in DATASETS.items() if spec.covered_utilities is not None}
    assert limited == set(NOT_COVERED_RULES)


def test_the_router_templates_and_planner_hardcode_no_utility_for_coverage():
    # Coverage comes from DatasetSpec.covered_utilities; a literal "PGE" in
    # these files would be a second source.
    for path in (
        "services/agent/routing.py",
        "services/agent/decisions/tool_pick_mode.py",
        "services/agent/eval/slot_plan.py",
    ):
        source = (ROOT / path).read_text(encoding="utf-8")
        assert not re.search(r"""["']PGE["']""", source), path


def test_coverage_checks_follow_the_registry(monkeypatch):
    question = "Compare SCE and SDG&E EPSS outages in 2022"
    assert route_question(question).rule == "epss_non_pge_utility"
    slots = route_question("How many EPSS outages did SCE have in 2022?").slots
    assert arguments_for_tool("data_query_records", slots, "") is None
    # Registry says EPSS covers SCE too: the router compares and the template runs.
    spec = DATASETS["epss_outages"]
    monkeypatch.setitem(DATASETS, "epss_outages", replace(spec, covered_utilities=("PGE", "SCE", "SDGE")))
    assert route_question(question).rule == "utility_comparison"
    assert arguments_for_tool("data_query_records", slots, "")["utility"] == "SCE"


def test_the_clarification_text_comes_from_the_registry(monkeypatch):
    spec = DATASETS["epss_outages"]
    monkeypatch.setitem(
        DATASETS,
        "epss_outages",
        replace(spec, not_covered_reason="Test reason", not_covered_alternatives=("calfire_incidents",)),
    )
    answer = route_question("Compare SCE and SDG&E EPSS outages in 2022").answer
    assert answer.startswith("Test reason, so there are no SCE and SDG&E rows in EPSS outages")
    assert "compare SCE and SDG&E's CAL FIRE incidents instead, or PG&E's EPSS outages" in answer
    assert "PSPS" not in answer


def test_the_registry_map_loads_from_either_import_order():
    # The registry reads the comparison map on first use, since the comparison
    # service imports the registry.
    assert dataset_registry.COMPARISON_METRIC_DATASETS["ignition_count"] == "cpuc_ignitions"
    with pytest.raises(AttributeError):
        dataset_registry.NOT_A_REGISTRY_NAME  # noqa: B018
