"""Quantities cannot add years to a question's calendar scope."""

from datetime import date

import pytest

from services.agent.routing import route_question
from services.agent.time_resolve import explicit_year_range, resolve_time

TODAY = date(2026, 10, 1)


@pytest.mark.parametrize(
    "question,year",
    [
        ("fires bigger than 2000 acres in 2023", 2023),
        ("more than 2050 customers affected in 2024", 2024),
        ("List CAL FIRE fires exceeding 2025 acres during 2023.", 2023),
        ("How many CAL FIRE fires had an area above 2001 acres in 2024?", 2024),
        ("How many CAL FIRE fires were larger than 2099 acres in 2022?", 2022),
        ("In 2024, list incidents with footprints of 2018 hectares.", 2024),
        ("Show outages affecting 2020 households during 2023.", 2023),
        ("For 2022, find events within 2021 meters of this point.", 2022),
        ("CAL FIRE fires > 2000 acres in 2023", 2023),
        ("Equipment at 2050 kV in 2024", 2024),
        ("2000 widgets recorded in 2023", 2023),
        ("$2000 in 2023", 2023),
        ("2000.5 acres in 2023", 2023),
        ("38.2000, -121.2021 in 2024", 2024),
        ("1995 meters in 2023", 2023),
        ("fires above the 2000-acre threshold in 2023", 2023),
    ],
)
def test_quantities_do_not_create_years_or_breakdowns(question, year):
    time = resolve_time(question, today=TODAY)
    assert time.status == "explicit"
    assert time.year == year
    assert time.years == (year,)
    assert time.per_year is False
    slots = route_question(question).slots
    assert slots["years"] == [year]


@pytest.mark.parametrize(
    "question",
    [
        "fires over 2000 acres",
        "2050 customers affected",
        "1995 meters",
        "$2023",
        "2000.5 acres",
    ],
)
def test_quantities_without_dates_leave_time_unspecified(question):
    time = resolve_time(question, today=TODAY)
    assert time.status == "none"
    assert not time.years
    assert not time.per_year


@pytest.mark.parametrize(
    "question",
    [
        "counts in 2021 to 2025",
        "July 2024 and August 2024",
        "each year since 2020",
        "2019-2022 totals",
        "Was 2024 better or worse?",
        "2023 CPUC ignitions",
        "the 2021 Dixie Fire",
        "on 2024-08-15",
        "Were there fewer CAL FIRE fires in 2022 than 2020?",
        "Map the 2024 US ignition sample.",
        "the 2024 fire season",
        "what proportion of 2020 sampled wildfire ignitions were inside utility territories?",
        "Edison and outages: was 2021 rougher than 2020 for their customers?",
        "CPUC: is 2023 lower than 2021 for PG&E?",
        "Outages: were 2024 higher than 2022 within PG&E territory?",
    ],
)
def test_calendar_syntax_is_preserved(question):
    assert resolve_time(question, today=TODAY).status in {"explicit", "relative_range"}


def test_quantity_does_not_make_a_third_year_outside_a_range():
    time = resolve_time("fires above 2000 acres from 2021 to 2025", today=TODAY)
    assert time.years == (2021, 2022, 2023, 2024, 2025)
    assert not time.per_year
    assert explicit_year_range("fires above 2000 acres from 2021 to 2025")[:2] == (
        "2021-01-01",
        "2025-12-31",
    )


def test_a_large_quantity_does_not_trigger_future_year_refusal():
    result = route_question(
        "How many CAL FIRE fires were larger than 2099 acres in 2022?"
    )
    assert result.rule != "unsupported_future_prediction"
    assert result.slots["year"] == 2022
