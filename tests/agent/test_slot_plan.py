"""Slot plans do not call Jev."""

from services.agent.eval.slot_plan import slot_plan


def test_a_monthly_trend_alone_is_one_series():
    plan = slot_plan("Show the monthly CAL FIRE incident trend for 2024")
    assert plan == ["visualization_create"]
    monthly_count = slot_plan(
        "For PGE, what was the monthly count of EPSS outages across 2022?"
    )
    assert monthly_count == ["visualization_create"]
    each_month = slot_plan("How many PGE EPSS outages were there in each month of 2023?")
    assert each_month == ["visualization_create"]


def test_a_monthly_breakdown_plus_a_total_adds_the_count():
    plan = slot_plan("Give me the PGE ignition count and its monthly trend for 2024")
    assert plan == ["data_query_records", "visualization_create"]
    yearly = slot_plan("PGE EPSS events by month for 2023 plus the yearly total")
    assert yearly == ["data_query_records", "visualization_create"]
