import copy
import json

import pytest

from services.agent.eval import jev_v4
from tests.agent.test_jev_first import facts
from tests.agent.test_jev_decide import FakeBackend


def _case():
    return {
        "id": "example",
        "question": "How did PG&E ignitions change from 2020 to 2023?",
        "expected": {
            "intent": ["compare"],
            "off_topic": ["on_topic"],
            "path": ["model"],
        },
    }


def test_default_command_makes_no_calls(monkeypatch, capsys):
    monkeypatch.setattr(
        jev_v4,
        "make_backend",
        lambda *args, **kwargs: pytest.fail("No backend in plan mode"),
    )
    assert jev_v4.main([]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["network_calls"] == 0 and plan["planned_api_calls"] == 540


def test_live_command_requires_explicit_budgets(tmp_path):
    with pytest.raises(SystemExit) as exc:
        jev_v4.main(["run", "--output", str(tmp_path / "run")])
    assert exc.value.code == 2
    assert not (tmp_path / "run").exists()


def test_capture_and_replay_preserve_the_live_payload_and_decision():
    class RecordingBackend(FakeBackend):
        sent = []

        def evaluate(self, state, questions, **kwargs):
            self.sent.append(jev_v4._payload(state, questions, "test"))
            return super().evaluate(state, questions, **kwargs)

    backend = RecordingBackend(facts("compare"))
    record = jev_v4.capture_case(
        _case(), backend=backend, today="2026-09-27", model="test", repeat=1
    )
    assert backend.calls == 3
    assert len(record["requests"]) == len(record["responses"]) == 3
    assert backend.sent == [request["payload"] for request in record["requests"]]
    decision = jev_v4.replay_record(record)
    assert decision.rule == "jev_intent"
    report = jev_v4.summarize(
        {"cases": [_case()], "set_status": "scripted development fixture"}, [record]
    )
    assert not report["failures"]
    assert report["metrics"]["intent"]["accuracy"] == 1
    assert report["metrics"]["intent"]["mean_confidence"] == pytest.approx(0.95)


@pytest.mark.parametrize("change", ["schema", "hash", "request"])
def test_replay_rejects_old_or_mismatched_payloads(change):
    record = jev_v4.capture_case(
        _case(),
        backend=FakeBackend(facts("compare")),
        today="2026-09-27",
        model="test",
        repeat=1,
    )
    record = copy.deepcopy(record)
    if change == "schema":
        record["schema_version"] = "v3"
    elif change == "hash":
        record["payload_hash"] = "old"
    else:
        record["requests"][0]["payload"]["state"]["question"] = "different"
    with pytest.raises(ValueError):
        jev_v4.replay_record(record)


def test_case_set_has_no_duplicate_questions_or_unknown_expectations():
    case_set = json.loads(jev_v4.CASES.read_text())
    cases = case_set["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["question"] for case in cases}) == len(cases)
    assert "not a clean holdout" in case_set["set_status"]
    assert all(
        set(case["expected"]) <= {"intent", "off_topic", "path"} for case in cases
    )


def test_review_request_example_pins_the_current_payload():
    path = jev_v4.CASES.with_name("jev_v4_request_example.json")
    example = json.loads(path.read_text(encoding="utf-8"))
    assert example["requests"] == jev_v4.requests_for(
        example["question"], example["today"], example["model"]
    )
