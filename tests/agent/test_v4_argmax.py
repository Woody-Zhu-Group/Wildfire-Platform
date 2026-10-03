import copy
import gzip
import json

import pytest

from services.agent.decisions.jev_first import decide_from_answers, highest_choices
from services.agent.eval.v4_argmax import CAPTURE, CASES, compare
from services.agent.decisions.canonical import payload_hash
from services.agent.decisions.integrity import answer_to_json
from services.agent.eval.jev_v4 import requests_for
from tests.agent.test_jev_decide import _noul
from tests.agent.test_jev_first import facts


def test_argmax_bypasses_gates_without_faking_confidence():
    answers = facts()
    for answer in answers.values():
        if answer.kind == "choice":
            answer.confidence = 0.1
    original = copy.deepcopy(answers)
    question = "How many PG&E ignitions were there in 2024?"
    assert decide_from_answers(question, answers).rule == "jev_uncertain"
    chosen = decide_from_answers(question, answers, use_confidence=False)
    assert chosen.path == "model"
    assert chosen.slots["jev_decide"]["jev_intent_confidence"] == 0.1
    assert chosen.slots["jev_decide"]["why"] == "argmax"
    assert answers == original


def test_choice_uses_the_highest_probability_not_the_supplied_label():
    answers = facts()
    answers["intent"].probabilities = {"count": 0.1, "compare": 0.9}
    assert highest_choices(answers)["intent"]["value"] == "compare"
    assert answers["intent"].value == "count"


def test_binary_top_choice_uses_half_not_a_zero_threshold():
    answers = facts()
    answers["future_time"] = _noul(0.49)
    assert (
        decide_from_answers(
            "Count PG&E ignitions in 2024", answers, use_confidence=False
        ).path
        == "model"
    )
    answers["future_time"] = _noul(0.51)
    assert (
        decide_from_answers(
            "Count PG&E ignitions in 2024", answers, use_confidence=False
        ).rule
        == "unsupported_future_prediction"
    )


def test_no_confidence_mode_keeps_missing_input_and_backend_checks():
    assert (
        decide_from_answers(
            "How many PG&E ignitions?", facts(), use_confidence=False
        ).rule
        == "records_missing_year"
    )
    assert (
        decide_from_answers(
            "Count ignitions", None, error="timeout", use_confidence=False
        ).path
        == "error"
    )
    answers = facts()
    answers["intent"].probabilities = {}
    with pytest.raises(ValueError):
        highest_choices(answers)
    answers["intent"].probabilities = {"count": 0.0, "compare": 0.0}
    with pytest.raises(ValueError):
        highest_choices(answers)


def test_same_recorded_answers_are_compared_without_touching_the_capture():
    if not CAPTURE.is_file():
        pytest.skip("Historical archive unavailable; set WILDFIRE_EVAL_RUNS_DIR to replay it")
    with gzip.open(CAPTURE, "rt", encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream]
    report = compare(json.loads(CASES.read_text(encoding="utf-8")), records)
    assert report["network_calls"] == 0
    assert report["metrics"]["gated"]["n"] == report["metrics"]["argmax"]["n"] == 180
    assert len(report["rows"]) == 180
    broken = copy.deepcopy(next(row for row in records if row["mode"] == "prior_v4"))
    broken["payload_hash"] = "changed"
    with pytest.raises(ValueError):
        compare(json.loads(CASES.read_text(encoding="utf-8")), [broken])


def test_synthetic_replay_rejects_a_modified_payload_without_an_archive():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    case = cases["cases"][0]
    model = "typesafe/jev-1.13-20260917"
    requests = [call["payload"] for call in requests_for(case["question"], cases["today"], model)]
    record = {"id": case["id"], "mode": "prior_v4", "repeat": 1,
              "question": case["question"], "today": cases["today"], "model": model,
              "requests": requests, "payload_hash": payload_hash(requests), "error": None,
              "answers": {key: answer_to_json(value) for key, value in facts().items()}}
    report = compare(cases, [record])
    assert report["network_calls"] == 0
    assert report["metrics"]["argmax"]["n"] == 1
    record["requests"][0]["state"]["question"] = "changed query"
    with pytest.raises(ValueError, match="mismatched"):
        compare(cases, [record])
