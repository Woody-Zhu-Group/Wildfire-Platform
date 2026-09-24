"""Unsupported-topic patterns: plural forms where the topic is the thing asked
for, and answers for questions that only mention a word in passing.

A pattern written as a stem with a closing word boundary (``fatalit\\b``) or a
singular noun (``cpz\\b``) silently misses "fatalities" or "CPZs", and the
question falls through to the model. That matters only for keys whose words
name the thing being asked for ("CPZs", "satellite images", "fatalities").

Other keys match ordinary words people use in passing ("I'll handle costs
separately", "our budgets", "utility schedules"). Widening those to plurals
refused questions the data answers, so they keep their singular patterns and
are listed in PASSING_MENTION_KEYS instead. A new key must be put in one of
the two groups.
"""

import re

import pytest

from services.agent.routing import UNSUPPORTED, route_question

# Keys whose words are the topic asked for: every listed form must match.
VARIANTS: dict[str, tuple[str, ...]] = {
    "cpz": ("CPZ", "CPZs", "circuit protection zone", "circuit protection zones"),
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
}

# Keys whose words are common in passing mentions. Their patterns are not
# widened; the tests below check that passing mentions are answered.
PASSING_MENTION_KEYS = frozenset({"cost", "leadership", "optimization", "live_web"})

# Each variant inside a question that is otherwise in scope.
QUESTION = "What were the {} for PG&E ignitions in 2020?"


def test_every_unsupported_key_is_in_exactly_one_group():
    assert set(VARIANTS).isdisjoint(PASSING_MENTION_KEYS)
    assert set(VARIANTS) | PASSING_MENTION_KEYS == set(UNSUPPORTED)


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


# A word from a passing-mention key, used in passing, in a question the data
# answers. The first four were refused when those patterns took plurals.
PASSING_MENTIONS = [
    "Show PG&E ignitions in 2022; I'll handle costs separately.",
    "Map CAL FIRE incidents in 2021, not prices.",
    "How many PSPS events were there in 2019 across all utility schedules?",
    "Which counties had the most ignitions in 2022? It feeds our budgets.",
    "Before we look at prices, show me a map of PG&E ignitions in 2022.",
    "The CEOs asked for a map of PG&E ignitions in 2022.",
    "After several web searches, how many EPSS outages did PG&E have in 2024?",
    "How many SCE ignitions were there in 2021? We are optimizing crew routes.",
]


@pytest.mark.parametrize("question", PASSING_MENTIONS)
def test_a_passing_mention_is_still_answered(question):
    decision = route_question(question)
    assert decision.path != "unsupported", (question, decision.rule)
