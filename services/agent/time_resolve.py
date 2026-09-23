"""Harness-owned relative time resolution. The model must never invent years."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# Warehouse coverage used for out-of-range guards (inclusive).
DATA_YEAR_MIN = 2014

WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_MONTH_ALT = "|".join(
    re.escape(name) for name, _ in sorted(MONTHS.items(), key=lambda item: -len(item[0]))
)
_RANGE_SEP = r"(?:to|through|until|–|—|-)"


@dataclass(frozen=True)
class TimeResolution:
    """Result of harness time parsing for a question."""

    status: str  # explicit | relative_year | relative_range | ambiguous | none | out_of_coverage
    year: int | None = None
    years: tuple[int, ...] = ()
    start_date: str | None = None
    end_date: str | None = None
    source: str | None = None
    phrase: str | None = None
    reason: str | None = None

    def as_slot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "year": self.year,
            "years": list(self.years),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "source": self.source,
            "phrase": self.phrase,
            "reason": self.reason,
        }


def _parse_count(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        value = int(token)
        return value if value > 0 else None
    return WORD_NUMBERS.get(token)


def month_from_text(text: str) -> tuple[int, str] | None:
    """Return (month_number, phrase) when a calendar month is named."""
    lower = " ".join(text.lower().split())
    for name, number in sorted(MONTHS.items(), key=lambda item: -len(item[0])):
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return number, name
    return None


def explicit_calendar_day(text: str) -> date | None:
    """A specific calendar day, or None when only a month/year is named.

    Accepts ISO ``YYYY-MM-DD``, ``August 15th 2024``, ``August 15, 2024``,
    and ``15 August 2024``. A month with no day (``August 2024``) is not a day.
    """
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    lower = " ".join(text.lower().split())
    names = "|".join(
        re.escape(name) for name, _ in sorted(MONTHS.items(), key=lambda item: -len(item[0]))
    )
    suffix = r"(?:st|nd|rd|th)?"
    month_first = re.search(
        rf"\b({names})\s+(\d{{1,2}}){suffix},?\s+(20\d{{2}})\b",
        lower,
    )
    if month_first:
        try:
            return date(
                int(month_first.group(3)),
                MONTHS[month_first.group(1)],
                int(month_first.group(2)),
            )
        except ValueError:
            return None
    day_first = re.search(
        rf"\b(\d{{1,2}}){suffix}\s+({names})\s+(20\d{{2}})\b",
        lower,
    )
    if day_first:
        try:
            return date(
                int(day_first.group(3)),
                MONTHS[day_first.group(2)],
                int(day_first.group(1)),
            )
        except ValueError:
            return None
    return None


def _range_for_year_month(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}"


def explicit_month_year_range(text: str) -> tuple[str, str, str] | None:
    """``August 2023 to September 2024`` → first-of-start-month … last-of-end-month."""
    lower = " ".join(text.lower().split())
    match = re.search(
        rf"\b({_MONTH_ALT})\s+(20\d{{2}})\s*{_RANGE_SEP}\s*({_MONTH_ALT})\s+(20\d{{2}})\b",
        lower,
    )
    if not match:
        return None
    month_a, year_a = MONTHS[match.group(1)], int(match.group(2))
    month_b, year_b = MONTHS[match.group(3)], int(match.group(4))
    start, _ = _range_for_year_month(year_a, month_a)
    _, end = _range_for_year_month(year_b, month_b)
    if start > end:
        start, _ = _range_for_year_month(year_b, month_b)
        _, end = _range_for_year_month(year_a, month_a)
    return start, end, match.group(0)


def explicit_year_range(text: str) -> tuple[str, str, str] | None:
    """``2021 to 2025`` / ``2021-2025`` → full inclusive calendar years."""
    lower = " ".join(text.lower().split())
    match = re.search(rf"\b(20\d{{2}})\s+(?:to|through|until)\s+(20\d{{2}})\b", lower)
    if not match:
        match = re.search(rf"\bbetween\s+(20\d{{2}})\s+and\s+(20\d{{2}})\b", lower)
    if not match:
        match = re.search(rf"\b(20\d{{2}})\s*[-–—]\s*(20\d{{2}})\b", lower)
    if not match:
        return None
    year_a, year_b = int(match.group(1)), int(match.group(2))
    if year_a > year_b:
        year_a, year_b = year_b, year_a
    return f"{year_a}-01-01", f"{year_b}-12-31", match.group(0)


def _span_resolution(
    start: str, end: str, *, phrase: str, data_max: int
) -> TimeResolution:
    start_year = int(start[:4])
    end_year = int(end[:4])
    years = tuple(range(start_year, end_year + 1))
    for year in years:
        if year < DATA_YEAR_MIN or year > data_max:
            return TimeResolution(
                status="out_of_coverage",
                years=years,
                source="explicit",
                phrase=phrase,
                reason=(
                    f"Year {year} is outside warehouse coverage "
                    f"{DATA_YEAR_MIN}-{data_max}"
                ),
            )
    return TimeResolution(
        status="explicit",
        year=start_year if start_year == end_year else None,
        years=years,
        start_date=start,
        end_date=end,
        source="explicit",
        phrase=phrase,
    )


_APOSTROPHE_YEAR = re.compile(r"(?<!\d)'(\d{2})\b")
# Same pivot as Python's %y: '00-'68 are the 2000s, '69-'99 the 1900s.
_APOSTROPHE_PIVOT = 69


def _apostrophe_century_year(digits: str) -> int:
    value = int(digits)
    return (1900 if value >= _APOSTROPHE_PIVOT else 2000) + value


def expand_apostrophe_year(text: str) -> str:
    """Turn a written '24 into 2024 and '99 into 1999.

    The digits are in the question; this is not a guess. A year outside
    coverage in either century (like '99) is left to the coverage clarify.
    """

    def replace(match: re.Match[str]) -> str:
        return str(_apostrophe_century_year(match.group(1)))

    return _APOSTROPHE_YEAR.sub(replace, text)


# A written 1900s year. Decimals (a coordinate like 38.1985), thousands
# separators, money, and acreage are numbers, not years.
_BARE_1900S_YEAR = re.compile(
    r"(?<![\d.,$])\b(19\d{2})\b(?![.,]\d)(?!\s*(?:acres?|ac)\b)",
    re.IGNORECASE,
)


def _pre_2000_apostrophe_year(text: str) -> tuple[int, str] | None:
    """The first '69-'99 year written in the question, as (year, phrase)."""
    for match in _APOSTROPHE_YEAR.finditer(text):
        year = _apostrophe_century_year(match.group(1))
        if year < 2000:
            return year, match.group(0)
    return None


def _pre_2000_year(text: str) -> tuple[int, str] | None:
    """The first pre-2000 year in the question, written '99 or 1999."""
    written = _pre_2000_apostrophe_year(text)
    if written is not None:
        return written
    bare = _BARE_1900S_YEAR.search(text)
    if bare is not None:
        return int(bare.group(1)), bare.group(1)
    return None


_START_EXPR = (
    rf"(?P<start>20\d{{2}}-\d{{2}}-\d{{2}}"
    rf"|(?:{_MONTH_ALT})\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+20\d{{2}}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_ALT})\s+20\d{{2}}"
    rf"|(?:{_MONTH_ALT})\s+(?:of\s+)?20\d{{2}}"
    rf"|20\d{{2}})"
)
_OPEN_END = (
    r"(?:up\s+(?:to|until|till|through)|to|through|thru|until|till|til|\u2013|\u2014|-)\s*"
    r"(?:today|now|the\s+present(?:\s+day)?|present(?:\s+day)?|date)\b"
)
_OPEN_RANGE_WITH_END = re.compile(
    rf"(?:\b(?:from|since)\s+)?(?<![\w-]){_START_EXPR}\s*{_OPEN_END}"
)
_SINCE_START = re.compile(rf"\bsince\s+{_START_EXPR}(?![\w-])")
_BOUNDED_END_AHEAD = re.compile(
    rf"\s*(?:to|through|thru|until|till|\u2013|\u2014|-)\s*(?:{_MONTH_ALT}|20\d{{2}})"
)


def _open_range_start(phrase: str) -> date | None:
    """First day named by an open range start: a day, a month, or a year."""
    day = explicit_calendar_day(phrase)
    if day is not None:
        return day
    month_year = re.fullmatch(rf"({_MONTH_ALT})\s+(?:of\s+)?(20\d{{2}})", phrase)
    if month_year:
        return date(int(month_year.group(2)), MONTHS[month_year.group(1)], 1)
    if re.fullmatch(r"20\d{2}", phrase):
        return date(int(phrase), 1, 1)
    return None


def open_ended_range(lower: str, *, today: date) -> TimeResolution | None:
    """``from January 2024 up to today`` / ``since March 2023`` / ``2021 to date``.

    The end is today, capped at the end of warehouse coverage. A ``since``
    start followed by a named end (``since 2020 to 2022``) is a bounded range
    and is left to the bounded parsers.
    """
    match = _OPEN_RANGE_WITH_END.search(lower)
    if match is None:
        match = _SINCE_START.search(lower)
        if match is None or _BOUNDED_END_AHEAD.match(lower, match.end()):
            return None
    start = _open_range_start(match.group("start"))
    if start is None:
        return None
    phrase = match.group(0).strip()
    data_max = today.year
    end = min(today, date(data_max, 12, 31))
    if start.year < DATA_YEAR_MIN:
        return TimeResolution(
            status="out_of_coverage",
            years=tuple(range(start.year, end.year + 1)),
            source="relative",
            phrase=phrase,
            reason=(
                f"Year {start.year} is outside warehouse coverage "
                f"{DATA_YEAR_MIN}-{data_max}"
            ),
        )
    if start > end:
        return TimeResolution(
            status="out_of_coverage",
            year=start.year,
            years=(start.year,),
            source="relative",
            phrase=phrase,
            reason=(
                f"The range starts {start.isoformat()}, after the end of "
                f"warehouse coverage {end.isoformat()}"
            ),
        )
    years = tuple(range(start.year, end.year + 1))
    return TimeResolution(
        status="relative_range",
        year=years[0] if len(years) == 1 else None,
        years=years,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        source="relative",
        phrase=phrase,
    )


def resolve_time(text: str, *, today: date | None = None) -> TimeResolution:
    """Resolve explicit or relative time. Never guess vague phrases."""
    ref = today or date.today()
    data_max = ref.year
    early = _pre_2000_year(text)
    if early is not None:
        year, written = early
        return TimeResolution(
            status="out_of_coverage",
            year=year,
            years=(year,),
            source="explicit",
            phrase=written,
            reason=(
                f"Year {year} is outside warehouse coverage "
                f"{DATA_YEAR_MIN}-{data_max}"
            ),
        )
    text = expand_apostrophe_year(text)
    lower = " ".join(text.lower().split())
    open_range = open_ended_range(lower, today=ref)
    if open_range is not None:
        return open_range
    month_hit = month_from_text(lower)
    day = explicit_calendar_day(text)
    if day is not None:
        if day.year < DATA_YEAR_MIN or day.year > data_max:
            return TimeResolution(
                status="out_of_coverage",
                year=day.year,
                years=(day.year,),
                source="explicit",
                phrase=day.isoformat(),
                reason=(
                    f"Year {day.year} is outside warehouse coverage "
                    f"{DATA_YEAR_MIN}-{data_max}"
                ),
            )
        return TimeResolution(
            status="explicit",
            year=day.year,
            years=(day.year,),
            start_date=day.isoformat(),
            end_date=day.isoformat(),
            source="explicit",
            phrase=day.isoformat(),
        )

    month_span = explicit_month_year_range(lower)
    if month_span is not None:
        start, end, phrase = month_span
        return _span_resolution(start, end, phrase=phrase, data_max=data_max)

    year_span = explicit_year_range(lower)
    if year_span is not None:
        start, end, phrase = year_span
        return _span_resolution(start, end, phrase=phrase, data_max=data_max)

    explicit = list(dict.fromkeys(int(v) for v in re.findall(r"\b(20\d{2})\b", text)))
    if len(set(explicit)) == 1:
        year = explicit[0]
        if year < DATA_YEAR_MIN or year > data_max:
            return TimeResolution(
                status="out_of_coverage",
                year=year,
                years=(year,),
                source="explicit",
                phrase=str(year),
                reason=f"Year {year} is outside warehouse coverage {DATA_YEAR_MIN}-{data_max}",
            )
        if month_hit is not None:
            month, month_name = month_hit
            start, end = _range_for_year_month(year, month)
            # Keep year= for slots/audit; tool builders must prefer start/end
            # so the month is not silently widened to the full calendar year.
            return TimeResolution(
                status="explicit",
                year=year,
                years=(year,),
                start_date=start,
                end_date=end,
                source="explicit",
                phrase=f"{month_name} {year}",
            )
        return TimeResolution(
            status="explicit",
            year=year,
            years=(year,),
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
            source="explicit",
            phrase=str(year),
        )
    if len(set(explicit)) > 1:
        years = tuple(explicit)
        for year in years:
            if year < DATA_YEAR_MIN or year > data_max:
                return TimeResolution(
                    status="out_of_coverage",
                    years=years,
                    source="explicit",
                    phrase=",".join(str(y) for y in years),
                    reason=f"Year {year} is outside warehouse coverage {DATA_YEAR_MIN}-{data_max}",
                )
        return TimeResolution(
            status="explicit",
            years=years,
            source="explicit",
            phrase=",".join(str(y) for y in years),
        )

    if re.search(r"\btomorrow\b", lower):
        day = ref + timedelta(days=1)
        return TimeResolution(
            status="explicit",
            year=day.year,
            years=(day.year,),
            start_date=day.isoformat(),
            end_date=day.isoformat(),
            source="relative",
            phrase="tomorrow",
        )
    if re.search(r"\b(?:today|tonight)\b", lower):
        return TimeResolution(
            status="explicit",
            year=ref.year,
            years=(ref.year,),
            start_date=ref.isoformat(),
            end_date=ref.isoformat(),
            source="relative",
            phrase="today",
        )

    # Vague: never resolve.
    vague = re.search(
        r"\b(?P<phrase>recent|recently|lately|currently|current|nowadays)\b",
        lower,
    )
    if vague:
        return TimeResolution(
            status="ambiguous",
            source="relative",
            phrase=vague.group("phrase"),
            reason="Vague relative time cannot be mapped to a calendar year",
        )

    # "N years ago" / "two years ago" -> single calendar year.
    ago = re.search(
        r"\b(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\s+ago\b",
        lower,
    )
    if ago:
        n = _parse_count(ago.group("n"))
        if n is None:
            return TimeResolution(
                status="ambiguous",
                source="relative",
                phrase=ago.group(0),
                reason="Could not parse years-ago count",
            )
        year = ref.year - n
        if year < DATA_YEAR_MIN or year > data_max:
            return TimeResolution(
                status="out_of_coverage",
                year=year,
                years=(year,),
                source="relative",
                phrase=ago.group(0),
                reason=f"Resolved year {year} is outside warehouse coverage {DATA_YEAR_MIN}-{data_max}",
            )
        return TimeResolution(
            status="relative_year",
            year=year,
            years=(year,),
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
            source="relative",
            phrase=ago.group(0),
        )

    if re.search(r"\blast\s+year\b", lower):
        year = ref.year - 1
        return TimeResolution(
            status="relative_year",
            year=year,
            years=(year,),
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
            source="relative",
            phrase="last year",
        )
    if re.search(r"\bthis\s+year\b", lower):
        year = ref.year
        return TimeResolution(
            status="relative_year",
            year=year,
            years=(year,),
            start_date=f"{year}-01-01",
            end_date=f"{year}-12-31",
            source="relative",
            phrase="this year",
        )

    # "in the last N years" / "over the past N years" -> inclusive calendar range.
    last_n = re.search(
        r"\b(?:in\s+the\s+last|over\s+the\s+past|during\s+the\s+past|past|last|previous)\s+"
        r"(?P<n>\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\b",
        lower,
    )
    if last_n:
        n = _parse_count(last_n.group("n"))
        if n is None or n < 1:
            return TimeResolution(
                status="ambiguous",
                source="relative",
                phrase=last_n.group(0),
                reason="Could not parse multi-year relative span",
            )
        start_year = ref.year - n + 1
        end_year = ref.year
        if start_year < DATA_YEAR_MIN:
            return TimeResolution(
                status="out_of_coverage",
                years=tuple(range(start_year, end_year + 1)),
                source="relative",
                phrase=last_n.group(0),
                reason=(
                    f"Resolved range {start_year}-{end_year} starts before "
                    f"warehouse coverage {DATA_YEAR_MIN}"
                ),
            )
        years = tuple(range(start_year, end_year + 1))
        return TimeResolution(
            status="relative_range",
            year=None if len(years) != 1 else years[0],
            years=years,
            start_date=f"{start_year}-01-01",
            end_date=f"{end_year}-12-31",
            source="relative",
            phrase=last_n.group(0),
        )

    # Unparsed relative-looking leftover (e.g. "years ago" without count).
    if re.search(r"\b(?:years?\s+ago|last\s+few\s+years)\b", lower):
        return TimeResolution(
            status="ambiguous",
            source="relative",
            phrase="unparsed relative time",
            reason="Relative time phrase could not be resolved safely",
        )

    return TimeResolution(status="none")


def allowed_years_from_resolution(resolution: TimeResolution) -> set[int]:
    allowed = set(resolution.years)
    if resolution.year is not None:
        allowed.add(resolution.year)
    if resolution.start_date:
        allowed.add(int(resolution.start_date[:4]))
    if resolution.end_date:
        allowed.add(int(resolution.end_date[:4]))
    return allowed


def years_in_arguments(arguments: dict[str, Any]) -> set[int]:
    """Collect calendar years present in tool arguments."""
    years: set[int] = set()
    year = arguments.get("year")
    if isinstance(year, int):
        years.add(year)
    for key in (
        "start_date",
        "end_date",
        "period_a_start",
        "period_a_end",
        "period_b_start",
        "period_b_end",
        "date",
    ):
        value = arguments.get(key)
        if isinstance(value, str) and re.match(r"^20\d{2}", value):
            years.add(int(value[:4]))
    return years


def _allowed_years(time_resolution: dict[str, Any] | None) -> set[int]:
    if not time_resolution:
        return set()
    allowed = set(time_resolution.get("years") or [])
    single = time_resolution.get("year")
    if isinstance(single, int):
        allowed.add(single)
    for key in ("start_date", "end_date"):
        value = time_resolution.get(key)
        if isinstance(value, str) and re.match(r"^20\d{2}", value):
            allowed.add(int(value[:4]))
    return allowed


def _as_day(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _resolved_window(time_resolution: dict[str, Any]) -> tuple[date, date] | None:
    """The harness start and end when the question resolved to a span of days."""
    start = _as_day(time_resolution.get("start_date"))
    end = _as_day(time_resolution.get("end_date"))
    if start is None or end is None or start >= end:
        return None
    return start, end


def _argument_window(
    arguments: dict[str, Any], resolved: tuple[date, date]
) -> tuple[date, date] | None:
    """The window a tool call filters on, from start/end or a bare year."""
    year = arguments.get("year")
    year_start = date(year, 1, 1) if isinstance(year, int) else None
    year_end = date(year, 12, 31) if isinstance(year, int) else None
    start = _as_day(arguments.get("start_date"))
    end = _as_day(arguments.get("end_date"))
    if start is None and end is None:
        if year_start is None:
            return None
        return year_start, year_end
    return start or year_start or resolved[0], end or year_end or resolved[1]


def _hold_resolved_window(
    filled: dict[str, Any],
    time_resolution: dict[str, Any],
    corrections: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Replace a tool window that differs from the resolved span with the span.

    The model may not narrow ``2021 to 2025`` to one year, or ``from January
    2024 up to today`` to one month. A span that is one full calendar year is
    written as ``year=`` so a single-year question stays single-year.
    """
    resolved = _resolved_window(time_resolution)
    if resolved is None:
        return filled
    window = _argument_window(filled, resolved)
    if window is None or window == resolved:
        return filled
    start, end = resolved
    before = {
        key: filled.get(key)
        for key in ("year", "start_date", "end_date", "interval")
        if key in filled
    }
    if start == date(start.year, 1, 1) and end == date(start.year, 12, 31):
        filled["year"] = start.year
        filled.pop("start_date", None)
        filled.pop("end_date", None)
    else:
        filled["start_date"] = start.isoformat()
        filled["end_date"] = end.isoformat()
        harness_year = time_resolution.get("year")
        if isinstance(harness_year, int):
            filled["year"] = harness_year
        else:
            filled.pop("year", None)
            # A weekly series needs one year; a multi-year span reads monthly,
            # the same window rule the router applies.
            if filled.get("kind") == "time_series" and filled.get("interval") in (
                None,
                "weekly",
            ):
                filled["interval"] = "monthly"
    if corrections is not None:
        corrections.append(
            {
                "rule": "hold_resolved_window",
                "resolved": [start.isoformat(), end.isoformat()],
                "requested": before,
                "applied": {
                    key: filled.get(key)
                    for key in ("year", "start_date", "end_date", "interval")
                    if key in filled
                },
            }
        )
    return filled


def apply_harness_years(
    arguments: dict[str, Any],
    *,
    time_resolution: dict[str, Any] | None,
    today: date | None = None,
    hold_window: bool = False,
    corrections: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Override wrong model years with harness years; reject only invented years.

    - Out-of-coverage years always fail (never softened).
    - If the harness resolved year(s) and the model supplies a different year,
      substitute the harness window into the arguments.
    - If the harness resolved no year and the model invents one, reject.
    - When ``time_resolution`` is omitted, only coverage bounds apply.
    - With ``hold_window`` (model tool calls), years inside the resolved span
      may not narrow or reshape it: the call gets the resolved start and end,
      and each correction is appended to ``corrections``.
    """
    filled = dict(arguments)
    found = years_in_arguments(filled)
    ref = today or date.today()
    data_max = ref.year
    for year in found:
        if year < DATA_YEAR_MIN or year > data_max:
            return filled, (
                f"Year {year} is outside warehouse coverage "
                f"{DATA_YEAR_MIN}-{data_max}"
            )
    if time_resolution is None:
        return filled, None
    status = time_resolution.get("status") or "none"
    allowed = _allowed_years(time_resolution)
    if status in {"none", "ambiguous"}:
        if found:
            return filled, (
                "Tool arguments include a calendar year that was not derived "
                "from the question by the harness"
            )
        return filled, None
    if status == "out_of_coverage":
        return filled, (
            time_resolution.get("reason")
            or "Resolved year is outside warehouse coverage"
        )
    if not allowed:
        return filled, None
    if found and found.issubset(allowed):
        if hold_window:
            filled = _hold_resolved_window(filled, time_resolution, corrections)
        # Prefer harness month/window when present and model used a bare year.
        start = time_resolution.get("start_date")
        end = time_resolution.get("end_date")
        if start and end and start == end and filled.get("date"):
            filled["date"] = start
        if (
            start
            and end
            and start[5:] != "01-01"
            and filled.get("year") is not None
            and not filled.get("start_date")
        ):
            filled.pop("year", None)
            filled["start_date"] = start
            filled["end_date"] = end
        return filled, None
    if not found:
        return filled, None

    # Model year disagrees with harness — override rather than reject.
    start = time_resolution.get("start_date")
    end = time_resolution.get("end_date")
    harness_year = time_resolution.get("year")
    if isinstance(harness_year, int):
        if "year" in filled or not (start and end):
            filled["year"] = harness_year
            filled.pop("start_date", None)
            filled.pop("end_date", None)
        if start and end and start[5:] != "01-01":
            filled.pop("year", None)
            filled["start_date"] = start
            filled["end_date"] = end
    elif start and end:
        filled.pop("year", None)
        filled["start_date"] = start
        filled["end_date"] = end
    elif len(allowed) == 1:
        only = next(iter(allowed))
        filled["year"] = only
        filled.pop("start_date", None)
        filled.pop("end_date", None)
    for key in (
        "period_a_start",
        "period_a_end",
        "period_b_start",
        "period_b_end",
        "date",
    ):
        value = filled.get(key)
        if isinstance(value, str) and re.match(r"^20\d{2}", value):
            year = int(value[:4])
            if year not in allowed:
                # Replace year prefix; keep rest of date when possible.
                suffix = value[4:] if len(value) > 4 else "-01-01"
                replacement = next(iter(sorted(allowed)))
                filled[key] = f"{replacement}{suffix}"
    return filled, None


def year_guard_error(
    arguments: dict[str, Any],
    *,
    time_resolution: dict[str, Any] | None,
    today: date | None = None,
) -> str | None:
    """Compatibility wrapper: error only, no argument rewrite."""
    _filled, error = apply_harness_years(
        arguments, time_resolution=time_resolution, today=today
    )
    return error
