"""Model tool calls may not narrow a range the harness resolved from the question."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.time_resolve import apply_harness_years, resolve_time
from services.agent.tools import ToolExecutor


TODAY = date(2026, 8, 10)


def _slot(question: str) -> dict:
    return resolve_time(question, today=TODAY).as_slot()


def _hold(arguments: dict, question: str) -> tuple[dict, str | None, list[dict]]:
    corrections: list[dict] = []
    filled, error = apply_harness_years(
        arguments,
        time_resolution=_slot(question),
        today=TODAY,
        hold_window=True,
        corrections=corrections,
    )
    return filled, error, corrections


def test_year_range_is_not_narrowed_to_one_year():
    filled, error, corrections = _hold(
        {"dataset": "cpuc_ignitions", "result_mode": "count", "year": 2023},
        "How many SCE ignitions 2021 to 2025?",
    )
    assert error is None
    assert "year" not in filled
    assert filled["start_date"] == "2021-01-01"
    assert filled["end_date"] == "2025-12-31"
    assert len(corrections) == 1
    assert corrections[0]["requested"] == {"year": 2023}
    assert corrections[0]["resolved"] == ["2021-01-01", "2025-12-31"]


def test_year_range_is_not_narrowed_to_a_window():
    filled, error, corrections = _hold(
        {
            "dataset": "cpuc_ignitions",
            "start_date": "2022-03-01",
            "end_date": "2022-06-30",
        },
        "How many SCE ignitions 2021 to 2025?",
    )
    assert error is None
    assert (filled["start_date"], filled["end_date"]) == ("2021-01-01", "2025-12-31")
    assert len(corrections) == 1


def test_open_range_up_to_today_is_not_narrowed():
    question = "How many PG&E ignitions were there from January 2024 up to today?"
    by_year, error, corrections = _hold({"dataset": "cpuc_ignitions", "year": 2024}, question)
    assert error is None
    assert "year" not in by_year
    assert (by_year["start_date"], by_year["end_date"]) == ("2024-01-01", "2026-08-10")
    assert corrections
    by_month, error, corrections = _hold(
        {
            "dataset": "cpuc_ignitions",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        },
        question,
    )
    assert error is None
    assert (by_month["start_date"], by_month["end_date"]) == ("2024-01-01", "2026-08-10")
    assert corrections


def test_single_year_question_stays_single_year():
    question = "How many PG&E ignitions in 2024?"
    same, error, corrections = _hold({"dataset": "cpuc_ignitions", "year": 2024}, question)
    assert error is None
    assert same == {"dataset": "cpuc_ignitions", "year": 2024}
    assert corrections == []
    narrowed, error, corrections = _hold(
        {
            "dataset": "cpuc_ignitions",
            "start_date": "2024-06-01",
            "end_date": "2024-06-30",
        },
        question,
    )
    assert error is None
    assert narrowed == {"dataset": "cpuc_ignitions", "year": 2024}
    assert len(corrections) == 1


def test_matching_window_is_left_alone():
    filled, error, corrections = _hold(
        {
            "dataset": "cpuc_ignitions",
            "start_date": "2021-01-01",
            "end_date": "2025-12-31",
        },
        "How many SCE ignitions 2021 to 2025?",
    )
    assert error is None
    assert (filled["start_date"], filled["end_date"]) == ("2021-01-01", "2025-12-31")
    assert corrections == []


def test_weekly_series_over_a_multi_year_range_reads_monthly():
    filled, error, _ = _hold(
        {"kind": "time_series", "dataset": "ignitions", "year": 2022},
        "Show SCE ignitions 2021 to 2025 as a time series",
    )
    assert error is None
    assert filled["interval"] == "monthly"
    assert "year" not in filled


def test_year_outside_the_range_keeps_existing_rules():
    question = "How many SCE ignitions 2021 to 2025?"
    for arguments in ({"year": 2019}, {"year": 2030}):
        held, held_error, _ = _hold(dict(arguments), question)
        plain, plain_error = apply_harness_years(
            dict(arguments), time_resolution=_slot(question), today=TODAY
        )
        assert (held, held_error) == (plain, plain_error)
    _, error, _ = _hold({"year": 2030}, question)
    assert error is not None


def test_router_calls_are_not_held():
    filled, error = apply_harness_years(
        {"dataset": "cpuc_ignitions", "year": 2023},
        time_resolution=_slot("How many SCE ignitions 2021 to 2025?"),
        today=TODAY,
    )
    assert error is None
    assert filled == {"dataset": "cpuc_ignitions", "year": 2023}


def _run_execute(harness_call: bool) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"unexpected": "partial"})

    async def run():
        executor = ToolExecutor(
            AgentSettings(),
            ArtifactStore(),
            transport=httpx.MockTransport(handler),
        )
        try:
            await executor.execute(
                "data_query_records",
                {"dataset": "cpuc_ignitions", "result_mode": "count", "year": 2023},
                request_id="test",
                attempt=1,
                time_resolution=resolve_time(
                    "How many CPUC ignitions 2021 to 2025?"
                ).as_slot(),
                harness_call=harness_call,
            )
        finally:
            await executor.close()

    asyncio.run(run())
    return seen


def test_executor_holds_model_range_and_logs_the_correction(capsys):
    requests = _run_execute(harness_call=False)
    assert requests
    params = requests[0].url.params
    assert params.get("start_date") == "2021-01-01"
    assert params.get("end_date") == "2025-12-31"
    assert params.get("year") is None
    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    corrections = [e for e in events if e.get("event") == "harness_time_correction"]
    assert len(corrections) == 1
    assert corrections[0]["requested"] == {"year": 2023}
    attempts = [e for e in events if e.get("event") == "tool_attempt"]
    logged = attempts[-1]["time_corrections"]
    assert len(logged) == 1
    assert logged[0]["requested"] == {"year": 2023}
    assert logged[0]["applied"] == {
        "start_date": "2021-01-01",
        "end_date": "2025-12-31",
    }


def test_executor_leaves_router_calls_alone(capsys):
    requests = _run_execute(harness_call=True)
    assert requests
    assert requests[0].url.params.get("year") == "2023"
    assert "harness_time_correction" not in capsys.readouterr().out


def test_enumerated_years_keep_one_call_per_year():
    question = "PG&E ignitions in 2021, 2022, and 2023"
    assert _slot(question)["per_year"] is True
    for year in (2021, 2022, 2023):
        filled, error, corrections = _hold(
            {"dataset": "cpuc_ignitions", "utility": "PGE", "year": year}, question
        )
        assert error is None
        assert filled == {"dataset": "cpuc_ignitions", "utility": "PGE", "year": year}
        assert corrections == []


def test_each_year_in_a_range_keeps_one_call_per_year():
    question = "PG&E ignitions each year from 2018 to 2020"
    slot = _slot(question)
    assert slot["per_year"] is True
    assert (slot["start_date"], slot["end_date"]) == ("2018-01-01", "2020-12-31")
    for year in (2018, 2019, 2020):
        filled, error, corrections = _hold(
            {"dataset": "cpuc_ignitions", "utility": "PGE", "year": year}, question
        )
        assert error is None
        assert filled == {"dataset": "cpuc_ignitions", "utility": "PGE", "year": year}
        assert corrections == []


def test_per_year_question_still_corrects_years_outside_it():
    question = "PG&E ignitions each year from 2018 to 2020"
    outside, error, corrections = _hold({"dataset": "cpuc_ignitions", "year": 2017}, question)
    assert error is None
    assert "year" not in outside
    assert (outside["start_date"], outside["end_date"]) == ("2018-01-01", "2020-12-31")
    assert corrections == []
    _, error, _ = _hold({"dataset": "cpuc_ignitions", "year": 2030}, question)
    assert error is not None


def test_breakdown_words_set_per_year():
    for question in (
        "ignitions per year 2018 to 2020",
        "ignitions by year from 2018 through 2020",
        "annual ignitions 2018-2020",
        "yearly ignitions between 2018 and 2020",
        "every year from 2018 to 2020",
        "year over year ignitions 2018 to 2020",
    ):
        assert _slot(question)["per_year"] is True, question
    for question in (
        "How many SCE ignitions 2021 to 2025?",
        "How many PG&E ignitions in 2024?",
        "How many PG&E ignitions were there from January 2024 up to today?",
    ):
        assert _slot(question)["per_year"] is False, question


def test_listed_years_reject_a_year_not_in_the_list():
    question = "PG&E ignitions in 2021, 2022, and 2023"
    for hold in (True, False):
        for arguments in (
            {"dataset": "cpuc_ignitions", "utility": "PGE", "year": 2019},
            {
                "dataset": "cpuc_ignitions",
                "start_date": "2019-01-01",
                "end_date": "2019-12-31",
            },
        ):
            _, error = apply_harness_years(
                arguments,
                time_resolution=_slot(question),
                today=TODAY,
                hold_window=hold,
            )
            assert error is not None
            assert "2019" in error
            assert "2021, 2022, 2023" in error


def test_listed_years_each_stand():
    question = "PG&E ignitions in 2021, 2022, and 2023"
    for year in (2021, 2022, 2023):
        for hold in (True, False):
            filled, error = apply_harness_years(
                {"dataset": "cpuc_ignitions", "year": year},
                time_resolution=_slot(question),
                today=TODAY,
                hold_window=hold,
            )
            assert error is None
            assert filled == {"dataset": "cpuc_ignitions", "year": year}


def test_executor_rejects_an_unlisted_year():
    async def run():
        executor = ToolExecutor(AgentSettings(), ArtifactStore())
        try:
            return await executor.execute(
                "data_query_records",
                {"dataset": "cpuc_ignitions", "result_mode": "count", "year": 2019},
                request_id="test",
                attempt=1,
                time_resolution=resolve_time(
                    "PG&E ignitions in 2021, 2022, and 2023"
                ).as_slot(),
            )
        finally:
            await executor.close()

    result = asyncio.run(run())
    assert not result.ok
    assert result.error["code"] == "year_not_derived"
