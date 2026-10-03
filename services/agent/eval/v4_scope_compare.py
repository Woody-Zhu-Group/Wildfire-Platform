"""Paired live geography-fact experiment; topic and places are shared per pair."""

from __future__ import annotations

import argparse
import gzip
import json
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from services.agent.decisions import v4, v4_scope
from services.agent.decisions.canonical import payload_hash
from services.agent.decisions.integrity import answer_to_json, question_hash
from services.agent.decisions.jev_first import decide_from_answers
from services.agent.decisions.shadow import _payload
from services.agent.decisions.typesafe_backend import OpenRouterJevBackend
from services.agent.eval.jev_metrics import INPUT_USD_PER_MILLION
from services.agent.eval.router_gate_compare import MODEL, account_usage

HERE = Path(__file__).resolve().parent


def load_cases() -> list[dict]:
    original = json.loads((HERE / "router_gate_cases.json").read_text(encoding="utf-8"))
    controls = json.loads((HERE / "scope_check_cases.json").read_text(encoding="utf-8"))
    return [
        {
            "id": case["id"],
            "question": case["question"],
            "group": "original_36",
            "expected_dispositions": sorted(
                {
                    "answer" if p in {"model", "deterministic"} else p
                    for p in case["expected"]["path"]
                }
            ),
        }
        for case in original["cases"]
    ] + [{**case, "group": "scope_controls_14"} for case in controls["cases"]]


def paired_calls(question: str, today: str) -> dict:
    old = {call["name"]: call for call in v4.calls_for(question, today)}
    new = {call["name"]: call for call in v4_scope.calls_for(question, today)}
    assert old["topic"] == new["topic"] and old["places"] == new["places"]
    return {
        "facts_old": old["facts"],
        "facts_new": new["facts"],
        "topic": old["topic"],
        "places": old["places"],
    }


def capture(case: dict, repeat: int, today: str) -> dict:
    backend = OpenRouterJevBackend(model=MODEL, timeout_seconds=20)
    record = {
        "case": case,
        "repeat": repeat,
        "today": today,
        "model": MODEL,
        "requests": {},
        "responses": {},
        "answers": {},
        "input_tokens": 0,
        "error": None,
    }
    start = time.perf_counter()
    calls = paired_calls(case["question"], today)
    # Alternate order to avoid always collecting the revised facts second.
    names = (
        ["facts_old", "facts_new", "topic", "places"]
        if repeat % 2
        else ["facts_new", "facts_old", "topic", "places"]
    )
    for name in names:
        call = calls[name]
        record["requests"][name] = _payload(call["state"], call["questions"], MODEL)
        try:
            result = backend.evaluate(
                call["state"],
                call["questions"],
                request_id=f"{case['id']}:{repeat}:{name}",
                question_hash=question_hash(case["question"]),
            )
        except Exception as exc:
            record["error"] = type(exc).__name__
            break
        if result is None or result.error or result.unexpected_option:
            record["error"] = "backend_error"
            break
        record["input_tokens"] += int(result.input_tokens or 0)
        record["responses"][name] = result.raw
        record["answers"][name] = {
            key: answer_to_json(value) for key, value in result.answers.items()
        }
    record["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
    record["payload_hash"] = payload_hash(record["requests"])
    return record


def evaluate(records: list[dict]) -> dict:
    metrics = defaultdict(
        lambda: {
            "n": 0,
            "correct": 0,
            "routes": Counter(),
            "rules": Counter(),
            "scope_confidence_sum": 0.0,
            "failures": [],
        }
    )
    scope = {
        "n": 0,
        "correct": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
        "true_negative": 0,
        "confidence_sum": 0.0,
    }
    paired = Counter()
    other_fact_changes = Counter()
    isolated = Counter()
    outcomes = defaultdict(set)
    rows = []
    for record in records:
        case = record["case"]
        expected_payloads = {
            name: _payload(call["state"], call["questions"], record["model"])
            for name, call in paired_calls(case["question"], record["today"]).items()
        }
        if (
            record["error"]
            or payload_hash(expected_payloads) != record["payload_hash"]
            or payload_hash(record["requests"]) != record["payload_hash"]
        ):
            raise ValueError("Incomplete or mismatched paired capture")
        shared = {**record["answers"]["topic"], **record["answers"]["places"]}
        old_facts, new_facts = (
            record["answers"]["facts_old"],
            record["answers"]["facts_new"],
        )
        for name in old_facts.keys() & new_facts.keys():
            if (old_facts[name]["value"] >= 0.5) != (new_facts[name]["value"] >= 0.5):
                other_fact_changes[name] += 1
        row = {
            "id": case["id"],
            "repeat": record["repeat"],
            "question": case["question"],
            "group": case["group"],
            "expected": case["expected_dispositions"],
        }
        for mode, key, fact in (
            ("old", "facts_old", "broad_region"),
            ("new", "facts_new", v4_scope.SCOPE_FACT),
        ):
            answers = {**shared, **record["answers"][key]}
            decision = decide_from_answers(
                case["question"],
                answers,
                today=date.fromisoformat(record["today"]),
                use_confidence=False,
                geography_fact=fact,
            )
            disposition = (
                "answer"
                if decision.path in {"model", "deterministic"}
                else decision.path
            )
            correct = disposition in case["expected_dispositions"]
            row[mode] = {
                "path": decision.path,
                "rule": decision.rule,
                "correct": correct,
                "scope_probability": answers[fact]["value"],
            }
            stats = metrics[f"{case['group']}:{mode}"]
            stats["n"] += 1
            stats["correct"] += correct
            stats["routes"][decision.path] += 1
            stats["rules"][decision.rule] += 1
            probability = answers[fact]["value"]
            stats["scope_confidence_sum"] += max(probability, 1 - probability)
            if not correct:
                stats["failures"].append(
                    {
                        "id": case["id"],
                        "repeat": record["repeat"],
                        "rule": decision.rule,
                    }
                )
            outcomes[(case["group"], mode, case["id"])].add(
                (decision.path, decision.rule)
            )
        paired["improved"] += not row["old"]["correct"] and row["new"]["correct"]
        paired["regressed"] += row["old"]["correct"] and not row["new"]["correct"]
        # Additional offline attribution check, not a separately captured run:
        # keep every old fact except the one whose question was replaced.
        isolated_answers = {
            **shared,
            **{k: v for k, v in old_facts.items() if k != "broad_region"},
            v4_scope.SCOPE_FACT: new_facts[v4_scope.SCOPE_FACT],
        }
        isolated_decision = decide_from_answers(
            case["question"],
            isolated_answers,
            today=date.fromisoformat(record["today"]),
            use_confidence=False,
            geography_fact=v4_scope.SCOPE_FACT,
        )
        isolated_path = (
            "answer"
            if isolated_decision.path in {"model", "deterministic"}
            else isolated_decision.path
        )
        isolated_correct = isolated_path in case["expected_dispositions"]
        isolated["n"] += 1
        isolated["correct"] += isolated_correct
        isolated["improved"] += not row["old"]["correct"] and isolated_correct
        isolated["regressed"] += row["old"]["correct"] and not isolated_correct
        if "expected_scope_missing" in case:
            probability = row["new"]["scope_probability"]
            predicted = probability >= 0.5
            expected = case["expected_scope_missing"]
            scope["n"] += 1
            scope["correct"] += predicted == expected
            scope["confidence_sum"] += max(probability, 1 - probability)
            scope[
                ("true_" if predicted == expected else "false_")
                + ("positive" if predicted else "negative")
            ] += 1
        rows.append(row)
    for name, stats in metrics.items():
        group, mode = name.split(":")
        stats["accuracy"] = stats["correct"] / stats["n"]
        stats["mean_scope_confidence"] = stats.pop("scope_confidence_sum") / stats["n"]
        stats["unstable_cases"] = [
            case_id
            for (g, m, case_id), values in outcomes.items()
            if g == group and m == mode and len(values) > 1
        ]
    scope["mean_confidence"] = (
        scope.pop("confidence_sum") / scope["n"] if scope["n"] else None
    )
    return {
        "metrics": dict(metrics),
        "paired_changes": dict(paired),
        "unchanged_question_label_flips": dict(other_fact_changes),
        "offline_scope_only_swap": dict(isolated),
        "missing_scope_controls": scope,
        "rows": rows,
        "method": "Fresh paired facts calls with identical shared topic/places answers; argmax without confidence rejection. Development data, not an independent holdout or final-answer evaluation.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cap-usd", type=float)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    cases = load_cases()
    if args.replay:
        if args.run or args.output is None:
            parser.error("Replay needs --output and no --run")
        with gzip.open(args.replay, "rt", encoding="utf-8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        report = evaluate(records)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps({"paired_records": len(records), "network_calls": 0}))
        return 0
    if not args.run:
        print(
            json.dumps(
                {
                    "network_calls": 0,
                    "questions": len(cases),
                    "repeats": args.repeats,
                    "max_api_calls": len(cases) * args.repeats * 4,
                }
            )
        )
        return 0
    if (
        args.output is None
        or args.cap_usd is None
        or not 0.1 < args.cap_usd <= 1
        or args.repeats < 1
    ):
        parser.error(
            "Use a new --output directory, positive repeats and --cap-usd in (0.1, 1]"
        )
    from shared.db import load_env

    load_env()
    today = date.today().isoformat()
    initial = account_usage()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "case_hash": payload_hash(cases),
        "today": today,
        "model": MODEL,
        "repeats": args.repeats,
        "new_question": v4_scope.SCOPE_INSTRUCTIONS,
        "schema_version": v4_scope.SCHEMA_VERSION,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    jobs = [
        (case, repeat, today) for case in cases for repeat in range(1, args.repeats + 1)
    ]
    records = []
    stopped = None
    with (
        gzip.open(args.output / "captures.jsonl.gz", "wt", encoding="utf-8") as stream,
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        for offset in range(0, len(jobs), 4):
            batch = jobs[offset : offset + 4]
            reserve = sum(
                len(
                    json.dumps(
                        _payload(call["state"], call["questions"], MODEL)
                    ).encode()
                )
                + 4096
                for case, _, day in batch
                for call in paired_calls(case["question"], day).values()
            )
            estimate = (
                (sum(row["input_tokens"] for row in records) + reserve)
                * INPUT_USD_PER_MILLION
                / 1e6
            )
            if (
                account_usage() - initial >= args.cap_usd - 0.1
                or estimate >= args.cap_usd - 0.1
            ):
                stopped = "budget"
                break
            group = list(pool.map(lambda job: capture(*job), batch))
            for record in group:
                records.append(record)
                stream.write(json.dumps(record) + "\n")
            stream.flush()
            print(f"captured {len(records)}/{len(jobs)} pairs", flush=True)
            if any(record["error"] for record in group):
                stopped = "backend_error"
                break
    if stopped:
        report = {"stopped": stopped, "completed_records": len(records)}
    else:
        report = evaluate(records)
    report.update(
        stopped=stopped,
        account_usage_start=initial,
        account_usage_end=account_usage(),
        input_tokens=sum(row["input_tokens"] for row in records),
        estimated_input_cost=sum(row["input_tokens"] for row in records)
        * INPUT_USD_PER_MILLION
        / 1e6,
    )
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "pairs": len(records),
                "stopped": stopped,
                "estimated_input_cost": report["estimated_input_cost"],
            }
        )
    )
    return 2 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
