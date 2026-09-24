"""Read the production Jev shadow log and report agreement, not accuracy."""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from services.agent.decisions.backend import QuestionSpec
from services.agent.decisions.integrity import question_hash, replay_mismatch
from services.agent.decisions.shadow_log import ShadowLog, resolve_log_path
from services.agent.eval.jev_metrics import (
    categorized_summary,
    check_record_parse,
    choice_confidence,
    choice_value,
    latency_line,
    parse_stats,
    rate,
    sanity_lines,
    write_review_csv,
)


def load_log(path: str) -> list[dict[str, Any]]:
    log = ShadowLog(path, max_bytes=1)
    return log.read_records()


def _since(record: dict[str, Any], stamp: str | None) -> bool:
    if not stamp:
        return True
    return str(record.get("ts") or "") >= stamp


def join_tool_outcomes(
    records: list[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int]:
    picks = {
        record.get("request_id"): record
        for record in records
        if record.get("type") == "tool_pick"
    }
    outcomes = {
        record.get("request_id"): record
        for record in records
        if record.get("type") == "outcome"
    }
    joined = []
    orphans = 0
    ids = set(picks) | set(outcomes)
    for request_id in ids:
        pick = picks.get(request_id)
        outcome = outcomes.get(request_id)
        if pick is None or outcome is None:
            orphans += 1
            continue
        if pick.get("question_hash") != outcome.get("question_hash"):
            orphans += 1
            continue
        if pick.get("question_hash") != question_hash(str(pick.get("question") or "")):
            orphans += 1
            continue
        joined.append((pick, outcome))
    return joined, orphans


def render(records: list[dict[str, Any]], args: argparse.Namespace) -> list[str]:
    filtered = [
        record
        for record in records
        if _since(record, args.since)
        and (not args.rule or ((record.get("regex") or {}).get("rule") == args.rule))
    ]
    stats = parse_stats(filtered)
    _joined, orphans = join_tool_outcomes(filtered)
    stats["orphaned_joins"] = orphans
    stats["replay_mismatches"] = "not run"
    dropped = sum(1 for record in filtered if record.get("type") == "dropped")
    errors = sum(
        1
        for record in filtered
        if record.get("type") in {"routing", "tool_pick"} and record.get("error")
    )
    timeouts = sum(
        1
        for record in filtered
        if "TimeoutError" in str(record.get("error") or "")
    )
    routing = [record for record in filtered if record.get("type") == "routing"]
    lines = sanity_lines(stats)
    lines.append(f"- shadowed questions: {len(routing)}")
    lines.append(f"- Jev errors: {errors}")
    lines.append(f"- timeouts: {timeouts}")
    lines.append(f"- dropped jobs: {dropped}")
    lines.append(
        "Agreement below is regex versus Jev. There is no ground truth in this log, so this is not accuracy."
    )
    comparable = [
        record
        for record in routing
        if record.get("agree") and (record.get("jev") or {}).get("answers")
    ]
    if args.disagreements_only:
        comparable = [
            record
            for record in comparable
            if not all((record.get("agree") or {}).values())
        ]
    for field in ("disposition", "intent", "dataset", "utilities", "county"):
        scored = [
            record
            for record in comparable
            if isinstance((record.get("agree") or {}).get(field), bool)
        ]
        hits = sum(1 for record in scored if record["agree"][field])
        lines.append(f"- agreement {field} {rate(hits, len(scored))}")
    by_rule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in comparable:
        by_rule[((record.get("regex") or {}).get("rule")) or "(none)"].append(record)
    lines.append("Agreement by regex rule (intent)")
    for rule, rows in sorted(by_rule.items()):
        hits = sum(1 for record in rows if record["agree"].get("intent"))
        lines.append(f"- {rule} {rate(hits, len(rows))}")
    disagreements = []
    for record in comparable:
        if all(record["agree"].values()):
            continue
        confidence = choice_confidence(record, "intent") or 0
        if args.min_confidence and confidence < args.min_confidence:
            continue
        disagreements.append(record)
    disagreements.sort(
        key=lambda record: choice_confidence(record, "intent") or 0,
        reverse=True,
    )
    lines.append("High-confidence disagreements first")
    for record in disagreements:
        answers = (record.get("jev") or {}).get("answers") or {}
        intent = answers.get("intent") or {}
        lines.append(
            "- "
            + json.dumps(
                {
                    "question": record.get("question"),
                    "regex": (record.get("regex") or {}).get("labels"),
                    "jev_intent": intent.get("value"),
                    "probabilities": intent.get("probabilities"),
                    "agree": record.get("agree"),
                },
                default=str,
            )
        )
    joined, _orphans = join_tool_outcomes(filtered)
    matched_first = 0
    matched_final = 0
    elapsed = []
    scored = 0
    for pick, outcome in joined:
        if not (pick.get("jev") or {}).get("answers"):
            continue
        scored += 1
        picked = choice_value(pick, "tool_pick")
        first = outcome.get("model_first_tools") or []
        final = outcome.get("model_final_tools") or []
        if picked and picked in first:
            matched_first += 1
        if picked and picked in final:
            matched_final += 1
        if outcome.get("elapsed_ms") is not None:
            elapsed.append(float(outcome["elapsed_ms"]))
    lines.append(
        f"- tool_pick matches qwen3 first tools {rate(matched_first, scored)}; "
        f"final tools {rate(matched_final, scored)}"
    )
    lines.append("- qwen3 elapsed on those questions " + latency_line(elapsed))
    latencies = [
        float((record.get("jev") or {}).get("latency_ms"))
        for record in routing
        if (record.get("jev") or {}).get("latency_ms") is not None
    ]
    tokens = sum(
        int((record.get("jev") or {}).get("input_tokens") or 0) for record in routing
    )
    lines.append("Jev latency " + latency_line(latencies))
    lines.append(f"Jev input tokens {tokens}")
    versions = Counter(record.get("model_version") for record in filtered if record.get("model_version"))
    lines.append(f"Model versions {dict(versions)}")
    if len(versions) > 1:
        lines.append("Metrics are not split per version in v1 beyond the counts above; do not pool them.")
    return lines, disagreements


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log",
        default=os.environ.get(
            "AGENT_JEV_LOG_PATH", "services/agent/logs/jev_shadow.jsonl"
        ),
    )
    parser.add_argument("--since", default="")
    parser.add_argument("--rule", default="")
    parser.add_argument("--disagreements-only", action="store_true")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--out", default="")
    parser.add_argument("--check-parse", action="store_true")
    parser.add_argument("--replay", type=int, default=0)
    parser.add_argument("--export-payloads", type=int, default=0)
    parser.add_argument("--categorized", default="")
    args = parser.parse_args()
    path = resolve_log_path(args.log)
    if not path.exists() and not list(path.parent.glob(path.name + ".*")):
        print(f"No shadow log at {path}")
        stats = {
            "records": 0,
            "null_answers": 0,
            "parse_mismatches": 0,
            "unexpected_options": 0,
            "wiring_errors": 0,
            "orphaned_joins": 0,
            "replay_mismatches": "not run",
            "model_versions": {},
        }
        print("\n".join(sanity_lines(stats)))
        return 0
    records = load_log(args.log)
    lines, disagreements = render(records, args)
    if args.check_parse:
        details = [check_record_parse(record) for record in records]
        details = [item for item in details if item]
        if details:
            lines.insert(0, f"RESULTS NOT TRUSTWORTHY: {len(details)} parse mismatches")
            lines.extend(f"- parse {item}" for item in details)
        else:
            lines.append("Parse check: 0 mismatches")
    if args.replay:
        problems = _replay(records, args.replay)
        if problems:
            lines.insert(0, f"RESULTS NOT TRUSTWORTHY: {len(problems)} replay mismatches")
            lines.extend(f"- replay {item}" for item in problems)
        else:
            lines.append(f"Replay mismatches: 0 (n={args.replay})")
    if args.categorized:
        lines.extend(categorized_summary(Path(args.categorized)))
    text = "\n".join(lines)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    review = []
    for record in disagreements:
        review.append(
            {
                "request_id": record.get("request_id"),
                "source": "production",
                "question": record.get("question"),
                "expected": "",
                "regex": json.dumps((record.get("regex") or {}).get("labels"), default=str),
                "jev": choice_value(record, "intent") or "",
                "jev_confidence": choice_confidence(record, "intent") or "",
                "jev_probabilities": json.dumps(
                    ((record.get("jev") or {}).get("answers") or {}).get("intent", {}).get("probabilities")
                ),
                "category": "",
                "notes": "",
            }
        )
    if args.out:
        write_review_csv(Path(args.out).with_suffix(".csv"), review)
    if args.export_payloads:
        _export(records, args.export_payloads, Path(args.log).with_name("jev_shadow_curl.sh"))
    return 0


def _replay(records: list[dict[str, Any]], count: int) -> list[str]:
    if not (os.environ.get("TYPESAFE_API_KEY") or "").strip():
        print("TYPESAFE_API_KEY is not set. Replay skipped.")
        return []
    from services.agent.decisions.typesafe_backend import TypeSafeBackend

    pool = [record for record in records if (record.get("jev") or {}).get("raw")]
    if not pool:
        print("Replay skipped: log has no raw responses.")
        return []
    chosen = random.sample(pool, k=min(count, len(pool)))
    backend = TypeSafeBackend(timeout_seconds=30)
    problems = []
    print("request_id | stored | fresh | max_prob_delta")
    for record in chosen:
        payload = record["request_payload"]
        fresh = backend.evaluate(
            payload["state"],
            {
                name: QuestionSpec(
                    kind=body["type"],
                    instructions=body.get("instructions") or "",
                    criteria=body.get("criteria"),
                )
                for name, body in payload["questions"].items()
            },
            request_id=str(record.get("request_id")),
            question_hash=str(record.get("question_hash")),
        )
        if fresh is None or fresh.error:
            problems.append(f"{record.get('request_id')}: {None if fresh is None else fresh.error}")
            continue
        fresh_record = {
            "jev": {
                "answers": {
                    name: {
                        "kind": answer.kind,
                        "value": answer.value,
                        "confidence": answer.confidence,
                        "probabilities": answer.probabilities,
                    }
                    for name, answer in fresh.answers.items()
                }
            }
        }
        delta = replay_mismatch(record, fresh_record)
        print(f"{record.get('request_id')} | stored | fresh | {'; '.join(delta) or '0'}")
        problems.extend(delta)
    return problems


def _export(records: list[dict[str, Any]], count: int, path: Path) -> None:
    lines = ["# API key is $TYPESAFE_API_KEY. It is not written here."]
    exported = 0
    for record in records:
        payload = record.get("request_payload")
        if not payload:
            continue
        body = json.dumps(payload)
        lines.append(
            "curl https://api.typesafe.ai/v1/systemone "
            "-H \"Authorization: Bearer $TYPESAFE_API_KEY\" "
            "-H \"Content-Type: application/json\" "
            f"-d {json.dumps(body)}"
        )
        exported += 1
        if exported >= count:
            break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
