"""Paired offline v4 confidence ablation using the same captured API answers."""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from services.agent.decisions.canonical import payload_hash
from services.agent.decisions.jev_first import decide_from_answers, highest_choices
from services.agent.eval.jev_v4 import requests_for

HERE = Path(__file__).resolve().parent
CAPTURE = HERE / "runs/router_gate_review_20260928/captures.jsonl.gz"
CASES = HERE / "router_gate_cases.json"


def compare(cases: dict, records: list[dict]) -> dict:
    labels = {case["id"]: case for case in cases["cases"]}
    metrics = {
        mode: {"correct": 0, "routes": Counter(), "rules": Counter()}
        for mode in ("gated", "argmax")
    }
    outcomes = defaultdict(set)
    rows = []
    label_changes = choice_ties = all_intent_n = all_intent_correct = 0
    for record in records:
        if record["mode"] != "prior_v4":
            continue
        case = labels[record["id"]]
        expected_payload = [
            call["payload"]
            for call in requests_for(
                record["question"], record["today"], record["model"]
            )
        ]
        if (
            record["error"]
            or case["question"] != record["question"]
            or payload_hash(expected_payload) != record["payload_hash"]
            or payload_hash(record["requests"]) != record["payload_hash"]
        ):
            raise ValueError(
                "Incomplete or mismatched capture; cannot run a paired ablation"
            )
        selected = highest_choices(record["answers"])
        for name, answer in selected.items():
            if answer["kind"] != "choice":
                continue
            label_changes += answer["value"] != record["answers"][name]["value"]
            probabilities = answer["probabilities"]
            choice_ties += (
                sum(p == max(probabilities.values()) for p in probabilities.values())
                > 1
            )
        intents = case["expected"].get("intent")
        if intents:
            all_intent_n += 1
            all_intent_correct += selected["intent"]["value"] in intents
        expected = {
            "answer" if p in {"model", "deterministic"} else p
            for p in case["expected"]["path"]
        }
        row = {
            "id": case["id"],
            "repeat": record["repeat"],
            "question": record["question"],
            "expected": sorted(expected),
        }
        for mode in metrics:
            decision = decide_from_answers(
                record["question"],
                record["answers"],
                today=date.fromisoformat(record["today"]),
                use_confidence=mode == "gated",
            )
            disposition = (
                "answer"
                if decision.path in {"model", "deterministic"}
                else decision.path
            )
            correct = disposition in expected
            metrics[mode]["correct"] += correct
            metrics[mode]["routes"][decision.path] += 1
            metrics[mode]["rules"][decision.rule] += 1
            outcomes[(mode, case["id"])].add((decision.path, decision.rule))
            row[mode] = {
                "path": decision.path,
                "rule": decision.rule,
                "correct": correct,
            }
        rows.append(row)
    for mode, stats in metrics.items():
        stats["n"] = len(rows)
        stats["accuracy"] = stats["correct"] / len(rows) if rows else None
        stats["unstable_cases"] = [
            key
            for (name, key), values in outcomes.items()
            if name == mode and len(values) > 1
        ]
    return {
        "variant": "v4_argmax_without_confidence_gate",
        "network_calls": 0,
        "extra_cost_usd": 0,
        "set_status": cases["set_status"],
        "metrics": metrics,
        "choice_label_changes": label_changes,
        "choice_ties": choice_ties,
        "all_labeled_intent": {"correct": all_intent_correct, "n": all_intent_n},
        "improved": sum(
            not row["gated"]["correct"] and row["argmax"]["correct"] for row in rows
        ),
        "regressed": sum(
            row["gated"]["correct"] and not row["argmax"]["correct"] for row in rows
        ),
        "rows": rows,
        "limitations": "Paired policy replay on existing development captures, not a new live run or final-answer evaluation. Choice ties retain the provider-selected maximum. Binary facts use p >= 0.5. Structural constraints and error handling remain enabled.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, default=CAPTURE)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    with gzip.open(args.captures, "rt", encoding="utf-8") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    report = compare(cases, records)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "rows"}, indent=2
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
