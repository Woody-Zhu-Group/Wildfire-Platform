"""Normalize calendar-year and inclusive date filters before querying dates."""

from datetime import date

from fastapi import HTTPException


def date_window(
    year: int | None, start: date | None, end: date | None,
) -> tuple[date | None, date | None]:
    """Intersect explicit bounds with the calendar year, preserving all filters.

    A disjoint intersection stays reversed so SQL matches no rows. Source
    year attributes are not used to decide which events fall in this window.
    """
    if year is None:
        return start, end
    if not 1 <= year <= 9999:
        raise HTTPException(status_code=400, detail="year must be between 1 and 9999")
    first, last = date(year, 1, 1), date(year, 12, 31)
    return max(first, start) if start else first, min(last, end) if end else last
