"""Every unsupported-topic pattern matches the plural and common variant forms.

A pattern written as a stem with a closing word boundary (``fatalit\\b``) or a
singular noun (``cost\\b``) silently misses "fatalities" or "costs", and the
question falls through to the model. Each key lists the forms people use; each
form must match its own key's pattern, and the whole question must route to
that key's refusal. A new key without variants fails the coverage test.
"""

import re

import pytest

from services.agent.routing import UNSUPPORTED, route_question

VARIANTS: dict[str, tuple[str, ...]] = {
    "cpz": ("CPZ", "CPZs", "circuit protection zone", "circuit protection zones"),
    "cost": (
        "cost",
        "costs",
        "price",
        "prices",
        "budget",
        "budgets",
        "dollar",
        "dollars",
        "economic",
        "economics",
        "premium",
        "premiums",
    ),
    "air_quality": ("air quality",),
    "evacuation": ("evacuation", "evacuations", "evacuated", "evacuate", "evacuating"),
    "translation": ("translate this answer", "translated into Spanish", "Spanish translation"),
    "personnel": (
        "how many firefighters",
        "number of firefighters",
        "firefighters deployed",
        "firefighter was assigned",
        "personnel",
    ),
    "satellite": ("satellite image", "satellite images", "satellite imagery", "satellite photos"),
    "leadership": ("CEO", "CEOs", "chief executive", "chief executives"),
    "optimization": (
        "optimize",
        "optimise",
        "optimized",
        "optimizing",
        "optimization",
        "optimal",
        "schedule",
        "schedules",
        "allocate",
        "allocating",
        "allocation",
    ),
    "damage": (
        "property damage",
        "property damages",
        "expected loss",
        "expected losses",
        "insured loss",
        "insured losses",
        "fatality",
        "fatalities",
    ),
    "live_web": ("live fire", "live fires", "web search", "web searches", "today's fires"),
}

# Each variant inside a question that is otherwise in scope.
QUESTION = "What were the {} for PG&E ignitions in 2020?"


def test_every_unsupported_key_lists_its_variants():
    assert set(VARIANTS) == set(UNSUPPORTED)


@pytest.mark.parametrize(
    "key,variant",
    [(key, variant) for key, variants in VARIANTS.items() for variant in variants],
)
def test_the_pattern_matches_each_variant(key, variant):
    assert re.search(UNSUPPORTED[key], variant.lower(), re.I), (key, variant)


@pytest.mark.parametrize(
    "key,variant",
    [(key, variant) for key, variants in VARIANTS.items() for variant in variants],
)
def test_a_question_with_the_variant_is_refused_by_that_key(key, variant):
    decision = route_question(QUESTION.format(variant))
    assert decision.path == "unsupported", (variant, decision.path, decision.rule)
    assert decision.rule == f"unsupported_{key}", (variant, decision.rule)
