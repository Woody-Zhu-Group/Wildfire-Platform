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
from typing import Any

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
    dataset_years,
    default_definition,
    definition_for_filter,
    partial_coverage_note,
    rows_in_period,
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
    # Nothing is offered: the CAL FIRE count reads its default query, which
    # has no Liberty rows in 2023, and Liberty's PSPS rows start 2024-11-11.
    assert not rows_in_period("calfire_incidents", "Liberty", "2023-01-01", "2023-12-31")
    assert "CAL FIRE" not in answer and "PSPS" not in answer
    assert "Do you want" not in answer


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
    # Bear Valley's CAL FIRE incidents are offered only where the count the
    # offer leads to (CAL FIRE's default query) has Bear Valley rows in the
    # asked period, as measured.
    year = decision.slots.get("year")
    period = (f"{year}-01-01", f"{year}-12-31") if year else (None, None)
    offered = "Bear Valley's CAL FIRE incidents" in decision.answer
    assert offered == rows_in_period("calfire_incidents", "BVES", *period), decision.answer


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



# ---------------------------------------------------------------------------
# Final review: offers need measured rows in the asked period, not a window
# that overlaps it. SDG&E's PSPS window spans 2022, but SDG&E has no PSPS rows
# in 2022 or 2023. The generated file holds rows per calendar year for each
# dataset, utility, and the untagged rows, per query definition, and every
# offer is checked against them.
# ---------------------------------------------------------------------------


def _year_rows(
    dataset: str, utility: str | None, year: int, definition: str | None = None
) -> int:
    """Measured rows in a year, for the query definition (None: the default)."""
    entry = DATASET_COVERAGE[dataset]
    if definition not in (None, entry.get("definition")):
        entry = entry["definitions"][definition]
    if utility is None:
        span = entry
    elif utility == "untagged":
        span = entry.get("untagged") or {}
    else:
        span = (entry.get("utilities") or {}).get(utility) or {}
    return int((span.get("years") or {}).get(str(year), 0))


def test_the_year_counts_add_up_to_the_measured_rows():
    for dataset, entry in DATASET_COVERAGE.items():
        years = entry.get("years") or {}
        if entry.get("date_column") is None:
            assert years == {}, dataset
            continue
        # Rows with no date are counted in rows but in no year.
        assert 0 < sum(years.values()) <= entry["rows"], dataset
        if not entry.get("utility_dimension") or len(entry.get("utilities") or {}) == 1:
            continue
        for year, rows in years.items():
            parts = sum(
                (span.get("years") or {}).get(year, 0) for span in entry["utilities"].values()
            ) + ((entry.get("untagged") or {}).get("years") or {}).get(year, 0)
            assert parts == rows, (dataset, year)


def test_every_offer_has_measured_rows_in_the_asked_period():
    checked = 0
    for dataset, entry in DATASET_COVERAGE.items():
        if not entry.get("utility_dimension"):
            continue
        for utility in UTILITY_CODES:
            for start, end in _years():
                gap = dataset_coverage_gap(dataset, [utility], start, end)
                if gap is None:
                    continue
                for other in gap["alternatives"]:
                    checked += 1
                    assert _year_rows(other, utility, start.year) > 0, (dataset, utility, start.year, other)
                for code in gap["covered_utilities"]:
                    checked += 1
                    assert _year_rows(dataset, code, start.year) > 0, (dataset, code, start.year)
    assert checked > 50


@pytest.mark.parametrize(
    "dataset,utility,year,offered",
    [
        # EPSS for SDG&E: CPUC has SDG&E rows in 2022 and 2023; PSPS has none.
        ("epss_outages", "SDGE", 2022, ["cpuc_ignitions"]),
        ("epss_outages", "SDGE", 2023, ["cpuc_ignitions"]),
        # CPUC for Bear Valley in 2020 and Liberty in 2022 and 2023: no
        # dataset has their rows in that year, so nothing is offered.
        ("cpuc_ignitions", "BVES", 2020, []),
        ("cpuc_ignitions", "Liberty", 2022, []),
        ("cpuc_ignitions", "Liberty", 2023, []),
    ],
)
def test_an_overlapping_window_without_rows_is_never_offered(dataset, utility, year, offered):
    start, end = date(year, 1, 1), date(year, 12, 31)
    gap = dataset_coverage_gap(dataset, [utility], start, end)
    assert gap["alternatives"] == offered
    for other in DATASETS[dataset].not_covered_alternatives:
        if other not in offered:
            assert not rows_in_period(other, utility, start, end), other


@pytest.mark.parametrize(
    "dataset,utility,year",
    [("psps_events", "SDGE", 2022), ("psps_events", "SDGE", 2023)],
)
def test_the_reviewer_pairs_overlap_the_window_but_have_no_rows(dataset, utility, year):
    start, end = date(year, 1, 1), date(year, 12, 31)
    first, last = coverage_window(dataset, utility)
    assert first <= end and last >= start
    assert _year_rows(dataset, utility, year) == 0
    assert not rows_in_period(dataset, utility, start, end)


def test_every_window_year_without_rows_has_no_rows_in_period_for_every_definition():
    # The CAL FIRE reviewer pairs (Bear Valley 2020, Liberty 2022) as a rule:
    # any year inside a utility's window with no measured rows, in any query
    # definition, is never counted as having rows.
    checked = 0
    for dataset, spec in DATASETS.items():
        entry = DATASET_COVERAGE.get(dataset)
        if not entry or not entry.get("utility_dimension"):
            continue
        for definition in list(spec.query_definitions) or [None]:
            for utility in covered_utilities(dataset, definition=definition):
                window = coverage_window(dataset, utility, definition=definition)
                if window is None:  # no date column (circuits): no years
                    continue
                first, last = window
                for year in range(first.year, last.year + 1):
                    if _year_rows(dataset, utility, year, definition) == 0:
                        checked += 1
                        assert not rows_in_period(
                            dataset, utility, f"{year}-01-01", f"{year}-12-31", definition=definition
                        ), (dataset, definition, utility, year)
    assert checked > 10


def test_rows_in_period_needs_a_whole_year_or_a_measured_row_date():
    # SDG&E PSPS rows: 2021 (first 2021-11-24), 2024, 2025 (last 2025-01-20).
    assert rows_in_period("psps_events", "SDGE", "2024-01-01", "2024-12-31")
    assert rows_in_period("psps_events", "SDGE", "2021-11-01", "2021-11-30")
    assert rows_in_period("psps_events", "SDGE", "2025-01-01", "2025-01-31")
    assert not rows_in_period("psps_events", "SDGE", "2022-01-01", "2023-12-31")
    # Part of a year with rows, holding neither measured date: not known.
    assert not rows_in_period("psps_events", "SDGE", "2024-03-01", "2024-03-31")
    # Untagged rows are measured too, on the rows the default count reads.
    year = dataset_years("calfire_incidents", "untagged")[0]
    assert rows_in_period("calfire_incidents", "untagged", f"{year}-01-01", f"{year}-12-31")
    assert not rows_in_period("cpuc_ignitions", "untagged", "2023-01-01", "2023-12-31")


def _clarification_on_any_path(question: str, call: tuple[str, dict]) -> tuple[str, str]:
    """The rule and text of the clarification, whichever path the question takes.

    A question the router reads is clarified there; one it leaves to the model
    reaches the executor with the model's call, which must clarify the same way.
    """
    decision = route_question(question)
    if decision.path != "model":
        return decision.rule, decision.answer or ""
    backend = _RecordingBackend()
    response = _ask(question, backend, _ScriptedModel([call]))
    assert backend.requests == []
    assert response["status"] == "clarification", response["answer_text"]
    return "executor", response["answer_text"]


def _count(dataset: str, utility: str, year: int) -> tuple[str, dict]:
    return ("data_query_records",
            {"dataset": dataset, "result_mode": "count", "utility": utility, "year": year})


U8 = [
    "Which circuits had the most EPSS outages for San Diego Gas & Electric in 2023?",
    "Top EPSS circuits for SDG&E in 2023",
    "How many EPSS outages did SDG&E have in 2023?",
    "SDG&E EPSS shutoff count, 2023",
]


@pytest.mark.parametrize("question", U8)
def test_sdge_epss_2023_offers_only_data_with_sdge_rows_in_2023(question):
    rule, answer = _clarification_on_any_path(question, _count("epss_outages", "SDGE", 2023))
    assert rule in {"epss_non_pge_utility", "executor"}, answer
    assert "CPUC ignitions have records for SDG&E in 2023" in answer
    assert "EPSS outages have records for PG&E in 2023" in answer
    assert "PSPS" not in answer and "does exist" not in answer


NO_OFFER = [
    ("How many CPUC ignitions did Bear Valley have in 2020?", "Bear Valley"),
    ("Bear Valley Electric CPUC-reported ignitions, 2020", "Bear Valley"),
    ("Count BVES utility ignitions for 2020", "Bear Valley"),
    ("What was Bear Valley's CPUC ignition count in 2020?", "Bear Valley"),
    ("How many CPUC ignitions did Liberty report in 2022?", "Liberty"),
    ("Liberty Utilities CPUC ignitions in 2022", "Liberty"),
    ("Number of utility-caused ignitions for Liberty in 2022?", "Liberty"),
    ("Count Liberty's CPUC-reported ignitions for 2022", "Liberty"),
]


@pytest.mark.parametrize("question,name", NO_OFFER)
def test_cpuc_for_a_utility_with_no_rows_that_year_anywhere_offers_nothing(question, name):
    code = "BVES" if name == "Bear Valley" else name
    year = int(re.search(r"20\d\d", question).group(0))
    rule, answer = _clarification_on_any_path(question, _count("cpuc_ignitions", code, year))
    assert rule in {"dataset_not_covered", "executor"}, answer
    assert f"no {name} rows in CPUC ignitions" in answer
    assert "Do you want" not in answer and "CAL FIRE" not in answer


# ---------------------------------------------------------------------------
# Final check: coverage is measured on exactly the rows each count reads.
#
# The final check found coverage measured on every CAL FIRE row while the
# default CAL FIRE count reads only the registry's default incident types, so
# years whose rows the default excludes (2013; Bear Valley's and some of
# Liberty's years, offered as alternatives) were measured as covered and then
# answered 0. The loader now measures each query definition the registry
# gives a dataset (DatasetSpec.query_definitions), and every coverage lookup
# uses the definition its call reads. The expectations below are read from the
# measured file per definition, never from a list of incident types: the
# default definition may change.
# ---------------------------------------------------------------------------

CALFIRE = "calfire_incidents"


_DATA_QUERY: list[Any] = []


def _real_data_query() -> Any:
    """The data_query app on the warehouse, or None when the warehouse is not reachable."""
    if not _DATA_QUERY:
        client = None
        try:
            import psycopg
            from fastapi.testclient import TestClient

            from services.data_query.app import app
            from shared.db import connect, get_settings

            connect(get_settings()).close()
            client = TestClient(app)
        except (ImportError, psycopg.Error):
            client = None
        _DATA_QUERY.append(client)
    return _DATA_QUERY[0]


def _calfire_service(requests: list[dict]) -> Any:
    """data_query for the reviewer cases: the real service when the warehouse is up.

    With the warehouse, the returned value is data_query's own count, so an
    answer only matches the measured rows when coverage and the count read the
    same rows. Without it, a fixture returns the measured count of the rows the
    request reads (its incident_type picks the definition exactly as
    data_query does), and test_each_definitions_measured_rows_are_what_data_query_counts
    ties the measured file to data_query where a warehouse exists.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/calfire/incidents"), request.url
        params = dict(request.url.params)
        requests.append(params)
        real = _real_data_query()
        if real is not None:
            response = real.get(request.url.path, params=params)
            return httpx.Response(response.status_code, json=response.json())
        definition = definition_for_filter(CALFIRE, params.get("incident_type"))
        entry = DATASET_COVERAGE[CALFIRE]
        if definition != entry["definition"]:
            entry = entry["definitions"][definition]
        if "year" in params:
            total = _year_rows(CALFIRE, params.get("utility"), int(params["year"]), definition)
        else:
            total = entry["rows"]
        meta = {
            "total": total,
            "returned": 0,
            "filters": params,
            "null_incident_type_count": DATASET_COVERAGE[CALFIRE]["definitions"]["untyped"]["rows"],
            "null_utility_records_in_table": (entry.get("untagged") or {}).get("rows", 0),
        }
        return httpx.Response(200, json={"data": [], "meta": meta})

    return handle


def _count_call(year: int, utility: str | None = None, definition: str | None = None) -> tuple[str, dict]:
    args: dict[str, Any] = {"dataset": CALFIRE, "result_mode": "count", "year": year}
    if utility:
        args["utility"] = utility
    if definition:
        args["incident_type_mode"] = definition
    return ("data_query_records", args)


def _assert_count_or_not_covered(
    question: str, year: int, utility: str | None, definition: str | None
) -> dict:
    """Ask the question end to end and check the returned value against measured rows.

    Where the definition's measured rows hold none for the utility in the
    year, the answer is the not-covered clarification and no service is
    called. Otherwise the count the service returns is in the answer.
    """
    rows = _year_rows(CALFIRE, utility, year, definition)
    requests: list[dict] = []
    backend = _RecordingBackend(_calfire_service(requests))
    response = _ask(question, backend, _ScriptedModel([_count_call(year, utility, definition)]))
    text = response["answer_text"]
    covered = dataset_coverage_gap(
        CALFIRE, [utility] if utility else [], f"{year}-01-01", f"{year}-12-31", definition=definition
    ) is None
    if not covered:
        assert response["status"] == "clarification", text
        assert backend.requests == [], text
        assert "absent, not zero" in text and " 0 " not in f" {text} "
        return response
    assert response["status"] == "answer", text
    counted = [params for params in requests if params.get("year") == str(year)]
    assert counted, requests
    wanted = definition or default_definition(CALFIRE)
    for params in counted:
        assert definition_for_filter(CALFIRE, params.get("incident_type")) == wanted, params
    assert re.search(rf"(?<![\d,]){rows:,}(?![\d,])", text), (rows, text)
    return response


CAL_FIRE_2013 = [
    "How many CAL FIRE incidents were there in 2013?",
    "CAL FIRE wildfire count for 2013",
    "Number of CAL FIRE fires in 2013?",
    "How many wildfires did CAL FIRE record in 2013?",
]


@pytest.mark.parametrize("question", CAL_FIRE_2013)
def test_cal_fire_2013_is_answered_from_the_rows_the_default_count_reads(question):
    # The default count's rows, as measured: the not-covered result when it
    # has none in 2013, the returned count otherwise. Never 0 for rows the
    # default count does not read.
    response = _assert_count_or_not_covered(question, 2013, None, None)
    if _year_rows(CALFIRE, None, 2013) == 0:
        years = dataset_years(CALFIRE)
        before = max(year for year in years if year < 2013)
        after = min(year for year in years if year > 2013)
        assert f"have no rows between {before} and {after}" in response["answer_text"]
        # Other definitions have 2013 rows, so the reason names the default's.
        assert _year_rows(CALFIRE, None, 2013, "all") > 0
        assert DATASETS[CALFIRE].definition_words[default_definition(CALFIRE)] in (
            response["answer_text"]
        )


BEAR_VALLEY_CAL_FIRE = [
    ("How many CAL FIRE incidents did Bear Valley have in 2013?", 2013),
    ("Bear Valley Electric CAL FIRE incidents, 2015", 2015),
    ("Count BVES CAL FIRE incidents for 2013", 2013),
    ("How many CAL FIRE fires were tagged to Bear Valley in 2015?", 2015),
]


@pytest.mark.parametrize("question,year", BEAR_VALLEY_CAL_FIRE)
def test_bear_valley_cal_fire_years_are_measured_on_the_default_count(question, year):
    # Bear Valley's CAL FIRE rows under every incident type fall in 2013 and
    # 2015; whether the default count reads any is the measured file's answer.
    assert _year_rows(CALFIRE, "BVES", year, "all") > 0
    _assert_count_or_not_covered(question, year, "BVES", None)


LIBERTY_OFFERS = [
    ("How many CPUC ignitions did Liberty have in 2015?", 2015),
    ("Liberty Utilities CPUC ignitions in 2016", 2016),
    ("Count Liberty's CPUC-reported ignitions for 2017", 2017),
    ("How many CPUC ignitions did Liberty report in 2020?", 2020),
    ("Number of utility-caused ignitions for Liberty in 2014?", 2014),
]


@pytest.mark.parametrize("question,year", LIBERTY_OFFERS)
def test_liberty_cal_fire_is_offered_only_where_the_default_count_has_rows(question, year):
    # CPUC has no Liberty rows, so the clarification may offer Liberty's CAL
    # FIRE incidents: only in a year the offered count (CAL FIRE's default
    # query) has Liberty rows, and following the offer returns those rows.
    rule, answer = _clarification_on_any_path(question, _count("cpuc_ignitions", "Liberty", year))
    assert rule in {"dataset_not_covered", "executor"}, answer
    offered = "Liberty's CAL FIRE incidents" in answer
    assert offered == (_year_rows(CALFIRE, "Liberty", year) > 0), answer
    if offered:
        _assert_count_or_not_covered(
            f"How many CAL FIRE incidents did Liberty have in {year}?", year, "Liberty", None
        )


DEFINITION_2013 = [
    ("How many CAL FIRE incidents of all incident types were there in 2013?", "all"),
    ("How many CAL FIRE records, including non-wildfire, were there in 2013?", "all"),
    ("CAL FIRE incident count for 2013 regardless of incident type", "all"),
    ("How many untyped CAL FIRE incidents were there in 2013?", "untyped"),
    ("How many CAL FIRE incidents without an incident type were there in 2013?", "untyped"),
]


@pytest.mark.parametrize("question,definition", DEFINITION_2013)
def test_a_call_with_another_definition_uses_that_definitions_coverage(question, definition):
    # incident_type_mode all or untyped reads other rows than the default, so
    # its coverage is its own: 2013 has rows there, and the count is returned.
    assert _year_rows(CALFIRE, None, 2013, definition) > 0
    _assert_count_or_not_covered(question, 2013, None, definition)
    gap = dataset_coverage_gap(CALFIRE, [], "2013-01-01", "2013-12-31", definition=definition)
    assert gap is None


def test_a_comparison_cannot_carry_another_definition_so_it_clarifies():
    # comparison_run reads the default query only; answering would drop the
    # asked incident types.
    decision = route_question("Compare all CAL FIRE incident types for PG&E vs SCE in 2019")
    assert (decision.path, decision.rule) == ("clarification", "unexpressed_filter_constraints")
    assert "incident type" in decision.answer


def test_every_definition_is_measured_from_the_registry():
    # The measured file names each dataset's definitions exactly as the
    # registry gives them, the default first; nothing else is measured.
    for key, spec in DATASETS.items():
        entry = DATASET_COVERAGE.get(key)
        if entry is None:
            continue
        definitions = dict(spec.query_definitions)
        if not definitions:
            assert "definition" not in entry and "definitions" not in entry, key
            continue
        default, *others = definitions
        assert (entry["definition"], entry["where"]) == (default, definitions[default]), key
        assert {name: other["where"] for name, other in entry["definitions"].items()} == {
            name: definitions[name] for name in others
        }, key


def test_a_file_measured_for_other_definitions_fails_loudly():
    from services.shared import dataset_registry as registry

    stale = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))["datasets"]
    assert registry._stale_definitions(stale) is None
    stale[CALFIRE]["where"] = "incident_type IN ('Something else')"
    message = registry._stale_definitions(stale)
    assert message and "python -m db.loaders.coverage" in message
    unavailable = registry._CoverageUnavailable(message)
    with pytest.raises(RuntimeError, match="db.loaders.coverage"):
        unavailable.get(CALFIRE)


class _SqlRecorder:
    """A connection whose cursor records SQL and reports no rows."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, *_params):
        self.sql.append(" ".join(str(sql).split()))

    def fetchone(self):
        return (None, None, 0)

    def fetchall(self):
        return []


def test_the_loader_measures_the_registry_definitions_not_its_own(monkeypatch):
    # Swap the registry's CAL FIRE definitions: the loader must measure the
    # swapped predicates, so it holds no incident types of its own.
    from dataclasses import replace

    from db.loaders import coverage as loader

    swapped = {"first_rows": "incident_type = 'Swapped'", "every_row": None}
    monkeypatch.setitem(
        DATASETS, CALFIRE, replace(DATASETS[CALFIRE], query_definitions=swapped)
    )
    conn = _SqlRecorder()
    measured = loader.measure(conn)["datasets"][CALFIRE]
    assert (measured["definition"], measured["where"]) == ("first_rows", swapped["first_rows"])
    assert list(measured["definitions"]) == ["every_row"]
    calfire_sql = [sql for sql in conn.sql if "calfire_incidents" in sql]
    assert any("incident_type = 'Swapped'" in sql for sql in calfire_sql)
    source = (ROOT / "db/loaders/coverage.py").read_text(encoding="utf-8")
    assert "incident_type" not in source and "Wildfire" not in source


def test_each_definitions_measured_rows_are_what_data_query_counts():
    """Ground truth: data_query's CAL FIRE count for each definition, year, and
    utility equals the measured rows, so coverage and the count read the same rows."""
    psycopg = pytest.importorskip("psycopg")
    from services.data_query.queries import query_calfire
    from shared.db import connect, get_settings

    try:
        conn = connect(get_settings())
    except psycopg.Error as exc:
        pytest.skip(f"warehouse not reachable: {exc}")
    try:
        for definition in DATASETS[CALFIRE].query_definitions:
            incident_type = None if definition == default_definition(CALFIRE) else definition
            for utility in [None, "BVES", "Liberty"]:
                for year in range(2009, 2027):
                    _rows, total, _extra = query_calfire(
                        conn, utility=utility, include_untagged=False, county=None, year=year,
                        start_date=None, end_date=None, min_acres=None,
                        incident_type=incident_type, limit=1, offset=0,
                    )
                    assert total == _year_rows(CALFIRE, utility, year, definition), (
                        definition, utility, year,
                    )
    finally:
        conn.close()


SDGE_2009 = [
    "How many utility ignitions did SDG&E report in 2009?",
    "SDG&E CPUC ignitions in 2009",
    "How many ignitions did San Diego Gas & Electric have in 2009?",
    "Count SDG&E's CPUC-reported ignitions for 2009",
]


@pytest.mark.parametrize("question", SDGE_2009)
def test_a_year_outside_one_dataset_uses_the_not_covered_wording(question):
    # Before, 2009 was refused as outside warehouse coverage 2014 to today.
    rule, answer = _clarification_on_any_path(question, _count("cpuc_ignitions", "SDGE", 2009))
    assert rule in {"dataset_not_covered", "executor"}, answer
    assert "CPUC ignitions for SDG&E start on 2020-01-29" in answer
    assert "absent, not zero" in answer and "warehouse coverage" not in answer


def test_a_year_between_measured_rows_is_not_covered():
    # CAL FIRE's default count has one 2009 row, then no rows until its next
    # measured year: the years between are a gap in the source, not zeros.
    years = dataset_years(CALFIRE)
    after = min(year for year in years if year > 2009)
    assert after > 2010
    for year in range(2010, after):
        assert _year_rows(CALFIRE, None, year) == 0
        decision = route_question(f"How many CAL FIRE incidents were there in {year}?")
        assert (decision.path, decision.rule) == ("clarification", "dataset_not_covered")
        assert f"have no rows between 2009 and {after}" in decision.answer


def test_the_first_covered_year_is_measured_not_declared():
    from services.agent.time_resolve import DATA_YEAR_MIN

    # Every query definition counts: a year only one of them has rows in can
    # still be asked about.
    first = min(
        int(year)
        for entry in DATASET_COVERAGE.values()
        for measured in (entry, *(entry.get("definitions") or {}).values())
        for year, rows in (measured.get("years") or {}).items()
        if rows
    )
    assert DATA_YEAR_MIN == first
    source = (ROOT / "services/agent/time_resolve.py").read_text(encoding="utf-8")
    assert not re.search(r"DATA_YEAR_MIN\s*=\s*\d", source)
    assert route_question(f"How many CAL FIRE incidents were there in {first - 1}?").rule == (
        "time_out_of_coverage"
    )


def _utility_comparison_service(request: httpx.Request) -> httpx.Response:
    assert request.url.path.endswith("/compare-utilities"), request.url
    params = request.url.params
    results = [
        {"key": code, "value": 1000 if code in covered_utilities("epss_outages") else None,
         "raw_value": None,
         "reason": None if code in covered_utilities("epss_outages") else "no rows"}
        for code in params.get("utilities").split(",")
    ]
    return httpx.Response(
        200, json={"metric": params.get("metric"), "normalize": "none", "results": results, "meta": {}}
    )


def test_u2_pge_vs_sdge_epss_2022_offers_only_what_sdge_has_in_2022():
    question = "Compare EPSS outages for PG&E and SDG&E in 2022."
    backend = _RecordingBackend(_utility_comparison_service)
    response = _ask(question, backend)
    text = response["answer_text"]
    assert response["status"] == "answer", text
    assert "SDG&E has no EPSS outage count" in text
    assert "CPUC ignitions have records for SDG&E in 2022 and can be compared instead" in text
    assert "PSPS" not in text and "do exist" not in text


U7 = "Show EPSS outages for Southern California Edison in Los Angeles County in 2024."
U7_CALLS = [
    (U7, "data_query_records", {"dataset": "epss_outages", "result_mode": "count", "utility": "SCE",
                                "county": "Los Angeles", "year": 2024}, "Los Angeles County"),
    (U7, "visualization_create", {"kind": "map", "dataset": "epss", "utility": "SCE",
                                  "county": "Los Angeles County", "year": 2024}, "Los Angeles County"),
]


@pytest.mark.parametrize("question,tool,args,dropped", U7_CALLS)
def test_u7_an_offer_that_drops_a_filter_says_so(question, tool, args, dropped):
    assert route_question(question).path == "model"
    backend = _RecordingBackend()
    response = _ask(question, backend, _ScriptedModel([(tool, args)]))
    text = response["answer_text"]
    assert backend.requests == []
    assert response["status"] == "clarification", text
    assert "PSPS events and CPUC ignitions have records for SCE in 2024" in text
    assert f"That offer drops the {dropped} filter." in text


@pytest.mark.parametrize(
    "extra,dropped",
    [
        ({"circuit_id": "043371102"}, "circuit 043371102 filter"),
        ({"bbox": [-118.7, 33.7, -117.6, 34.4]}, "map area filter"),
        ({"county": "Kern", "min_acres": 100}, "Kern County and minimum 100 acres filters"),
    ],
)
def test_every_filter_coverage_does_not_measure_is_named_as_dropped(extra, dropped):
    from services.agent.coverage import call_coverage_gap
    from services.shared.dataset_registry import not_covered_question

    args = {"dataset": "epss_outages", "utility": "SCE", "year": 2024, **extra}
    text = not_covered_question(call_coverage_gap("data_query_records", args))
    assert f"That offer drops the {dropped}." in text
    # Without such a filter, nothing is said about dropping one.
    plain = call_coverage_gap("data_query_records", {"dataset": "epss_outages", "utility": "SCE", "year": 2024})
    assert "drops the" not in not_covered_question(plain)


def test_a_not_covered_answer_with_nothing_to_offer_asks_nothing():
    backend = _RecordingBackend()
    call = ("data_query_records", {"dataset": "cpuc_ignitions", "result_mode": "count",
                                   "utility": "Liberty", "county": "Placer", "year": 2022})
    response = _ask("Liberty CPUC ignitions in Placer County, 2022", backend, _ScriptedModel([call]))
    text = response["answer_text"]
    assert response["status"] == "clarification", text
    assert "one of those" not in text and "Do you want" not in text
    assert "drops the" not in text
