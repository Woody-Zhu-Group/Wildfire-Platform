"""The three acceptable-label rules. Original gold stays in the files."""

from services.agent.eval.label_rules import (
    clarify_alternatives,
    fragment_without_verb,
    intent_alternatives,
    separate_count_question,
    tool_alternatives,
)


def test_two_gaps_accept_either_clarify_reason():
    question = "Show ignitions around the town during summer"
    allowed = clarify_alternatives(question, "undefined_spatial_scope")
    assert allowed is not None
    assert "undefined_spatial_scope" in allowed
    assert "records_missing_year" in allowed
    assert clarify_alternatives("How many PGE ignitions in 2024?", "records_missing_year") is None


def test_separate_counts_also_accept_records():
    question = "How many ignitions in 2018, and how many in 2020?"
    assert separate_count_question(question)
    assert tool_alternatives(question, "comparison_run") == [
        "comparison_run",
        "data_query_records",
    ]
    assert tool_alternatives("How many PGE ignitions in 2024?", "data_query_records") is None


def test_verbless_fragment_accepts_count_or_list():
    question = "CAL FIRE incidents in Kern County for 2019"
    assert fragment_without_verb(question)
    assert intent_alternatives(question, "count") == ["count", "records_list"]
    assert not fragment_without_verb("How many CAL FIRE incidents in Kern County in 2019?")
