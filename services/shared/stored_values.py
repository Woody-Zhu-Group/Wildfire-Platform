"""Canonical values for free-text filter columns, read from the warehouse.

EPSS ``outage_type`` and ``cause`` and CAL FIRE ``incident_type`` have no
fixed list in the repo; the loaders store whatever the source files carry
("Vegetation", "VEG", "Equipment Failure/Involved", "Wildfire", ...). An
exact-match filter on them returned 0 rows for "vegetation" and the agent
could read that 0 as a real count. Every filter on these columns now resolves
the caller's value against ``SELECT DISTINCT`` from the warehouse: a
case-insensitive, whitespace-insensitive match returns the stored spelling,
and anything else raises UnknownStoredValueError with the closest stored
values.

The distinct values are read once per process on first use and cached (the
warehouse is loaded offline, not written by these services).
``clear_stored_values_cache`` resets the cache after a reload or in tests.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

# field name -> (table, column). The field name is the query parameter.
STORED_VALUE_COLUMNS: dict[str, tuple[str, str]] = {
    "outage_type": ("wildfire.epss_outages", "outage_type"),
    "cause": ("wildfire.epss_outages", "cause"),
    "incident_type": ("wildfire.calfire_incidents", "incident_type"),
}

_cache: dict[str, tuple[str, ...]] = {}


class UnknownStoredValueError(ValueError):
    """A filter value that matches no stored value of its column."""

    def __init__(
        self,
        field: str,
        value: str,
        suggestions: list[str],
        allowed: tuple[str, ...],
        *,
        ambiguous: bool = False,
    ) -> None:
        self.field = field
        self.value = value
        self.suggestions = suggestions
        self.ambiguous = ambiguous
        shown = suggestions or list(allowed)
        hint = f" Did you mean {' or '.join(shown)}?" if shown else ""
        what = (
            f"matches more than one {field} that differ only by case"
            if ambiguous
            else f"it matches no {field} in the warehouse"
        )
        super().__init__(f"unknown {field} {value!r}; {what}.{hint}")


def _key(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def stored_values(conn: Any, field: str) -> tuple[str, ...]:
    """Distinct non-empty stored values for ``field``, cached per process."""
    if field not in STORED_VALUE_COLUMNS:
        raise KeyError(field)
    if field not in _cache:
        table, column = STORED_VALUE_COLUMNS[field]
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT DISTINCT {column} FROM {table} "
                f"WHERE {column} IS NOT NULL AND BTRIM({column}) <> '' ORDER BY 1"
            )
            _cache[field] = tuple(str(row[0]) for row in cur.fetchall())
    return _cache[field]


def set_stored_values(field: str, values: tuple[str, ...] | list[str]) -> None:
    """Seed the cache (tests, or a service that already read the values)."""
    if field not in STORED_VALUE_COLUMNS:
        raise KeyError(field)
    _cache[field] = tuple(values)


def clear_stored_values_cache() -> None:
    _cache.clear()


def stored_value_suggestions(value: str, allowed: tuple[str, ...], *, limit: int = 3) -> list[str]:
    """Closest stored values: fuzzy matches, then prefix or substring hits."""
    key = _key(value)
    if not key:
        return []
    by_key = {_key(item): item for item in allowed}
    found = [by_key[k] for k in difflib.get_close_matches(key, list(by_key), n=limit, cutoff=0.6)]
    compact = re.sub(r"[^a-z0-9]", "", key)
    for item in allowed:
        item_compact = re.sub(r"[^a-z0-9]", "", item.casefold())
        if item in found or not compact or not item_compact:
            continue
        if item_compact.startswith(compact) or compact in item_compact or item_compact in compact:
            found.append(item)
    return found[:limit]


def resolve_stored_value(field: str, value: str, allowed: tuple[str, ...]) -> str:
    """Return the stored spelling of ``value`` or raise UnknownStoredValueError.

    Matching ignores case and repeated or surrounding whitespace only; it
    never maps one stored value onto a different one ("veg" resolves to the
    stored code "VEG"). Folding EPSS cause codes into their word forms is a
    separate rule applied by ``parse_cause`` (see ``epss_causes``).
    """
    key = _key(value)
    matches = [item for item in allowed if _key(item) == key]
    if key and len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Two stored spellings that differ only by case: refuse to pick one.
        raise UnknownStoredValueError(field, value, matches, allowed, ambiguous=True)
    raise UnknownStoredValueError(field, value, stored_value_suggestions(value, allowed), allowed)
