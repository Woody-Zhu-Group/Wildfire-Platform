"""CAL FIRE county matching, where one incident can list several counties.

``wildfire.calfire_incidents.county`` is the incident-map feed's county text.
Most rows name one county, but a fire that crossed a line lists every county
it burned in, comma separated ("Shasta, Tehama", "Butte, Plumas, Shasta,
Lassen, Tehama"). An exact match on that text dropped those incidents from
every county they list. These fragments match and group on the split list
instead, so a multi-county incident counts in each county it names. County
totals can then add up to more than the statewide total, which is why every
county-scoped CAL FIRE result also reports ``multi_county_incidents``.

CPUC ignitions and EPSS outages store one county per row and keep their
exact match.
"""

from __future__ import annotations

_SPLIT = r"'\s*,\s*'"


def county_parts_sql(column: str) -> str:
    """Array of the county names listed in ``column``, trimmed."""
    return f"regexp_split_to_array(BTRIM({column}), {_SPLIT})"


def county_match_sql(column: str) -> str:
    """WHERE fragment true when ``column`` lists the county bound to ``%s``.

    Case-insensitive, like the single-county match it replaces.
    """
    return f"lower(%s) = ANY(regexp_split_to_array(lower(BTRIM({column})), {_SPLIT}))"


def county_overlap_sql(column: str) -> str:
    """WHERE fragment true when ``column`` lists any county in the ``%s`` array.

    The bound array must hold lowercase names.
    """
    return f"regexp_split_to_array(lower(BTRIM({column})), {_SPLIT}) && %s::text[]"


def multi_county_sql(column: str) -> str:
    """Boolean expression true when ``column`` lists more than one county."""
    return f"(strpos(COALESCE({column}, ''), ',') > 0)"


def multi_county_count_sql(column: str) -> str:
    """Aggregate: how many rows in the group list more than one county."""
    return f"COUNT(*) FILTER (WHERE {multi_county_sql(column)})::bigint"


def county_group_join_sql(column: str, *, unknown: str, alias: str = "cty") -> str:
    """LATERAL join yielding one row per listed county as ``{alias}.county``.

    A row with no county yields one ``unknown`` row, so it still counts once.
    """
    return (
        f"CROSS JOIN LATERAL unnest(regexp_split_to_array("
        f"COALESCE(NULLIF(BTRIM({column}), ''), '{unknown}'), {_SPLIT})) AS {alias}(county)"
    )


MULTI_COUNTY_NOTE = (
    "A CAL FIRE incident that lists several counties (for example \"Shasta, "
    "Tehama\") is counted, with its full acreage, in every county it lists, so "
    "county totals can add up to more than the statewide total."
)


def multi_county_meta(count: int) -> dict[str, object]:
    """Meta keys for a county-scoped CAL FIRE result."""
    return {
        "multi_county_incidents": int(count),
        "multi_county_rule": "counted_in_each_listed_county",
    }
