"""Coverage is measured from the data, never declared (PR #93 re-review).

The re-review found the registry's coverage written by hand and wrong: it said
CPUC, PSPS, and circuits cover every utility, though the warehouse has CPUC
rows only for PacifiCorp, PG&E, SCE, and SDG&E, PSPS rows only for PG&E, SCE,
SDG&E, and Liberty, and circuits only for PG&E; and it had no dates, so years
before a dataset starts (PSPS 2021-10-11, EPSS 2021-11-01) read as 0.

The invariant: the loaders measure which utilities each dataset has rows for
and each one's first and last date (shared/dataset_coverage.json); every path
reads that. A count for a dataset, utility, and period outside it is not
covered, with the reason, never 0; an alternative is offered only where
measured coverage includes that utility and period.

The reviewer's cases and paraphrases written for this change are below.
Comparison service values are fixture numbers, not warehouse figures.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import fields
from datetime import date
from pathlib import Path

import httpx
import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.decisions.tool_pick_mode import arguments_for_tool
from services.agent.eval.slot_plan import fallback_reason
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import ModelReply
from services.agent.routing import route_question
from services.agent.tools import ToolExecutor
from services.shared.dataset_registry import (
    COVERAGE_PATH,
    DATASET_COVERAGE,
    DATASETS,
    UTILITY_CODES,
    DatasetSpec,
    coverage_window,
    covered_utilities,
    dataset_coverage_gap,
    partial_coverage_note,
)

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# The measured file is the only source
# ---------------------------------------------------------------------------


def test_the_coverage_file_matches_the_loaded_warehouse():
    """Ground truth: re-measure the warehouse and compare with the committed file."""
    psycopg = pytest.importorskip("psycopg")
    from db.loaders.coverage import measure
    from shared.db import connect, get_settings

    try:
        conn = connect(get_settings())
    except psycopg.Error as exc:
        pytest.skip(f"warehouse not reachable: {exc}")
    try:
        measured = measure(conn)["datasets"]
    finally:
        conn.close()
    committed = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))["datasets"]
    assert measured == committed, "regenerate with: python -m db.loaders.coverage"


def test_the_measured_file_holds_what_the_reviewer_found():
    assert covered_utilities("cpuc_ignitions") == ["PACIFICORP", "PGE", "SCE", "SDGE"]
    assert covered_utilities("psps_events") == ["Liberty", "PGE", "SCE", "SDGE"]
    assert covered_utilities("circuits") == ["PGE"]
    assert covered_utilities("epss_outages") == ["PGE"]
    assert coverage_window("psps_events")[0] == date(2021, 10, 11)
    assert coverage_window("epss_outages", "PGE")[0] == date(2021, 11, 1)


def test_the_registry_declares_no_coverage():
    names = {item.name for item in fields(DatasetSpec)}
    assert "covered_utilities" not in names and "not_covered_reason" not in names
    source = (ROOT / "services/shared/dataset_registry.py").read_text(encoding="utf-8")
    assert "covered_utilities=" not in source


# Files that hold utility codes as naming data, not as coverage: the naming
# registry, and the service smoke scripts that pass a real utility filter.
NAMING_FILES = {"services/shared/naming.py"}
SERVICE_DIRS = ("agent", "comparison", "data_query", "visualization", "risk_forecasting", "shared")


def _service_sources():
    for directory in SERVICE_DIRS:
        for path in sorted((ROOT / "services" / directory).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in NAMING_FILES or path.name == "_smoke_test.py" or "/eval/_" in rel:
                continue
            yield rel, path.read_text(encoding="utf-8")


def test_no_service_hardcodes_a_utility_for_coverage():
    # A literal "PGE" or a "PG&E-only" sentence beside a dataset is a second,
    # hand-written coverage source.
    offenders = [
        rel
        for rel, source in _service_sources()
        if re.search(r"""["']PGE["']|PGE-only""", source)
    ]
    assert offenders == []


def test_the_website_hardcodes_no_utility_for_coverage():
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "website/src").rglob("*"))
        if path.suffix in {".ts", ".tsx"}
        and re.search(r"PG&E|PG&amp;E|['\"]PGE['\"]|'pge'", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    # The website reads the same measured file as the registry.
    source = (ROOT / "website/src/coverage.ts").read_text(encoding="utf-8")
    assert "../../shared/dataset_coverage.json" in source


# ---------------------------------------------------------------------------
# Offers follow measured coverage
# ---------------------------------------------------------------------------


def _years():
    return [(date(year, 1, 1), date(year, 12, 31)) for year in range(2012, 2028)]


def test_every_offer_is_covered_for_its_utility_and_period():
    checked = 0
    for dataset, entry in DATASET_COVERAGE.items():
        if not entry.get("utility_dimension"):
            continue
        for utility in UTILITY_CODES:
            for start, end in _years():
                gap = dataset_coverage_gap(dataset, [utility], start, end)
                if gap is None:
                    continue
                checked += 1
                for other in gap["alternatives"]:
                    assert dataset_coverage_gap(other, [utility], start, end) is None, (dataset, utility, start, other)
                for code in gap["covered_utilities"]:
                    assert dataset_coverage_gap(dataset, [code], start, end) is None, (dataset, code, start)
    assert checked > 100


# ---------------------------------------------------------------------------
# Reviewer case 1: Liberty CPUC ignitions 2023
# ---------------------------------------------------------------------------

LIBERTY_CPUC_2023 = [
    "How many Liberty CPUC ignitions were there in 2023?",
    "Number of Liberty ignitions in 2023?",
    "How many utility-caused ignitions did Liberty Utilities report in 2023?",
    "Liberty CPUC ignition count for 2023, please",
]


@pytest.mark.parametrize("question", LIBERTY_CPUC_2023)
def test_liberty_cpuc_2023_is_not_covered_and_offers_only_covered_data(question):
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("clarification", "dataset_not_covered"), decision.answer
    assert decision.tool_calls == []
    answer = decision.answer
    assert "CPUC ignitions have rows only for PacifiCorp, PG&E, SCE, and SDG&E" in answer
    assert "absent, not zero" in answer
    # Liberty has CAL FIRE rows in 2023; its PSPS rows start 2024-11-11.
    assert "Liberty's CAL FIRE incidents in 2023" in answer
    assert "PSPS" not in answer


def test_liberty_cpuc_2023_template_and_slot_plan_refuse():
    slots = {"dataset": "cpuc_ignitions", "utilities": ["Liberty"], "year": 2023}
    assert arguments_for_tool("data_query_records", slots, LIBERTY_CPUC_2023[0]) is None
    assert fallback_reason("How many CPUC ignitions did Liberty and PG&E have in 2023?") != "no calls"


# ---------------------------------------------------------------------------
# Reviewer case 2: Bear Valley PSPS and CPUC
# ---------------------------------------------------------------------------

BEAR_VALLEY = [
    ("How many PSPS events did Bear Valley have?", "PSPS events"),
    ("How many CPUC ignitions did Bear Valley have?", "CPUC ignitions"),
    ("How many PSPS events did Bear Valley Electric have in 2023?", "PSPS events"),
    ("Count Bear Valley's CPUC-reported ignitions in 2022", "CPUC ignitions"),
    ("Did Bear Valley have any PSPS shutoffs in 2024? How many?", "PSPS events"),
]


@pytest.mark.parametrize("question,label", BEAR_VALLEY)
def test_bear_valley_psps_and_cpuc_are_not_covered(question, label):
    decision = route_question(question)
    # With no year, asking for one cannot help: Bear Valley has no rows at all.
    assert (decision.path, decision.rule) == ("clarification", "dataset_not_covered"), decision.answer
    assert f"no Bear Valley rows in {label}" in decision.answer
    assert "Bear Valley's CAL FIRE incidents" in decision.answer


# ---------------------------------------------------------------------------
# Executor and answer text, with fixture services
# ---------------------------------------------------------------------------


class _RecordingBackend:
    def __init__(self, handler=None) -> None:
        self.requests: list[httpx.Request] = []
        self.handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is None:
            raise AssertionError(f"an uncovered read reached the service: {request.url}")
        return self.handler(request)


class _ScriptedModel:
    """Makes the given tool calls once, then stops; synthesis must not run."""

    def __init__(self, calls: list[tuple[str, dict]]) -> None:
        self.calls = calls

    async def complete(self, **kwargs):
        if kwargs.get("tools"):
            calls, self.calls = self.calls, []
            return ModelReply(
                content="",
                tool_calls=[
                    {"id": f"call_{i}", "type": "function",
                     "function": {"name": name, "arguments": json.dumps(args)}}
                    for i, (name, args) in enumerate(calls, start=1)
                ],
                raw={"choices": [{"finish_reason": "tool_calls"}]},
                latency_ms=1.0,
                usage={},
            )
        raise AssertionError("synthesis must not run after a not-covered read")


def _ask(question: str, backend: _RecordingBackend, model=None) -> dict:
    settings = AgentSettings(max_tool_steps=3)
    executor = ToolExecutor(settings, ArtifactStore(60), transport=httpx.MockTransport(backend))

    async def run():
        try:
            orchestrator = AgentOrchestrator(settings, model or _ScriptedModel([]), executor)
            return (await orchestrator.ask(question)).response
        finally:
            await executor.close()

    return asyncio.run(run())


BEAR_VALLEY_MODEL = "Bear Valley PSPS events and CPUC ignitions in 2024"


def test_bear_valley_on_the_model_path_reaches_no_service_and_reports_no_zero():
    assert route_question(BEAR_VALLEY_MODEL).path == "model"
    backend = _RecordingBackend()
    model = _ScriptedModel([
        ("data_query_records", {"dataset": "psps_events", "result_mode": "count", "utility": "BVES", "year": 2024}),
        ("data_query_records", {"dataset": "cpuc_ignitions", "result_mode": "count", "utility": "BVES", "year": 2024}),
    ])
    response = _ask(BEAR_VALLEY_MODEL, backend, model)
    text = response["answer_text"]
    assert backend.requests == []
    assert response["status"] == "clarification", text
    assert "no Bear Valley rows in PSPS events in 2024" in text
    assert " 0 " not in f" {text} "


# Reviewer case 3: PacifiCorp vs PG&E PSPS 2019. The router has no PSPS
# comparison metric, so these reach the model, and the executor refuses.
PACIFICORP_PGE_PSPS_2019 = [
    "Compare PacifiCorp vs PG&E PSPS events in 2019",
    "PacifiCorp and PG&E PSPS event counts for 2019, side by side",
]
PSPS_2019_CALL = (
    "comparison_run",
    {"kind": "utilities", "utilities": ["PACIFICORP", "PGE"], "metric": "psps_event_count",
     "start_date": "2019-01-01", "end_date": "2019-12-31"},
)


@pytest.mark.parametrize("question", PACIFICORP_PGE_PSPS_2019)
def test_pacificorp_vs_pge_psps_2019_clarifies_and_offers_only_cal_fire(question):
    assert route_question(question).path == "model"
    backend = _RecordingBackend()
    response = _ask(question, backend, _ScriptedModel([PSPS_2019_CALL]))
    text = response["answer_text"]
    assert backend.requests == []
    assert response["status"] == "clarification", text
    assert "PSPS events for PG&E start on 2021-10-11" in text
    assert "PSPS events have rows only for Liberty, PG&E, SCE, and SDG&E" in text
    # CPUC rows start 2020 (PG&E) and 2025 (PacifiCorp); EPSS 2021. Only CAL
    # FIRE has both in 2019.
    assert "CAL FIRE incidents" in text
    assert "CPUC ignitions" not in text and "EPSS" not in text


@pytest.mark.parametrize(
    "question",
    ["How many PSPS events did PacifiCorp have in 2019?", "How many PSPS events did PG&E have in 2019?"],
)
def test_each_side_of_the_psps_2019_comparison_is_not_covered_alone(question):
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("clarification", "dataset_not_covered")
    assert "in PSPS events in 2019" in decision.answer


# Reviewer case 4: PG&E EPSS 2020 vs 2021. The comparison runs; 2020 is before
# EPSS starts, so it is null with its reason, and 2021 counts only from
# 2021-11-01, which the answer says.
PGE_EPSS_2020_2021 = [
    "Compare PG&E EPSS outages 2020 vs 2021",
    "PG&E EPSS fast-trip outages: 2020 versus 2021",
    "compare pge epss outages in 2020 and 2021",
]


def _stale_comparison_service(request: httpx.Request) -> httpx.Response:
    # A comparison service that still counts 2020 as 0 (the defect): the
    # agent must not show that zero.
    return httpx.Response(
        200,
        json={
            "metric": "epss_outage_count",
            "normalize": "none",
            "scope_type": "utility",
            "scope": "PGE",
            "period_a": {"key": "period_a", "value": 0, "raw_value": 0, "reason": None,
                         "start_date": "2020-01-01", "end_date": "2020-12-31"},
            "period_b": {"key": "period_b", "value": 150, "raw_value": 150, "reason": None,
                         "start_date": "2021-01-01", "end_date": "2021-12-31"},
            "delta": {"value": 150.0, "reason": None},
            "meta": {},
        },
    )


@pytest.mark.parametrize("question", PGE_EPSS_2020_2021)
def test_pge_epss_2020_vs_2021_nulls_2020_and_notes_the_2021_window(question):
    decision = route_question(question)
    assert (decision.path, decision.rule) == ("deterministic", "period_comparison")
    backend = _RecordingBackend(_stale_comparison_service)
    response = _ask(question, backend)
    text = response["answer_text"]
    assert response["status"] == "answer", text
    assert "EPSS outages for PG&E start on 2021-11-01" in text
    assert "150 in 2021" in text
    assert "covers only 2021-11-01 to 2021-12-31" in text
    assert "0 in 2020" not in text and "change of" not in text
    evidence = next(item for item in response["evidence"] if item["tool"] == "comparison_run")
    assert evidence["summary"]["period_a"]["value"] is None
    assert evidence["summary"]["delta"]["value"] is None


def test_a_records_count_over_a_partly_covered_range_says_which_part_counts():
    def records(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [], "meta": {"total": 150, "returned": 0, "filters": {}}})

    question = "How did PG&E's EPSS outage count change from 2020 to 2021?"
    response = _ask(question, _RecordingBackend(records))
    text = response["answer_text"]
    assert "EPSS outages for PG&E cover 2021-11-01" in text
    assert "covers only 2021-11-01 to 2021-12-31" in text


def test_a_period_before_a_dataset_starts_is_not_covered_without_a_utility():
    decision = route_question("How many CPUC ignitions were there in 2019?")
    assert (decision.path, decision.rule) == ("clarification", "dataset_not_covered")
    assert "CPUC ignitions start on 2020-01-01" in decision.answer


# ---------------------------------------------------------------------------
# Comparison service: null with a reason, never 0, and the service never
# queries outside measured coverage.
# ---------------------------------------------------------------------------


class _NoQueryConn:
    def cursor(self, *args, **kwargs):
        raise AssertionError("an uncovered comparison value must not be queried")


@pytest.mark.parametrize(
    "metric,scope_id,start,end,reason",
    [
        ("ignition_count", "Liberty", "2023-01-01", "2023-12-31", "CPUC ignitions have rows only for"),
        ("ignition_count", "BVES", "2023-01-01", "2023-12-31", "CPUC ignitions have rows only for"),
        ("psps_event_count", "BVES", "2023-01-01", "2023-12-31", "PSPS events have rows only for"),
        ("psps_event_count", "PACIFICORP", "2019-01-01", "2019-12-31", "PSPS events have rows only for"),
        ("psps_event_count", "PGE", "2019-01-01", "2019-12-31", "PSPS events for PG&E start on 2021-10-11"),
        ("epss_outage_count", "PGE", "2020-01-01", "2020-12-31", "EPSS outages for PG&E start on 2021-11-01"),
        ("customers_deenergized", "PGE", "2019-01-01", "2019-12-31", "PSPS events for PG&E start on 2021-10-11"),
        ("epss_to_ignition_ratio", "PGE", "2020-01-01", "2020-12-31", "EPSS outages for PG&E start on 2021-11-01"),
    ],
)
def test_the_comparison_service_returns_null_with_a_reason(metric, scope_id, start, end, reason):
    from services.comparison import queries

    value, why = queries.raw_metric(
        _NoQueryConn(),
        metric,
        scope="utility",
        scope_id=scope_id,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        ignition_definition="attribute",
    )
    assert value is None
    assert why.startswith(reason), why


def test_the_comparison_service_nulls_an_hftd_period_before_the_dataset_starts():
    from services.comparison import queries

    value, why = queries.raw_metric(
        _NoQueryConn(), "psps_event_count", scope="hftd", scope_id="Tier 2",
        start=date(2019, 1, 1), end=date(2019, 12, 31), ignition_definition="spatial",
    )
    assert value is None and why == "PSPS events start on 2021-10-11"


class _CountConn:
    """Answers every count query with 150."""

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return (150,)

    def cursor(self, *args, **kwargs):
        return self._Cursor()


def test_the_comparison_service_notes_a_partly_covered_period():
    from services.comparison.app import _metric_for_scope

    row = _metric_for_scope(
        _CountConn(), metric="epss_outage_count", scope="utility", scope_id="PGE",
        start=date(2021, 1, 1), end=date(2021, 12, 31), normalize="none",
        ignition_definition="attribute",
    )
    assert row["value"] == 150
    assert "covers only 2021-11-01 to 2021-12-31" in row["coverage_note"]


def test_the_circuit_denominator_follows_the_measured_inventory():
    from services.comparison import queries

    assert queries.circuit_count_utility_attribute(_NoQueryConn(), "SCE") is None
    assert queries.circuit_count_utility_attribute(_CountConn(), "PGE") == 150


# ---------------------------------------------------------------------------
# data_query and visualization read measured coverage
# ---------------------------------------------------------------------------


def test_grouped_utility_rows_before_coverage_are_null_with_a_reason():
    from services.data_query.queries import _pad_utility_rows

    rows = _pad_utility_rows(
        {}, dataset="cpuc_ignitions", utility_filter=None,
        start_date=date(2019, 1, 1), end_date=date(2019, 12, 31),
    )
    assert rows and all(row["value"] is None and row["reason"] for row in rows)
    rows = _pad_utility_rows(
        {}, dataset="psps_events", utility_filter="Liberty",
        start_date=date(2023, 1, 1), end_date=date(2023, 12, 31),
    )
    assert rows == [{"key": "Liberty", "value": None, "reason": "PSPS events for Liberty start on 2024-11-11"}]
    # Inside coverage, an empty group is a real zero.
    rows = _pad_utility_rows(
        {}, dataset="cpuc_ignitions", utility_filter="SCE",
        start_date=date(2023, 1, 1), end_date=date(2023, 12, 31),
    )
    assert rows == [{"key": "SCE", "value": 0}]


def test_an_epss_utility_filter_matches_nothing_outside_measured_coverage():
    from services.data_query.queries import _epss_rank_empty

    assert _epss_rank_empty("SCE").startswith("EPSS outages have rows only for PG&E")
    assert _epss_rank_empty("PGE") is None
    assert _epss_rank_empty(None) is None


def test_partial_note_is_none_inside_coverage():
    assert partial_coverage_note("epss_outages", "PGE", "2022-01-01", "2022-12-31") is None
    assert partial_coverage_note("cpuc_ignitions", None, "2019-06-01", "2020-06-30").startswith(
        "CPUC ignitions cover 2020-01-01"
    )
    assert "circuits" in DATASETS
