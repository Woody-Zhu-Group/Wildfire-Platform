"""Repeated Jev calls, payload-hash checks, and flip-rate tables."""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from services.agent.decisions.backend import QuestionSpec
from services.agent.decisions.canonical import bytes_hash, payload_hash
from services.agent.eval.jev_metrics import INPUT_USD_PER_MILLION


def latest_jsonl(runs: Path) -> Path | None:
    found = sorted(runs.glob("jev_offline_*.jsonl"))
    return found[-1] if found else None


def _questions(payload: dict[str, Any]) -> dict[str, QuestionSpec]:
    return {
        name: QuestionSpec(
            kind=body["type"],
            instructions=body.get("instructions") or "",
            criteria=body.get("criteria"),
        )
        for name, body in payload["questions"].items()
    }


def _choice_answers(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    answers = ((record.get("jev") or {}).get("answers") or {})
    return {
        name: body
        for name, body in answers.items()
        if body.get("kind") == "choice" and body.get("value") is not None
    }


def _noul_answers(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    answers = ((record.get("jev") or {}).get("answers") or {})
    return {
        name: body
        for name, body in answers.items()
        if body.get("kind") == "noul" and isinstance(body.get("value"), (int, float))
    }


def stratify(records: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """Mix high- and low-confidence records, then fill until `count`.

    Confidence is the disposition or tool_pick answer, not the most confident
    question in the payload. A sure county answer would otherwise hide a
    shaky disposition.
    """

    def focus_confidence(record: dict[str, Any]) -> float | None:
        answers = (record.get("jev") or {}).get("answers") or {}
        for name in ("disposition", "tool_pick"):
            body = answers.get(name) or {}
            if body.get("kind") == "choice" and body.get("value") is not None:
                return float(body.get("confidence") or 0)
        return None

    usable = [record for record in records if "request_payload" in record and focus_confidence(record) is not None]
    high, low, rest = [], [], []
    for record in usable:
        confidence = focus_confidence(record) or 0
        if confidence >= 0.8:
            high.append(record)
        elif confidence < 0.65:
            low.append(record)
        else:
            rest.append(record)
    random.seed(0)
    for bucket in (high, low, rest):
        random.shuffle(bucket)
    half = max(1, count // 2)
    picked = high[:half] + low[: count - min(half, len(high))]
    seen = {id(item) for item in picked}
    for record in high[half:] + low + rest:
        if len(picked) >= count:
            break
        if id(record) in seen:
            continue
        picked.append(record)
        seen.add(id(record))
    return picked[:count]


def repeat_test(records: list[dict[str, Any]], backend: Any, *, payloads: int, repeats: int) -> dict[str, Any]:
    chosen = stratify(records, payloads)
    print(f"Repeat test: {len(chosen)} payloads x {repeats} sends, model={backend.model}")
    rows = []
    hash_mismatches = 0
    for record in chosen:
        stored = record["request_payload"]
        stored_hash = payload_hash(stored)
        outcomes: list[dict[str, Any]] = []
        sent_hashes: list[str] = []
        for _ in range(repeats):
            captured: list[bytes] = []
            fresh = backend.evaluate(
                stored["state"],
                _questions(stored),
                request_id=str(record.get("request_id")),
                question_hash=record.get("question_hash") or "",
                capture=captured,
            )
            if captured:
                raw = captured[-1]
                sent_hashes.append(bytes_hash(raw))
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if parsed is not None and payload_hash(parsed) != stored_hash:
                    hash_mismatches += 1
                    print(
                        f"HASH MISMATCH {record.get('request_id')} "
                        f"stored={stored_hash[:12]} sent_canonical={payload_hash(parsed)[:12]}"
                    )
            if fresh and fresh.answers and not fresh.error:
                outcomes.append(
                    {
                        name: {
                            "value": answer.value,
                            "confidence": answer.confidence,
                        }
                        for name, answer in fresh.answers.items()
                    }
                )
        print(
            f"{record.get('request_id')} stored={stored_hash[:12]} "
            f"sent_bytes={sent_hashes[0][:12] if sent_hashes else 'none'} "
            f"sent_unique={len(set(sent_hashes))}"
        )
        summary = _payload_summary(record, outcomes)
        summary["stored_hash"] = stored_hash
        summary["sent_byte_hashes"] = sorted(set(sent_hashes))
        rows.append(summary)
        for question, body in summary["questions"].items():
            print(
                f"  {question}: distinct={body['distinct']} "
                f"spread={body['spread']:.3f} modal_matches_original={body['modal_matches']}"
            )
    table = flip_table(chosen, rows)
    print("\nFlip rate by original confidence")
    print(table)
    return {"rows": rows, "hash_mismatches": hash_mismatches, "table": table, "model": backend.model}


def _payload_summary(record: dict[str, Any], outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    original = (record.get("jev") or {}).get("answers") or {}
    names = set(original)
    for outcome in outcomes:
        names.update(outcome)
    questions = {}
    for name in sorted(names):
        values = [item[name]["value"] for item in outcomes if name in item]
        confidences = [
            float(item[name]["confidence"])
            for item in outcomes
            if name in item and isinstance(item[name].get("confidence"), (int, float))
        ]
        numeric = [float(value) for value in values if isinstance(value, (int, float))]
        distinct_values = len({json.dumps(value, sort_keys=True, default=str) for value in values})
        if numeric:
            sides = {value >= 0.5 for value in numeric}
            distinct = len(sides)
            spread = (max(numeric) - min(numeric)) if len(numeric) >= 2 else 0.0
        else:
            distinct = distinct_values
            spread = (max(confidences) - min(confidences)) if len(confidences) >= 2 else 0.0
        modal = Counter(json.dumps(value, default=str) for value in values).most_common(1)
        original_value = (original.get(name) or {}).get("value")
        questions[name] = {
            "distinct": distinct,
            "spread": spread,
            "modal_matches": bool(modal) and modal[0][0] == json.dumps(original_value, default=str),
        }
    return {"request_id": record.get("request_id"), "questions": questions}


def flip_table(originals: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> str:
    by_id = {row["request_id"]: row for row in summaries}
    buckets = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    noul_buckets = [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.51)]
    choice_counts: dict[tuple[float, float], list[int]] = {bucket: [] for bucket in buckets}
    noul_counts: dict[tuple[float, float], list[int]] = {bucket: [] for bucket in noul_buckets}
    for record in originals:
        summary = by_id.get(record.get("request_id"))
        if not summary:
            continue
        answers = (record.get("jev") or {}).get("answers") or {}
        for name, body in answers.items():
            question = summary["questions"].get(name)
            if not question:
                continue
            flipped = 0 if question["distinct"] <= 1 else 1
            if body.get("kind") == "choice":
                confidence = float(body.get("confidence") or 0)
                for low, high in buckets:
                    if low <= confidence < high:
                        choice_counts[(low, high)].append(flipped)
            elif body.get("kind") == "noul" and isinstance(body.get("value"), (int, float)):
                distance = abs(float(body["value"]) - 0.5)
                for low, high in noul_buckets:
                    if low <= distance < high:
                        noul_counts[(low, high)].append(flipped)
    lines = ["choice confidence | n | flip rate", "---"]
    for low, high in buckets:
        choices = choice_counts[(low, high)]
        rate = (sum(choices) / len(choices)) if choices else 0
        lines.append(f"{low:.1f}-{min(high, 1.0):.1f} | {len(choices)} | {rate:.2f}")
    lines.append("noul distance from 0.5 | n | flip rate")
    for low, high in noul_buckets:
        nouls = noul_counts[(low, high)]
        rate = (sum(nouls) / len(nouls)) if nouls else 0
        lines.append(f"{low:.1f}-{high:.1f} | {len(nouls)} | {rate:.2f}")
    return "\n".join(lines)


def budget_estimate(jobs: int, repeats: int, calls_per_job: int = 1, tokens_per_call: int = 4400) -> str:
    calls = jobs * repeats * calls_per_job
    tokens = calls * tokens_per_call
    cost = tokens / 1_000_000 * INPUT_USD_PER_MILLION
    return (
        f"Budget: {calls} API calls, about {tokens:,} input tokens, "
        f"about ${cost:.4f} at ${INPUT_USD_PER_MILLION}/M input tokens. "
        f"jobs={jobs} repeats={repeats} calls_per_job={calls_per_job}."
    )


def majority_stats(flags: list[bool]) -> dict[str, float]:
    """flags[repeat][case] is flattened as a list of per-repeat accuracy bits for one field."""
    if not flags:
        return {"n": 0, "mean": 0, "stdev": 0, "majority": 0, "stable_correct": 0, "flip_rate": 0}
    return {}


def score_repeats(per_repeat: list[list[bool]]) -> dict[str, Any]:
    """per_repeat is K lists, each the same length, True when that case was correct."""
    if not per_repeat or not per_repeat[0]:
        return {"n": 0, "mean": 0.0, "stdev": 0.0, "majority": 0.0, "stable_correct": 0.0, "flip_rate": 0.0}
    width = len(per_repeat[0])
    accuracies = [sum(row) / width for row in per_repeat]
    modal_correct = 0
    stable = 0
    flips = 0
    for index in range(width):
        votes = [row[index] for row in per_repeat]
        if sum(votes) > len(votes) / 2:
            modal_correct += 1
        if all(votes):
            stable += 1
        if any(votes) and not all(votes):
            flips += 1
    return {
        "n": width,
        "repeats": len(per_repeat),
        "mean": statistics.fmean(accuracies),
        "stdev": statistics.pstdev(accuracies) if len(accuracies) > 1 else 0.0,
        "majority": modal_correct / width,
        "stable_correct": stable / width,
        "flip_rate": flips / width,
    }
