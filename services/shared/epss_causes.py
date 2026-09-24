"""EPSS cause codes and their word forms, applied in SQL and in filters.

The rule and the pairs are ``EPSS_CAUSE_CODE_WORDS`` (defined in
``services.shared.naming``, exported by the registry): a
code and its word form are one cause, filters match both spellings, and
results show the word form.
"""

from __future__ import annotations

from services.shared.dataset_registry import EPSS_CAUSE_CODE_WORDS


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def cause_word(value: str) -> str:
    """The display form of a stored cause (a code becomes its word)."""
    return EPSS_CAUSE_CODE_WORDS.get(value, value)


def cause_variants(value: str) -> list[str]:
    """Every stored spelling of the cause ``value`` names (word form first)."""
    word = cause_word(value)
    codes = sorted(code for code, target in EPSS_CAUSE_CODE_WORDS.items() if target == word)
    return [word, *codes]


def cause_display_sql(column: str) -> str:
    """SQL expression showing ``column`` with each code replaced by its word."""
    whens = " ".join(
        f"WHEN {_quote(code)} THEN {_quote(word)}" for code, word in EPSS_CAUSE_CODE_WORDS.items()
    )
    return f"(CASE {column} {whens} ELSE {column} END)"


def cause_filter_sql(column: str) -> str:
    """WHERE fragment matching any spelling bound to ``%s`` (a list)."""
    return f"{column} = ANY(%s)"
