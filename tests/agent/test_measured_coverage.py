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
    # Nothing is offered: Liberty has no CAL FIRE rows in 2023 (its CAL FIRE
    # windows overlap 2023, but its measured rows are in 2014 to 2017, 2020,
    # 2021, and 2024), and its PSPS rows start 2024-11-11.
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
    # Bear Valley's CAL FIRE rows are in 2013 and 2015 only: offered with no
    # year asked, never for a year in which it has no rows.
    offered = "Bear Valley's CAL FIRE incidents" in decision.answer
    assert offered == (decision.slots.get("year") is None), decision.answer


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
# in 2022 or 2023; Bear Valley has no CAL FIRE rows in 2020, Liberty none in
# 2022. The generated file holds rows per calendar year for each dataset,
# utility, and the untagged rows, and every offer is checked against them.
# ---------------------------------------------------------------------------


def _year_rows(dataset: str, utility: str | None, year: int) -> int:
    entry = DATASET_COVERAGE[dataset]
    span = entry if utility is None else (entry.get("utilities") or {}).get(utility) or {}
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
    [("psps_events", "SDGE", 2022), ("psps_events", "SDGE", 2023),
     ("calfire_incidents", "BVES", 2020), ("calfire_incidents", "Liberty", 2022)],
)
def test_the_reviewer_pairs_overlap_the_window_but_have_no_rows(dataset, utility, year):
    start, end = date(year, 1, 1), date(year, 12, 31)
    first, last = coverage_window(dataset, utility)
    assert first <= end and last >= start
    assert _year_rows(dataset, utility, year) == 0
    assert not rows_in_period(dataset, utility, start, end)


def test_rows_in_period_needs_a_whole_year_or_a_measured_row_date():
    # SDG&E PSPS rows: 2021 (first 2021-11-24), 2024, 2025 (last 2025-01-20).
    assert rows_in_period("psps_events", "SDGE", "2024-01-01", "2024-12-31")
    assert rows_in_period("psps_events", "SDGE", "2021-11-01", "2021-11-30")
    assert rows_in_period("psps_events", "SDGE", "2025-01-01", "2025-01-31")
    assert not rows_in_period("psps_events", "SDGE", "2022-01-01", "2023-12-31")
    # Part of a year with rows, holding neither measured date: not known.
    assert not rows_in_period("psps_events", "SDGE", "2024-03-01", "2024-03-31")
    # Untagged rows are measured too.
    assert rows_in_period("calfire_incidents", "untagged", "2013-01-01", "2013-12-31")
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


CAL_FIRE_2013 = [
    "How many CAL FIRE incidents were there in 2013?",
    "CAL FIRE wildfire count for 2013",
    "Number of CAL FIRE fires in 2013?",
    "How many wildfires did CAL FIRE record in 2013?",
]


@pytest.mark.parametrize("question", CAL_FIRE_2013)
def test_cal_fire_2013_answers_because_it_has_rows(question):
    assert _year_rows("calfire_incidents", None, 2013) == 141
    decision = route_question(question)
    assert decision.path == "deterministic", decision.answer
    (tool, args), = decision.tool_calls
    assert tool == "data_query_records" and args["dataset"] == "calfire_incidents"
    assert args.get("year") == 2013 or str(args.get("start_date", ""))[:4] == "2013"


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
    # CAL FIRE has one 2009 row, then rows from 2013: 2010 to 2012 are a gap
    # in the source, not zeros.
    for year in (2010, 2011, 2012):
        assert _year_rows("calfire_incidents", None, year) == 0
        decision = route_question(f"How many CAL FIRE incidents were there in {year}?")
        assert (decision.path, decision.rule) == ("clarification", "dataset_not_covered")
        assert "CAL FIRE incidents have no rows between 2009 and 2013" in decision.answer


def test_the_first_covered_year_is_measured_not_declared():
    from services.agent.time_resolve import DATA_YEAR_MIN

    first = min(
        int(year)
        for entry in DATASET_COVERAGE.values()
        for year, rows in (entry.get("years") or {}).items()
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
