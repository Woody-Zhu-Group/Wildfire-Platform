"""Plan, capture, and replay Jev-only v4 evaluations. Default: no network."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path

from services.agent.config import OPENROUTER_DEFAULT_JEV_MODEL
from services.agent.decisions.canonical import payload_hash
from services.agent.decisions.integrity import answer_to_json, question_hash
from services.agent.decisions.jev_first import decide_from_answers, _confidence, _value
from services.agent.decisions.shadow import _payload
from services.agent.decisions.typesafe_backend import make_backend
from services.agent.decisions.v4 import SCHEMA_VERSION, calls_for
from services.agent.eval.jev_metrics import INPUT_USD_PER_MILLION

CASES = Path(__file__).with_name("jev_v4_cases.json")


def requests_for(question: str, today: str, model: str) -> list[dict]:
    return [
        {
            "name": call["name"],
            "payload": _payload(call["state"], call["questions"], model),
        }
        for call in calls_for(question, today)
    ]


def capture_case(case: dict, *, backend, today: str, model: str, repeat: int) -> dict:
    calls = calls_for(case["question"], today)
    record = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case["id"],
        "repeat": repeat,
        "question": case["question"],
        "today": today,
        "model": model,
        "requests": requests_for(case["question"], today, model),
        "responses": [],
        "answers": {},
        "error": None,
        "input_tokens": 0,
    }
    for call in calls:
        try:
            result = backend.evaluate(
                call["state"],
                call["questions"],
                request_id=f"{case['id']}:{repeat}:{call['name']}",
                question_hash=question_hash(case["question"]),
            )
        except (
            Exception
        ) as exc:  # External SDK boundary; never record credentials in exception text.
            record["error"] = type(exc).__name__
            break
        if result is None:
            record["error"] = "backend_disabled"
            break
        record["input_tokens"] += int(result.input_tokens or 0)
        record["responses"].append(
            {
                "name": call["name"],
                "raw": result.raw,
                "model_version": result.model_version,
            }
        )
        if result.error or result.unexpected_option:
            record["error"] = "backend_error" if result.error else "unexpected_option"
            break
        record["answers"].update(
            {name: answer_to_json(answer) for name, answer in result.answers.items()}
        )
    record["payload_hash"] = payload_hash(record["requests"])
    return record


def replay_record(record: dict):
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            "A fresh v4 capture is required; v3 answers cannot validate v4."
        )
    expected = requests_for(record["question"], record["today"], record["model"])
    if (
        record.get("payload_hash") != payload_hash(expected)
        or record.get("requests") != expected
    ):
        raise ValueError("Stored requests do not match the current v4 payload.")
    return decide_from_answers(
        record["question"],
        record.get("answers"),
        error=record.get("error"),
        today=date.fromisoformat(record["today"]),
    )


def summarize(cases: dict, records: list[dict]) -> dict:
    by_id = {case["id"]: case for case in cases["cases"]}
    fields = defaultdict(lambda: {"correct": 0, "total": 0, "confidences": []})
    outcomes = defaultdict(set)
    failures = []
    decisions = []
    for record in records:
        case = by_id[record["case_id"]]
        if case["question"] != record["question"]:
            raise ValueError("Case text changed since capture")
        decision = replay_record(record)
        answers = record["answers"]
        actual = {
            "path": decision.path,
            **{name: _value(answers, name) for name in ("intent", "off_topic")},
        }
        wrong = []
        for name, accepted in case["expected"].items():
            stats = fields[name]
            stats["total"] += 1
            correct = actual[name] in accepted and not record.get("error")
            stats["correct"] += int(correct)
            if name != "path" and name in answers:
                stats["confidences"].append(_confidence(answers, name))
            if not correct:
                wrong.append(name)
        outcomes[case["id"]].add(
            (actual["intent"], actual["off_topic"], decision.path, decision.rule)
        )
        detail = {
            "id": case["id"],
            "repeat": record["repeat"],
            "question": case["question"],
            "actual": actual,
            "rule": decision.rule,
            "candidate_tools": decision.slots.get("candidate_tools", []),
            "planned_calls": decision.tool_calls,
            "text": decision.answer,
            "error": record.get("error"),
        }
        decisions.append(detail)
        if wrong:
            failures.append(
                {**detail, "wrong_fields": wrong, "expected": case["expected"]}
            )
    metrics = {}
    for name, stats in fields.items():
        scores = stats.pop("confidences")
        metrics[name] = {
            **stats,
            "accuracy": stats["correct"] / stats["total"],
            "mean_confidence": sum(scores) / len(scores) if scores else None,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "set_status": cases["set_status"],
        "records": len(records),
        "cases_run": len(outcomes),
        "metrics": metrics,
        "unstable_cases": [key for key, values in outcomes.items() if len(values) > 1],
        "failures": failures,
        "decisions": decisions,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=("plan", "run", "replay"), default="plan"
    )
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument(
        "--backend", choices=("typesafe", "openrouter"), default="typesafe"
    )
    parser.add_argument("--model")
    parser.add_argument("--cap-usd", type=float)
    parser.add_argument("--max-calls", type=int)
    parser.add_argument(
        "--input-usd-per-million",
        type=float,
        default=INPUT_USD_PER_MILLION,
        help="Configured cost estimate, not a provider billing guarantee; verify the rate before running.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="New output directory; existing directories are never overwritten",
    )
    parser.add_argument(
        "--responses", type=Path, help="responses.jsonl from a v4 capture"
    )
    args = parser.parse_args(argv)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if cases.get("schema_version") != SCHEMA_VERSION or args.repeats < 1:
        parser.error("Use a v4 case set and a positive repeat count")
    model = args.model or (
        OPENROUTER_DEFAULT_JEV_MODEL if args.backend == "openrouter" else "jev-latest"
    )
    planned = len(cases["cases"]) * args.repeats * 3
    if args.command == "plan":
        print(
            json.dumps(
                {
                    "network_calls": 0,
                    "cases": len(cases["cases"]),
                    "repeats": args.repeats,
                    "planned_api_calls": planned,
                    "set_status": cases["set_status"],
                },
                indent=2,
            )
        )
        return 0
    if args.output is None:
        parser.error("--output must name a new directory")
    if args.command == "run" and (
        not args.cap_usd
        or args.cap_usd <= 0
        or not args.max_calls
        or args.max_calls <= 0
    ):
        parser.error("Live calls require explicit --cap-usd and --max-calls budgets")
    if args.command == "replay" and args.responses is None:
        parser.error("Replay requires --responses")
    if args.input_usd_per_million <= 0:
        parser.error("The configured cost rate must be positive")
    if args.command == "run":
        from shared.db import load_env

        load_env()
        key = "TYPESAFE_API_KEY" if args.backend == "typesafe" else "OPENROUTER_API_KEY"
        if not os.environ.get(key, "").strip():
            parser.error(
                f"Set {key} in the environment or repo .env; no calls were made"
            )
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    stopped = None
    tokens = used_calls = 0
    if args.command == "replay":
        records = [
            json.loads(line)
            for line in args.responses.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        backend = make_backend(args.backend, model=model, timeout_seconds=30)
        with (args.output / "responses.jsonl").open("w", encoding="utf-8") as stream:
            for case in cases["cases"]:
                for repeat in range(1, args.repeats + 1):
                    requests = requests_for(case["question"], cases["today"], model)
                    # Reserve a conservative input estimate before each three-call group.
                    reserve = len(json.dumps(requests).encode("utf-8")) + 4096
                    if (
                        used_calls + 3 > args.max_calls
                        or (tokens + reserve) * args.input_usd_per_million / 1e6
                        > args.cap_usd
                    ):
                        stopped = "budget"
                        break
                    record = capture_case(
                        case,
                        backend=backend,
                        today=cases["today"],
                        model=model,
                        repeat=repeat,
                    )
                    records.append(record)
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                    used_calls += (
                        3  # Includes a reserved group that stopped on an error.
                    )
                    tokens += record["input_tokens"]
                    if record["error"]:
                        stopped = "backend_error"
                        break
                if stopped:
                    break
    report = summarize(cases, records)
    report.update(
        stopped=stopped,
        input_tokens=sum(row["input_tokens"] for row in records),
        configured_input_usd_per_million=args.input_usd_per_million,
    )
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"failures", "decisions"}
            },
            indent=2,
        )
    )
    return 2 if stopped or report["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
