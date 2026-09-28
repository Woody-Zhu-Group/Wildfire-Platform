"""Budgeted Jev-only comparison: router, v3 decide, prior v4, and router_gate."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from services.agent.decisions import decide_mode, jev_first, router_gate, v4
from services.agent.decisions.canonical import payload_hash
from services.agent.decisions.integrity import answer_to_json, question_hash
from services.agent.decisions.mapping import regex_labels
from services.agent.decisions.shadow import _payload
from services.agent.decisions.typesafe_backend import OpenRouterJevBackend
from services.agent.routing import route_question
from services.agent.eval.jev_metrics import INPUT_USD_PER_MILLION

CASES = Path(__file__).with_name("router_gate_cases.json")
MODEL = "typesafe/jev-1.13-20260917"


def account_usage() -> float:
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/key",
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return float(json.load(response)["data"]["usage"])


def request_calls(case: dict, mode: str, today: str) -> list[dict]:
    proposal = route_question(case["question"])
    return (
        decide_mode.jev_calls(case["question"], today)
        if mode == "decide_v3"
        else v4.calls_for(case["question"], today)
        if mode == "prior_v4"
        else router_gate.calls_for(case["question"], today, proposal)
    )


def capture(case: dict, mode: str, repeat: int, today: str) -> dict:
    calls = request_calls(case, mode, today)
    backend = OpenRouterJevBackend(model=MODEL, timeout_seconds=20)
    record = {
        "id": case["id"],
        "question": case["question"],
        "mode": mode,
        "repeat": repeat,
        "today": today,
        "model": MODEL,
        "requests": [],
        "responses": [],
        "answers": {},
        "input_tokens": 0,
        "error": None,
    }
    start = time.perf_counter()
    for call in calls:
        record["requests"].append(_payload(call["state"], call["questions"], MODEL))
        try:
            result = backend.evaluate(
                call["state"],
                call["questions"],
                request_id=f"{case['id']}:{mode}:{repeat}:{call['name']}",
                question_hash=question_hash(case["question"]),
            )
        except Exception as exc:
            record["error"] = type(exc).__name__
            break
        if result is None or result.error or result.unexpected_option:
            record["error"] = "backend_error"
            break
        record["input_tokens"] += int(result.input_tokens or 0)
        record["responses"].append(result.raw)
        record["answers"].update(
            {name: answer_to_json(value) for name, value in result.answers.items()}
        )
    record["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
    record["payload_hash"] = payload_hash(record["requests"])
    return record


def summarize(cases: dict, records: list[dict]) -> dict:
    by_id = {case["id"]: case for case in cases["cases"]}
    stats = defaultdict(
        lambda: {
            "runs": 0,
            "errors": 0,
            "intent_n": 0,
            "intent_correct": 0,
            "intent_confidences": [],
            "disposition_correct": 0,
            "routes": defaultdict(int),
            "failures": [],
        }
    )
    repeated = defaultdict(set)
    fit = {
        "n": 0,
        "correct": 0,
        "unsafe_accepts": [],
        "unnecessary_rejects": [],
        "confidences": [],
    }
    for record in records:
        case = by_id[record["id"]]
        proposal = route_question(case["question"])
        mode = record["mode"]
        answers = record["answers"]
        if mode == "decide_v3":
            result = decide_mode.decide_from_answers(
                case["question"],
                proposal,
                answers,
                error=record["error"],
                today=date.fromisoformat(record["today"]),
            )
            decision = result.decision
        elif mode == "prior_v4":
            decision = jev_first.decide_from_answers(
                case["question"],
                answers,
                error=record["error"],
                today=date.fromisoformat(record["today"]),
            )
        else:
            result = router_gate.decide_from_answers(
                case["question"],
                proposal,
                answers,
                error=record["error"],
                today=date.fromisoformat(record["today"]),
            )
            decision = result.decision
            reading = answers.get("route", {})
            label = reading.get("value")
            fit["n"] += 1
            fit["correct"] += int(
                label == case["expected_router_fit"] and not record["error"]
            )
            if reading.get("confidence") is not None:
                fit["confidences"].append(reading["confidence"])
            if label != case["expected_router_fit"]:
                target = (
                    "unsafe_accepts" if label == "router" else "unnecessary_rejects"
                )
                fit[target].append(
                    {
                        "id": case["id"],
                        "repeat": record["repeat"],
                        "question": case["question"],
                        "actual": label,
                    }
                )
        item = stats[mode]
        item["runs"] += 1
        item["errors"] += int(bool(record["error"]))
        actual_intent = answers.get("intent", {}).get("value")
        # V3 and gate use the v3 vocabulary. Score intent only on shared labels.
        expected_intent = case["expected"].get("intent")
        if expected_intent and not set(expected_intent) & {
            "model_metrics",
            "risk_surface",
        }:
            item["intent_n"] += 1
            item["intent_correct"] += int(
                actual_intent in expected_intent and not record["error"]
            )
            confidence = answers.get("intent", {}).get("confidence")
            if confidence is not None:
                item["intent_confidences"].append(confidence)
        expected = {
            "answer" if path in {"model", "deterministic"} else path
            for path in case["expected"]["path"]
        }
        actual = (
            "answer" if decision.path in {"model", "deterministic"} else decision.path
        )
        item["disposition_correct"] += int(actual in expected and not record["error"])
        item["routes"][decision.path] += 1
        repeated[(mode, case["id"])].add((actual_intent, decision.path, decision.rule))
        if actual not in expected or record["error"]:
            item["failures"].append(
                {
                    "id": case["id"],
                    "repeat": record["repeat"],
                    "question": case["question"],
                    "path": decision.path,
                    "rule": decision.rule,
                    "expected": sorted(expected),
                }
            )
    router_correct = router_intent_correct = router_intent_n = 0
    for case in cases["cases"]:
        decision = route_question(case["question"])
        expected = {
            "answer" if path in {"model", "deterministic"} else path
            for path in case["expected"]["path"]
        }
        actual = (
            "answer" if decision.path in {"model", "deterministic"} else decision.path
        )
        router_correct += actual in expected
        intents = case["expected"].get("intent")
        if intents and not set(intents) & {"model_metrics", "risk_surface"}:
            router_intent_n += 1
            router_intent_correct += regex_labels(decision).get("intent") in intents
    for mode, item in stats.items():
        confidences = item.pop("intent_confidences")
        item["mean_intent_confidence"] = (
            sum(confidences) / len(confidences) if confidences else None
        )
        item["unstable_cases"] = [
            key
            for (name, key), outcomes in repeated.items()
            if name == mode and len(outcomes) > 1
        ]
    confidences = fit.pop("confidences")
    fit["mean_confidence"] = (
        sum(confidences) / len(confidences) if confidences else None
    )
    return {
        "set_status": cases["set_status"],
        "cases": len(by_id),
        "router": {
            "disposition_correct": router_correct,
            "intent_correct": router_intent_correct,
            "intent_n": router_intent_n,
        },
        "modes": dict(stats),
        "router_fit": fit,
        "caveat": "Pre-execution classification only. An agent handoff is not proof of a correct final answer. Repeats measure consistency, not independent sample size.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument(
        "--replay", type=Path, help="Replay captures.jsonl.gz without API calls"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--cap-usd", type=float)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.replay:
        if args.run or args.output is None:
            parser.error("Replay requires --output and must not use --run")
        by_id = {case["id"]: case for case in cases["cases"]}
        with gzip.open(args.replay, "rt", encoding="utf-8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        for record in records:
            case = by_id[record["id"]]
            expected = [
                _payload(call["state"], call["questions"], MODEL)
                for call in request_calls(case, record["mode"], record["today"])
            ]
            if (
                record["error"]
                or record["question"] != case["question"]
                or record["payload_hash"] != payload_hash(expected)
                or record["payload_hash"] != payload_hash(record["requests"])
            ):
                raise ValueError(
                    "Capture is incomplete or its payload no longer matches; do not reuse it as a fresh result"
                )
        report = summarize(cases, records)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "records": len(records),
                    "network_calls": 0,
                    "router_fit_correct": report["router_fit"]["correct"],
                }
            )
        )
        return 0
    if not args.run:
        print(
            json.dumps(
                {
                    "cases": len(cases["cases"]),
                    "repeats": args.repeats,
                    "max_api_calls": len(cases["cases"]) * args.repeats * 7,
                    "network_calls": 0,
                }
            )
        )
        return 0
    if (
        args.output is None
        or args.cap_usd is None
        or not 0 < args.cap_usd <= 5
        or args.repeats < 1
    ):
        parser.error(
            "Live evaluation requires a new --output directory, positive repeats and --cap-usd in (0, 5]"
        )
    from shared.db import load_env

    load_env()
    initial = account_usage()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "manifest.json").write_text(
        json.dumps(
            {
                "case_file": str(args.cases),
                "case_hash": payload_hash(cases),
                "repeats": args.repeats,
                "model": MODEL,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    records = []
    jobs = [
        (case, mode, repeat)
        for case in cases["cases"]
        for repeat in range(1, args.repeats + 1)
        for mode in ("decide_v3", "prior_v4", "router_gate")
    ]
    stopped = None
    with (
        gzip.open(args.output / "captures.jsonl.gz", "wt", encoding="utf-8") as stream,
        ThreadPoolExecutor(max_workers=4) as pool,
    ):
        for offset in range(0, len(jobs), 4):
            usage = account_usage()
            batch = jobs[offset : offset + 4]
            reserve_tokens = sum(
                len(
                    json.dumps(
                        _payload(call["state"], call["questions"], MODEL)
                    ).encode("utf-8")
                )
                + 4096
                for case, mode, _ in batch
                for call in request_calls(case, mode, cases["today"])
            )
            estimated = (
                (sum(row["input_tokens"] for row in records) + reserve_tokens)
                * INPUT_USD_PER_MILLION
                / 1e6
            )
            if (
                usage - initial >= max(0, args.cap_usd - 0.10)
                or estimated >= args.cap_usd - 0.10
            ):
                stopped = "budget"
                break
            group = list(
                pool.map(
                    lambda job: capture(*job, cases["today"]), jobs[offset : offset + 4]
                )
            )
            for record in group:
                records.append(record)
                stream.write(json.dumps(record) + "\n")
            stream.flush()
            print(f"captured {len(records)}/{len(jobs)}", flush=True)
            if any(record["error"] for record in group):
                stopped = "backend_error"
                break
    report = summarize(cases, records)
    report.update(
        stopped=stopped,
        account_usage_start=initial,
        account_usage_end=account_usage(),
        input_tokens=sum(r["input_tokens"] for r in records),
        estimated_input_cost=sum(r["input_tokens"] for r in records)
        * INPUT_USD_PER_MILLION
        / 1e6,
        model=MODEL,
    )
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "records": len(records),
                "stopped": stopped,
                "account_usage_delta": report["account_usage_end"] - initial,
            }
        )
    )
    return 2 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
