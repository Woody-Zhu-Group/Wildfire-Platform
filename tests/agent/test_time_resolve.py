"""Harness-owned relative time resolution."""

from __future__ import annotations

from datetime import date

from services.agent.time_resolve import resolve_time, year_guard_error


TODAY = date(2026, 8, 10)


def test_supported_relative_forms():
    assert resolve_time("last year", today=TODAY).year == 2025
    assert resolve_time("this year", today=TODAY).year == 2026
    assert resolve_time("2 years ago", today=TODAY).year == 2024
    assert resolve_time("two years ago", today=TODAY).year == 2024
    span = resolve_time("in the last 2 years", today=TODAY)
    assert span.status == "relative_range"
    assert span.years == (2025, 2026)
    assert span.start_date == "2025-01-01"
    assert span.end_date == "2026-12-31"


def test_vague_relative_forms_are_ambiguous():
    for phrase in ("recent", "lately", "currently", "nowadays"):
        assert resolve_time(f"{phrase} ignitions", today=TODAY).status == "ambiguous"


def test_out_of_coverage_years_ago():
    result = resolve_time("20 years ago", today=TODAY)
    assert result.status == "out_of_coverage"


def test_year_guard_rejects_invented_year_and_overrides_mismatch():
    from services.agent.time_resolve import apply_harness_years

    resolution = resolve_time("2 years ago", today=TODAY).as_slot()
    # Wrong year is overridden to the harness year, not rejected.
    filled, error = apply_harness_years(
        {"year": 2022}, time_resolution=resolution, today=TODAY
    )
    assert error is None
    assert filled.get("year") == 2024
    assert (
        year_guard_error({"year": 2024}, time_resolution=resolution, today=TODAY)
        is None
    )
    # Invented year with no harness resolution still fails.
    assert (
        year_guard_error(
            {"year": 2024},
            time_resolution={"status": "none"},
            today=TODAY,
        )
        is not None
    )


def test_explicit_month_window():
    result = resolve_time("cpuc august 2023", today=TODAY)
    assert result.status == "explicit"
    assert result.year == 2023
    assert result.start_date == "2023-08-01"
    assert result.end_date == "2023-08-31"


def test_apostrophe_year_expands_into_the_2000s():
    result = resolve_time("Tally ignitions in '24", today=TODAY)
    assert result.status == "explicit"
    assert result.year == 2024
    assert result.start_date == "2024-01-01"
    assert result.end_date == "2024-12-31"


def test_bare_year_is_full_calendar_year():
    result = resolve_time("cpuc ignitions in 2024", today=TODAY)
    assert result.status == "explicit"
    assert result.year == 2024
    assert result.years == (2024,)
    assert result.start_date == "2024-01-01"
    assert result.end_date == "2024-12-31"


def test_month_year_to_month_year_span():
    result = resolve_time(
        "map cpuc ignitions from august 2023 to september 2024",
        today=TODAY,
    )
    assert result.status == "explicit"
    assert result.year is None
    assert result.years == (2023, 2024)
    assert result.start_date == "2023-08-01"
    assert result.end_date == "2024-09-30"


def test_named_year_ranges_include_every_year():
    assert resolve_time("ignition counts 2017 to 2023", today=TODAY).years == tuple(
        range(2017, 2024)
    )
    assert resolve_time("from 2018 through 2022", today=TODAY).years == tuple(
        range(2018, 2023)
    )
    assert resolve_time("2019-2022 totals", today=TODAY).years == tuple(range(2019, 2023))
    assert resolve_time("between 2017 and 2023", today=TODAY).years == tuple(
        range(2017, 2024)
    )
    named = resolve_time("how many in 2018, and how many in 2020", today=TODAY)
    assert named.years == (2018, 2020)


def test_year_to_year_and_dashed_span():
    spoken = resolve_time("trend of SCE ignitions 2021 to 2025", today=TODAY)
    assert spoken.status == "explicit"
    assert spoken.year is None
    assert spoken.years == (2021, 2022, 2023, 2024, 2025)
    assert spoken.start_date == "2021-01-01"
    assert spoken.end_date == "2025-12-31"
    dashed = resolve_time("SCE ignitions 2021-2025", today=TODAY)
    assert dashed.start_date == "2021-01-01"
    assert dashed.end_date == "2025-12-31"


def test_today_and_tomorrow_resolve_to_calendar_days():
    today = resolve_time("today", today=TODAY)
    assert today.start_date == today.end_date == "2026-08-10"
    assert today.phrase == "today"
    tomorrow = resolve_time("tomorrow", today=TODAY)
    assert tomorrow.start_date == tomorrow.end_date == "2026-08-11"
    assert tomorrow.phrase == "tomorrow"


def test_explicit_calendar_day_is_not_widened():
    iso = resolve_time(
        "What was the ignition risk in Sacramento County on 2024-08-15?",
        today=TODAY,
    )
    assert iso.start_date == "2024-08-15"
    assert iso.end_date == "2024-08-15"
    spoken = resolve_time(
        "How risky was Sacramento County on August 15th 2024?",
        today=TODAY,
    )
    assert spoken.start_date == "2024-08-15"
    assert spoken.end_date == "2024-08-15"
    comma = resolve_time("August 15, 2024", today=TODAY)
    assert comma.start_date == comma.end_date == "2024-08-15"
    day_first = resolve_time("15 August 2024", today=TODAY)
    assert day_first.start_date == day_first.end_date == "2024-08-15"
