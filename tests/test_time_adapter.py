from datetime import date

import pytest
from fastapi import HTTPException

from services.shared.time_adapter import date_window


@pytest.mark.parametrize("year,start,end,expected", [
    (2023, None, None, (date(2023, 1, 1), date(2023, 12, 31))),
    (2024, date(2024, 2, 29), None, (date(2024, 2, 29), date(2024, 12, 31))),
    (2023, None, date(2023, 6, 30), (date(2023, 1, 1), date(2023, 6, 30))),
    (2023, date(2022, 1, 1), date(2024, 12, 31), (date(2023, 1, 1), date(2023, 12, 31))),
    (None, date(2022, 12, 31), date(2023, 1, 1), (date(2022, 12, 31), date(2023, 1, 1))),
    (None, None, None, (None, None)),
])
def test_year_and_explicit_dates_share_one_window(year, start, end, expected):
    assert date_window(year, start, end) == expected


def test_conflicting_year_and_dates_never_drop_a_filter():
    start, end = date_window(2023, date(2024, 1, 1), date(2024, 12, 31))
    assert start > end


@pytest.mark.parametrize("year", [0, 10000])
def test_invalid_calendar_year_is_a_client_error(year):
    with pytest.raises(HTTPException) as exc:
        date_window(year, None, None)
    assert exc.value.status_code == 400
