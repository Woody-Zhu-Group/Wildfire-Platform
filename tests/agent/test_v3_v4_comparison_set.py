import json
import gzip
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from services.agent.decisions import decide_mode, v4_scope
from services.agent.decisions.integrity import answer_to_json
from services.agent.eval.router_gate_compare import request_calls, summarize
from services.agent.decisions.canonical import payload_hash
from services.agent.decisions.shadow import _payload
from services.agent.eval.router_gate_compare import MODEL, main
from tests.agent.test_jev_decide import _noul
from tests.agent.test_jev_first import facts


HERE = Path(__file__).resolve().parents[2] / "services/agent/eval"


def load_set():
    return json.loads((HERE / "jev_v3_v4_comparison_v1.json").read_text())


def test_set_has_paired_challenges_and_preserves_regression_dispositions():
    dataset = load_set()
    cases = dataset["cases"]
    assert len(cases) == len({c["id"] for c in cases}) == 80
    assert len({c["question"] for c in cases}) == 80
    assert Counter(c["split"] for c in cases) == {"regression": 50, "new_challenge": 30}
    assert set(Counter(c["pair_id"] for c in cases if "pair_id" in c).values()) == {2}
    assert all(c["plan_requirements"] and c["label_rationale"] for c in cases)
    original = json.loads((HERE / "router_gate_cases.json").read_text())["cases"]
    by_id = {c["id"]: c for c in cases}

    def dispositions(case):
        return {
            "answer" if p in {"model", "deterministic"} else p
            for p in case["expected"]["path"]
        }

    for case in original:
        assert by_id[case["id"]]["question"] == case["question"]
        assert dispositions(by_id[case["id"]]) == dispositions(case)


def test_versions_receive_their_own_schema_without_gold_labels():
    dataset = load_set()
    for case in dataset["cases"]:
        assert request_calls(
            case, "decide_v3", dataset["today"]
        ) == decide_mode.jev_calls(case["question"], dataset["today"])
        assert request_calls(
            case, "v4_scope_argmax", dataset["today"]
        ) == v4_scope.calls_for(case["question"], dataset["today"])


def test_latest_policy_and_report_do_not_count_handoff_as_a_verified_plan():
    dataset = load_set()
    case = next(c for c in dataset["cases"] if c["id"] == "cross_01a")
    answers = facts("count")
    del answers["broad_region"]
    answers[v4_scope.SCOPE_FACT] = _noul(0.01)
    for answer in answers.values():
        if answer.kind == "choice":
            answer.confidence = 0.01
    record = {
        "id": case["id"],
        "mode": "v4_scope_argmax",
        "repeat": 1,
        "today": date(2026, 9, 29).isoformat(),
        "error": None,
        "answers": {key: answer_to_json(value) for key, value in answers.items()},
    }
    report = summarize({**dataset, "cases": [case]}, [record])
    assert report["modes"]["v4_scope_argmax"]["disposition_correct"] == 1
    assert report["rows"][0]["path"] == "model"
    assert report["rows"][0]["plan_review"] == "not_executed"
    assert report["by_group"]["v4_scope_argmax:split:new_challenge"] == {
        "n": 1,
        "correct": 1,
    }


def test_v4_only_intents_are_excluded_from_shared_intent_denominator():
    dataset = load_set()
    case = next(c for c in dataset["cases"] if c["id"] == "cross_08a")
    answers = facts("model_metrics")
    del answers["broad_region"]
    answers[v4_scope.SCOPE_FACT] = _noul(0.01)
    record = {
        "id": case["id"],
        "mode": "v4_scope_argmax",
        "repeat": 1,
        "today": dataset["today"],
        "error": None,
        "answers": {key: answer_to_json(value) for key, value in answers.items()},
    }
    report = summarize({**dataset, "cases": [case]}, [record])
    assert report["modes"]["v4_scope_argmax"]["intent_n"] == 0
    assert report["modes"]["v4_scope_argmax"]["runs"] == 1


@pytest.mark.parametrize("duplicate", [False, True])
def test_replay_rejects_unpaired_or_duplicate_results(tmp_path, monkeypatch, duplicate):
    dataset = load_set()
    case = dataset["cases"][0]
    dataset["cases"] = [case]
    case_file = tmp_path / "cases.json"
    case_file.write_text(json.dumps(dataset))
    requests = [
        _payload(call["state"], call["questions"], MODEL)
        for call in request_calls(case, "decide_v3", dataset["today"])
    ]
    record = {
        "id": case["id"],
        "question": case["question"],
        "mode": "decide_v3",
        "repeat": 1,
        "today": dataset["today"],
        "error": None,
        "requests": requests,
        "payload_hash": payload_hash(requests),
    }
    capture_file = tmp_path / "capture.gz"
    with gzip.open(capture_file, "wt", encoding="utf-8") as stream:
        stream.write((json.dumps(record) + "\n") * (2 if duplicate else 1))
    monkeypatch.setattr(
        "sys.argv",
        [
            "compare",
            "--cases",
            str(case_file),
            "--replay",
            str(capture_file),
            "--output",
            str(tmp_path / "report"),
            "--repeats",
            "1",
        ],
    )
    with pytest.raises(ValueError, match="every case/mode/repeat exactly once"):
        main()
    assert not (tmp_path / "report").exists()
