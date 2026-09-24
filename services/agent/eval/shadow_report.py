"""Summarize a Jev shadow log (JSONL) for production review.

Reads the log at AGENT_JEV_LOG_PATH (or --log) plus its rotated backups
(<log>.1 through <log>.5). Built from the log format only, so it does not
import the Jev decision code and runs on any branch.

Row types handled:
- routing: router labels under regex, Jev answers under jev.answers. Rows
  from the v3 decider also carry outcome.disposition and outcome.confidence.
- tool_pick_decision: question, tool, confidence, path, reason.
- tool_pick, outcome, dropped, wiring_error: counted, tokens and drops summed.

Usage:
    python -m services.agent.eval.shadow_report
    python -m services.agent.eval.shadow_report --log path/to/jev_shadow.jsonl --out report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LOG = "services/agent/logs/jev_shadow.jsonl"
PRICE_PER_MILLION = 0.042
LOW_CONFIDENCE = 0.8
ROTATED_BACKUPS = 5
CONFIDENCE_BINS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0001)]


@dataclass
class LoadedLog:
    records: list[dict[str, Any]] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    bad_lines: int = 0


def resolve_log_path(raw: str | None) -> Path:
    value = raw or os.getenv("AGENT_JEV_LOG_PATH") or DEFAULT_LOG
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def log_files(path: Path) -> list[Path]:
    """Oldest first, so rows read in write order."""
    candidates = [path.with_name(f"{path.name}.{index}") for index in range(ROTATED_BACKUPS, 0, -1)]
    candidates.append(path)
    return [candidate for candidate in candidates if candidate.exists()]


def load_log(path: Path) -> LoadedLog:
    loaded = LoadedLog(files=log_files(path))
    for file in loaded.files:
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                loaded.bad_lines += 1
                continue
            if isinstance(row, dict):
                loaded.records.append(row)
            else:
                loaded.bad_lines += 1
    return loaded


def router_disposition(row: dict[str, Any]) -> str:
    regex = row.get("regex") or {}
    labels = regex.get("labels") or {}
    if labels.get("disposition"):
        return str(labels["disposition"])
    path = regex.get("path")
    if path in {"deterministic", "model"}:
        return "answer"
    if path == "clarification":
        return "clarify"
    if path == "unsupported":
        return "unsupported"
    return "unknown"


def _answer_confidence(answer: dict[str, Any]) -> float | None:
    """Choice answers carry their own confidence. A Noul is max(p, 1 - p)."""
    confidence = answer.get("confidence")
    if answer.get("kind") == "noul":
        value = answer.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(float(value), 1.0 - float(value))
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        return float(confidence)
    return None


def jev_disposition(row: dict[str, Any]) -> tuple[str | None, float | None]:
    """Jev disposition and its confidence, or (None, None) when Jev gave no answer."""
    if row.get("error"):
        return None, None
    outcome = row.get("outcome")
    if isinstance(outcome, dict) and outcome.get("disposition"):
        confidence = outcome.get("confidence")
        return str(outcome["disposition"]), float(confidence) if isinstance(confidence, (int, float)) else None
    jev = row.get("jev") or {}
    answer = (jev.get("answers") or {}).get("disposition")
    if isinstance(answer, dict) and answer.get("value") is not None:
        return str(answer["value"]), _answer_confidence(answer)
    return None, None


def input_tokens(row: dict[str, Any]) -> int:
    total = 0
    jev = row.get("jev")
    if isinstance(jev, dict) and isinstance(jev.get("input_tokens"), (int, float)):
        total += int(jev["input_tokens"])
    for call in row.get("calls") or []:
        if isinstance(call, dict) and isinstance(call.get("input_tokens"), (int, float)):
            total += int(call["input_tokens"])
    if isinstance(row.get("input_tokens"), (int, float)):
        total += int(row["input_tokens"])
    return total


def _table(headers: list[str], rows: Iterable[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return lines


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def _bin_label(low: float, high: float) -> str:
    return f"{low:.1f} to {min(high, 1.0):.1f}"


def build_report(loaded: LoadedLog, *, log_path: Path, examples: int = 10) -> list[str]:
    records = loaded.records
    by_type = Counter(str(row.get("type") or "untyped") for row in records)
    routing = [row for row in records if row.get("type") == "routing"]
    decisions = [row for row in records if row.get("type") == "tool_pick_decision"]

    questions = {row.get("question_hash") or row.get("question") for row in routing}
    questions |= {row.get("question") for row in decisions}
    questions.discard(None)
    request_ids = {row.get("request_id") for row in routing if row.get("request_id")}

    lines: list[str] = ["# Jev shadow log report", ""]
    lines.append(f"Log: {log_path}")
    lines.append(f"Files read: {', '.join(file.name for file in loaded.files) or 'none'}")
    lines.append(f"Rows: {len(records)} (unparseable lines skipped: {loaded.bad_lines})")
    timestamps = sorted(str(row["ts"]) for row in records if row.get("ts"))
    if timestamps:
        lines.append(f"Time span: {timestamps[0]} to {timestamps[-1]}")
    lines.append("")

    lines += ["## Questions", ""]
    lines += _table(
        ["measure", "count"],
        [
            ["distinct questions (routing and tool pick)", len(questions)],
            ["routing rows", len(routing)],
            ["distinct request ids on routing rows", len(request_ids)],
            ["tool_pick_decision rows", len(decisions)],
        ],
    )
    lines += ["", "Row types:", ""]
    lines += _table(["type", "rows"], sorted(by_type.items(), key=lambda item: (-item[1], item[0])))
    dropped = Counter(str(row.get("reason")) for row in records if row.get("type") == "dropped")
    if dropped:
        lines += ["", "Dropped before calling Jev:", ""]
        lines += _table(["reason", "rows"], sorted(dropped.items()))
    errors = [row for row in routing if row.get("error")]
    lines += ["", f"Routing rows with a Jev error: {len(errors)} ({_pct(len(errors), len(routing))})", ""]

    lines += ["## Router versus Jev disposition", ""]
    pairs: list[tuple[str, str, float | None, dict[str, Any]]] = []
    for row in routing:
        jev, confidence = jev_disposition(row)
        if jev is None:
            continue
        pairs.append((router_disposition(row), jev, confidence, row))
    agreed = sum(1 for router, jev, _, _ in pairs if router == jev)
    lines.append(
        f"Rows with a Jev disposition: {len(pairs)}. Agree: {agreed} ({_pct(agreed, len(pairs))}). "
        f"Disagree: {len(pairs) - agreed} ({_pct(len(pairs) - agreed, len(pairs))})."
    )
    lines.append("")
    router_values = sorted({router for router, _, _, _ in pairs})
    jev_values = sorted({jev for _, jev, _, _ in pairs})
    matrix = Counter((router, jev) for router, jev, _, _ in pairs)
    if pairs:
        lines.append("Rows are the router, columns are Jev.")
        lines.append("")
        lines += _table(
            ["router \\ jev", *jev_values, "total"],
            [
                [router, *[matrix[(router, jev)] for jev in jev_values], sum(matrix[(router, jev)] for jev in jev_values)]
                for router in router_values
            ],
        )
        lines.append("")
    disagreements = [pair for pair in pairs if pair[0] != pair[1]]
    if disagreements:
        lines += ["Disagreement pairs:", ""]
        pair_counts = Counter((router, jev) for router, jev, _, _ in disagreements)
        lines += _table(
            ["router", "jev", "rows"],
            [[router, jev, count] for (router, jev), count in pair_counts.most_common()],
        )
        lines += ["", f"Top {min(examples, len(disagreements))} disagreements, most confident Jev first:", ""]
        ranked = sorted(disagreements, key=lambda pair: -(pair[2] if pair[2] is not None else -1.0))
        lines += _table(
            ["router", "router rule", "jev", "jev conf", "jev reason", "question"],
            [
                [
                    router,
                    (row.get("regex") or {}).get("rule"),
                    jev,
                    confidence,
                    _jev_reason(row, jev),
                    row.get("question"),
                ]
                for router, jev, confidence, row in ranked[:examples]
            ],
        )
        lines.append("")

    lines += ["## Jev disposition confidence", ""]
    confidences = [confidence for _, _, confidence, _ in pairs if confidence is not None]
    if confidences:
        low = sum(1 for value in confidences if value < LOW_CONFIDENCE)
        lines.append(
            f"Rows with confidence: {len(confidences)}. Mean: {sum(confidences) / len(confidences):.3f}. "
            f"Under {LOW_CONFIDENCE}: {low} ({_pct(low, len(confidences))})."
        )
        lines.append("")
        lines += _table(
            ["confidence", "rows", "share"],
            [
                [_bin_label(lo, hi), n, _pct(n, len(confidences))]
                for lo, hi in CONFIDENCE_BINS
                for n in [sum(1 for value in confidences if lo <= value < hi)]
            ],
        )
    else:
        lines.append("No routing rows with a Jev disposition confidence.")
    lines.append("")

    lines += ["## tool_pick_decision rows", ""]
    if decisions:
        by_path_reason = Counter((str(row.get("path")), str(row.get("reason"))) for row in decisions)
        conf_by_key: dict[tuple[str, str], list[float]] = {}
        for row in decisions:
            value = row.get("confidence")
            if isinstance(value, (int, float)):
                conf_by_key.setdefault((str(row.get("path")), str(row.get("reason"))), []).append(float(value))
        lines += _table(
            ["path", "reason", "rows", "share", "mean conf"],
            [
                [
                    path,
                    reason,
                    count,
                    _pct(count, len(decisions)),
                    (sum(conf_by_key[(path, reason)]) / len(conf_by_key[(path, reason)]))
                    if conf_by_key.get((path, reason))
                    else None,
                ]
                for (path, reason), count in sorted(by_path_reason.items(), key=lambda item: (-item[1], item[0]))
            ],
        )
        lines += ["", "By tool:", ""]
        by_tool = Counter(str(row.get("tool")) for row in decisions)
        lines += _table(["tool", "rows"], by_tool.most_common())
    else:
        lines.append("No tool_pick_decision rows.")
    lines.append("")

    tokens = sum(input_tokens(row) for row in records)
    cost = tokens * PRICE_PER_MILLION / 1_000_000
    lines += ["## Estimated TypeSafe cost", ""]
    lines.append(f"Input tokens: {tokens:,}. At ${PRICE_PER_MILLION} per million: ${cost:.4f}.")
    lines.append("Rows without an input_tokens field (such as tool_pick_decision) are not counted.")
    lines.append("")
    return lines


def _jev_reason(row: dict[str, Any], disposition: str) -> str | None:
    outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
    answers = (row.get("jev") or {}).get("answers") or {}
    key = {"clarify": "clarify_reason", "unsupported": "unsupported_topic"}.get(disposition)
    if key is None:
        return None
    if outcome.get(key):
        return str(outcome[key])
    answer = answers.get(key)
    if isinstance(answer, dict) and answer.get("value") is not None:
        return str(answer["value"])
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", help="Shadow log path. Default: AGENT_JEV_LOG_PATH, then " + DEFAULT_LOG)
    parser.add_argument("--out", help="Also write the report as markdown to this path")
    parser.add_argument("--examples", type=int, default=10, help="Disagreement examples to show")
    args = parser.parse_args(argv)

    path = resolve_log_path(args.log)
    loaded = load_log(path)
    if not loaded.files:
        print(f"No shadow log at {path} (or rotated backups).", file=sys.stderr)
        return 1
    lines = build_report(loaded, log_path=path, examples=max(0, args.examples))
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
