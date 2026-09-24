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


def test_open_range_up_to_today_ends_today():
    result = resolve_time(
        "How many PG&E ignitions were there from January 2024 up to today?",
        today=TODAY,
    )
    assert result.status == "relative_range"
    assert result.year is None
    assert result.years == (2024, 2025, 2026)
    assert result.start_date == "2024-01-01"
    assert result.end_date == "2026-08-10"


def test_open_range_since_month_ends_today():
    result = resolve_time("PG&E ignitions since March 2023", today=TODAY)
    assert result.status == "relative_range"
    assert result.start_date == "2023-03-01"
    assert result.end_date == "2026-08-10"


def test_open_range_through_today_ends_today():
    result = resolve_time("ignitions from 2023 through today", today=TODAY)
    assert result.status == "relative_range"
    assert result.start_date == "2023-01-01"
    assert result.end_date == "2026-08-10"


def test_open_range_to_date_ends_today():
    result = resolve_time("SCE ignitions 2021 to date", today=TODAY)
    assert result.status == "relative_range"
    assert result.start_date == "2021-01-01"
    assert result.end_date == "2026-08-10"


def test_open_range_other_end_words_and_day_starts():
    until_now = resolve_time("ignitions from 2024-08-15 until now", today=TODAY)
    assert until_now.start_date == "2024-08-15"
    assert until_now.end_date == "2026-08-10"
    since_day = resolve_time("ignitions since August 15, 2024", today=TODAY)
    assert since_day.start_date == "2024-08-15"
    assert since_day.end_date == "2026-08-10"
    same_year = resolve_time("ignitions since March 2026", today=TODAY)
    assert same_year.year == 2026
    assert same_year.start_date == "2026-03-01"
    assert same_year.end_date == "2026-08-10"


def test_open_range_end_is_capped_at_coverage():
    from services.agent.time_resolve import open_ended_range

    result = open_ended_range("since 2024", today=TODAY)
    assert result is not None
    assert result.end_date == TODAY.isoformat()
    assert result.end_date <= f"{TODAY.year}-12-31"


def test_open_range_outside_coverage_clarifies():
    before = resolve_time("ignitions since 2010", today=TODAY)
    assert before.status == "out_of_coverage"
    assert "2010" in before.reason
    after = resolve_time("ignitions since December 2026", today=TODAY)
    assert after.status == "out_of_coverage"
    assert after.start_date is None


def test_since_with_named_end_stays_bounded():
    years = resolve_time("ignitions since 2020 to 2022", today=TODAY)
    assert years.status == "explicit"
    assert years.start_date == "2020-01-01"
    assert years.end_date == "2022-12-31"
    months = resolve_time("ignitions since March 2023 to May 2024", today=TODAY)
    assert months.start_date == "2023-03-01"
    assert months.end_date == "2024-05-31"


def test_single_months_and_bounded_ranges_are_unchanged():
    month = resolve_time("PG&E ignitions in January 2024", today=TODAY)
    assert month.status == "explicit"
    assert (month.start_date, month.end_date) == ("2024-01-01", "2024-01-31")
    span = resolve_time("from august 2023 to september 2024", today=TODAY)
    assert (span.start_date, span.end_date) == ("2023-08-01", "2024-09-30")
    years = resolve_time("from 2018 through 2022", today=TODAY)
    assert (years.start_date, years.end_date) == ("2018-01-01", "2022-12-31")
    today = resolve_time("what is the risk today", today=TODAY)
    assert today.start_date == today.end_date == "2026-08-10"


def test_apostrophe_year_in_coverage_stays_in_the_2000s():
    for written, year in (("'24", 2024), ("'14", 2014)):
        result = resolve_time(f"ignitions in {written}", today=TODAY)
        assert result.status == "explicit"
        assert result.year == year


def test_apostrophe_99_is_the_1990s_and_clarifies():
    from services.agent.time_resolve import expand_apostrophe_year

    assert expand_apostrophe_year("fires in '99") == "fires in 1999"
    result = resolve_time("How many ignitions were there in '99?", today=TODAY)
    assert result.status == "out_of_coverage"
    assert result.year == 1999
    assert "1999" in result.reason
    assert "2099" not in result.reason


def test_apostrophe_years_outside_coverage_clarify_in_either_century():
    early = resolve_time("ignitions in '05", today=TODAY)
    assert early.status == "out_of_coverage"
    assert early.year == 2005
    late = resolve_time("ignitions in '75", today=TODAY)
    assert late.status == "out_of_coverage"
    assert late.year == 1975


def test_bare_1900s_year_clarifies_as_out_of_coverage():
    for question, year in (
        ("How many ignitions were there in 1999?", 1999),
        ("CPUC ignitions from 1999 to 2005", 1999),
        ("PG&E ignitions since 1985", 1985),
        ("ignitions in March 1999", 1999),
    ):
        result = resolve_time(question, today=TODAY)
        assert result.status == "out_of_coverage", question
        assert result.year == year
        assert f"Year {year} is outside warehouse coverage" in result.reason


def test_numbers_that_are_not_1900s_years_are_ignored():
    assert resolve_time("fires near 38.1985, -121.4944", today=TODAY).status == "none"
    assert resolve_time("fires over 1950 acres in 2024", today=TODAY).year == 2024
    assert resolve_time("fires larger than 1,950 acres", today=TODAY).status == "none"
    assert resolve_time("circuit 043371102 outages in 2024", today=TODAY).year == 2024


def _window(question):
    result = resolve_time(question, today=TODAY)
    return result.status, result.start_date, result.end_date


def test_month_to_month_in_one_year_covers_the_full_window():
    assert _window("How many PGE ignitions were there from March to June 2023?") == (
        "explicit",
        "2023-03-01",
        "2023-06-30",
    )


def test_month_through_month_covers_the_full_window():
    assert _window("PGE ignitions March through June 2023") == (
        "explicit",
        "2023-03-01",
        "2023-06-30",
    )
    assert _window("PGE ignitions Mar thru Jun 2023")[1:] == ("2023-03-01", "2023-06-30")


def test_abbreviated_dashed_month_range_covers_the_full_window():
    assert _window("PGE ignitions Mar-Jun 2023") == ("explicit", "2023-03-01", "2023-06-30")
    assert _window("PGE ignitions Mar - Jun 2023")[1:] == ("2023-03-01", "2023-06-30")


def test_between_month_and_month_covers_the_full_window():
    assert _window("PGE ignitions between March and June 2023") == (
        "explicit",
        "2023-03-01",
        "2023-06-30",
    )


def test_month_range_keeps_the_year_and_the_phrase():
    result = resolve_time("CAL FIRE incidents from March to June of 2023", today=TODAY)
    assert result.year == 2023
    assert result.years == (2023,)
    assert (result.start_date, result.end_date) == ("2023-03-01", "2023-06-30")
    assert result.phrase == "from march to june of 2023"
    february = resolve_time("ignitions from January to February 2024", today=TODAY)
    assert february.end_date == "2024-02-29"


def test_a_month_range_across_a_year_boundary_is_ambiguous():
    result = resolve_time("PGE ignitions from November to February 2023", today=TODAY)
    assert result.status == "ambiguous"
    assert result.start_date is None


def test_a_month_range_outside_coverage_clarifies():
    assert resolve_time("PGE ignitions from March to June 2010", today=TODAY).status == (
        "out_of_coverage"
    )


def test_single_months_and_two_year_month_spans_are_unchanged():
    assert _window("PGE ignitions in March 2023") == ("explicit", "2023-03-01", "2023-03-31")
    assert _window("from march 2023 to june 2024") == ("explicit", "2023-03-01", "2024-06-30")
    assert _window("CAL FIRE incidents from March 2023 to June 2023") == (
        "explicit",
        "2023-03-01",
        "2023-06-30",
    )


def test_router_counts_the_full_month_range():
    from services.agent.routing import route_question

    decision = route_question("How many PGE ignitions were there from March to June 2023?")
    assert decision.path == "deterministic"
    (tool, args), = decision.tool_calls
    assert tool == "data_query_records"
    assert (args["start_date"], args["end_date"]) == ("2023-03-01", "2023-06-30")
    assert args["utility"] == "PGE"
