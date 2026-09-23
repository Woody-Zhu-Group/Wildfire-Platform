"""Shared scoring and sanity checks for the Jev offline eval and shadow report."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from services.agent.decisions.backend import QuestionSpec
from services.agent.decisions.integrity import (
    answer_to_json,
    auroc,
    parse_raw_answers,
    percentile,
)

INPUT_USD_PER_MILLION = 0.042


def sanity_lines(stats: dict[str, Any]) -> list[str]:
    lines = [
        "Sanity",
        f"- total records: {stats.get('records', 0)}",
        f"- null answers: {stats.get('null_answers', 0)}",
        f"- parse mismatches: {stats.get('parse_mismatches', 0)}",
        f"- unexpected option ids: {stats.get('unexpected_options', 0)}",
        f"- wiring errors: {stats.get('wiring_errors', 0)}",
        f"- orphaned joins: {stats.get('orphaned_joins', 0)}",
        f"- unmapped rules: {stats.get('unmapped_rules', 0)}",
        f"- replay mismatches: {stats.get('replay_mismatches', 'not run')}",
        f"- model versions: {stats.get('model_versions', {})}",
    ]
    bad = []
    if stats.get("parse_mismatches"):
        bad.append(f"{stats['parse_mismatches']} parse mismatches")
    if stats.get("wiring_errors"):
        bad.append(f"{stats['wiring_errors']} wiring errors")
    replay = stats.get("replay_mismatches")
    if isinstance(replay, int) and replay:
        bad.append(f"{replay} replay mismatches")
    if bad:
        lines.insert(0, "RESULTS NOT TRUSTWORTHY: " + "; ".join(bad))
    return lines


def check_record_parse(record: dict[str, Any]) -> str | None:
    """Return a mismatch description, or None when parsed answers match raw."""
    if record.get("type") not in {"routing", "tool_pick"}:
        return None
    jev = record.get("jev")
    if not jev:
        return None
    raw = jev.get("raw") or {}
    payload = (record.get("request_payload") or {}).get("questions") or {}
    specs = {
        name: QuestionSpec(
            kind=body.get("type"),
            instructions=body.get("instructions") or "",
            criteria=body.get("criteria"),
        )
        for name, body in payload.items()
        if isinstance(body, dict)
    }
    try:
        parsed, _unexpected = parse_raw_answers(raw, specs)
    except (TypeError, ValueError) as exc:
        return f"{record.get('request_id')}: {exc}"
    stored = jev.get("answers") or {}
    rebuilt = {name: answer_to_json(answer) for name, answer in parsed.items()}
    if stored != rebuilt:
        return f"{record.get('request_id')}: stored answers differ from raw"
    return None


def parse_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    mismatches = [item for item in (check_record_parse(record) for record in records) if item]
    versions = Counter(
        record.get("model_version")
        for record in records
        if record.get("model_version")
    )
    return {
        "records": len(records),
        "null_answers": sum(
            1
            for record in records
            if record.get("type") in {"routing", "tool_pick"} and not (record.get("jev") or {}).get("answers")
        ),
        "parse_mismatches": len(mismatches),
        "parse_mismatch_details": mismatches,
        "unexpected_options": sum(1 for record in records if record.get("unexpected_option")),
        "wiring_errors": sum(1 for record in records if record.get("type") == "wiring_error"),
        "unmapped_rules": sum(
            1
            for record in records
            if record.get("unmapped_rule")
            or ((record.get("regex") or {}).get("labels") or {}).get("unmapped_rule")
        ),
        "model_versions": dict(versions),
    }


def choice_value(record: dict[str, Any], name: str) -> str | None:
    answers = ((record.get("jev") or {}).get("answers") or {})
    item = answers.get(name)
    if not item or item.get("kind") != "choice":
        return None
    value = item.get("value")
    return str(value) if value is not None else None


def choice_confidence(record: dict[str, Any], name: str) -> float | None:
    answers = ((record.get("jev") or {}).get("answers") or {})
    item = answers.get(name) or {}
    value = item.get("confidence")
    return float(value) if isinstance(value, (int, float)) else None


def field_applies(field: str, expected: dict[str, Any]) -> bool:
    """Conditional questions are scored only when they apply to the case.

    intent, dataset, and tool_pick are scored only for disposition answer.
    clarify_reason only for clarify. unsupported_topic only for unsupported.
    """
    disposition = expected.get("disposition")
    if field in {"intent", "dataset", "tool_pick"}:
        return disposition == "answer" and expected.get(field) is not None
    if field == "clarify_reason":
        return disposition == "clarify" and expected.get("clarify_reason") is not None
    if field == "unsupported_topic":
        branches = expected.get("acceptable_outcomes") or []
        if branches:
            branch_dispositions = {branch.get("disposition") for branch in branches}
            if branch_dispositions != {"unsupported"}:
                return False
        return disposition == "unsupported" and expected.get("unsupported_topic") is not None
    if field == "comparison_kind":
        return disposition == "answer" and expected.get("comparison_kind") is not None
    return True


def labels_match(expected: Any, actual: Any) -> bool:
    """A list of expected labels is an acceptable set; any member is correct."""
    if isinstance(expected, (list, tuple)):
        return actual in expected
    return expected == actual


def score_acceptable_outcomes(
    actual: dict[str, Any], branches: list[dict[str, Any]]
) -> bool:
    """True when the fields on the branch selected by disposition all match."""
    disposition = actual.get("disposition")
    for branch in branches:
        if branch.get("disposition") != disposition:
            continue
        if all(
            labels_match(value, actual.get(key))
            for key, value in branch.items()
            if key != "disposition"
        ):
            return True
    return False


def accuracy(pairs: list[tuple[Any, Any]]) -> tuple[int, int]:
    scored = [
        (expected, actual)
        for expected, actual in pairs
        if actual is not None and expected is not None
    ]
    correct = sum(1 for expected, actual in scored if labels_match(expected, actual))
    return correct, len(scored)


def rate(correct: int, total: int) -> str:
    if not total:
        return "n=0"
    return f"{correct}/{total} ({correct / total:.1%})"


def confusion(pairs: list[tuple[str, str]]) -> dict[str, Counter]:
    table: dict[str, Counter] = defaultdict(Counter)
    for expected, actual in pairs:
        if expected is None or actual is None:
            continue
        table[str(expected)][str(actual)] += 1
    return table


def calibration(pairs: list[tuple[bool, float]]) -> list[str]:
    buckets = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]
    lines = ["| confidence | n | accuracy |", "|---|---:|---:|"]
    below = [(ok, conf) for ok, conf in pairs if conf < 0.5]
    if below:
        correct = sum(1 for ok, _conf in below if ok)
        lines.append(f"| <0.5 | {len(below)} | {correct / len(below):.1%} |")
    for low, high in buckets:
        chosen = [(ok, conf) for ok, conf in pairs if low <= conf < high]
        if not chosen:
            label = "1.0" if high > 1 else f"{low:.1f} to {high:.1f}"
            lines.append(f"| {label} | 0 | n=0 |")
            continue
        correct = sum(1 for ok, _conf in chosen if ok)
        label = "0.9 to 1.0" if high > 1 else f"{low:.1f} to {high:.1f}"
        lines.append(f"| {label} | {len(chosen)} | {correct / len(chosen):.1%} |")
    return lines


def noul_metrics(
    rows: list[tuple[int, float]],
) -> str:
    if not rows:
        return "n=0"
    correct, total = accuracy([(label, int(score >= 0.5)) for label, score in rows])
    score = auroc([label for label, _score in rows], [score for _label, score in rows])
    auroc_text = "n/a" if score is None else f"{score:.3f}"
    return f"threshold@0.5 {rate(correct, total)}; AUROC {auroc_text}"


def latency_line(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (
        f"n={len(values)} p50={percentile(values, 0.50):.0f}ms "
        f"p95={percentile(values, 0.95):.0f}ms "
        f"p99={percentile(values, 0.99):.0f}ms"
    )


def cost_line(tokens: int) -> str:
    dollars = tokens / 1_000_000 * INPUT_USD_PER_MILLION
    return f"{tokens} input tokens, about ${dollars:.4f} at $0.042 per million"


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "request_id",
        "source",
        "question",
        "expected",
        "regex",
        "jev",
        "jev_confidence",
        "jev_probabilities",
        "repeats",
        "correct_count",
        "modal_answer",
        "flip_rate",
        "category",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def categorized_summary(path: Path) -> list[str]:
    counts: Counter[str] = Counter()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            counts[(row.get("category") or "").strip() or "(blank)"] += 1
    lines = ["Categorized review"]
    for name, count in sorted(counts.items()):
        lines.append(f"- {name}: {count}")
    return lines


def adjusted_accuracy(
    rows: list[dict[str, Any]],
    *,
    jev_wrong: Callable[[dict[str, Any]], bool],
) -> tuple[str, str]:
    """Raw error rate versus a rate that drops label_wrong and wiring_bug rows."""
    judged = [row for row in rows if row.get("category")]
    if not judged:
        return "raw n=0", "adjusted n=0"
    raw_errors = sum(1 for row in judged if jev_wrong(row))
    kept = [
        row
        for row in judged
        if row.get("category") not in {"label_wrong", "wiring_bug"}
    ]
    adjusted_errors = sum(1 for row in kept if jev_wrong(row) and row.get("category") != "label_wrong")
    # label_wrong rows are removed from the error set and from the denominator.
    adjusted_errors = sum(1 for row in kept if jev_wrong(row))
    raw = f"raw errors {raw_errors}/{len(judged)} ({1 - raw_errors / len(judged):.1%} correct)"
    if not kept:
        return raw, "adjusted n=0"
    adjusted = (
        f"adjusted errors {adjusted_errors}/{len(kept)} "
        f"({1 - adjusted_errors / len(kept):.1%} correct)"
    )
    return raw, adjusted
