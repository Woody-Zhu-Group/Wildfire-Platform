"""Compare Jev tool_pick with qwen's first-turn and final tools on a shadow run."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from services.agent.decisions.mapping import model_tool_labels
from services.agent.decisions.shadow_log import resolve_log_path

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS = REPO_ROOT / "services" / "agent" / "eval" / "runs"


def _trajectories(tag: str, runs: Path = RUNS) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(runs.glob(f"*__{tag}/trajectories.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _question_key(text: str) -> str:
    return " ".join(text.split()).lower()


def _tool_pick_body(row: dict[str, Any]) -> dict[str, Any] | None:
    answers = ((row.get("jev") or {}).get("answers") or {})
    body = answers.get("tool_pick")
    if isinstance(body, dict) and body.get("value"):
        latency = None
        for call in row.get("calls") or []:
            if call.get("name") == "tool_pick":
                latency = call.get("latency_ms")
        return {
            "value": body["value"],
            "confidence": body.get("confidence"),
            "latency_ms": latency if latency is not None else (row.get("jev") or {}).get("latency_ms"),
        }
    return None


def load_shadow_picks(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Join key is the question text. Shadow request ids are not case ids."""
    picks: dict[str, dict[str, Any]] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") not in {"routing", "tool_pick"}:
                continue
            body = _tool_pick_body(row)
            question = row.get("question")
            if body is None or not question:
                continue
            picks[_question_key(question)] = body
    return picks


def default_log_paths() -> list[Path]:
    configured = os.getenv("AGENT_JEV_LOG_PATH", "services/agent/logs/jev_shadow.jsonl")
    return [resolve_log_path(configured)]


def qwen_tools(response: dict[str, Any]) -> tuple[str | None, str | None]:
    labels = model_tool_labels(
        response.get("trajectory") or [],
        str(response.get("status") or ""),
    )
    first = (labels["model_first_tools"] or [None])[0]
    final = (labels["model_final_tools"] or [None])[0]
    return first, final


def compare(tag: str, *, runs: Path = RUNS, logs: list[Path] | None = None) -> dict[str, Any]:
    rows = _trajectories(tag, runs)
    picks = load_shadow_picks(logs if logs is not None else default_log_paths())
    model_rows = [
        row
        for row in rows
        if (row.get("case") or {}).get("expected_route") == "model"
    ]
    cases = []
    for row in model_rows:
        case = row["case"]
        expected = (case.get("expected_tools") or [None])[0]
        first, final = qwen_tools(row.get("response") or {})
        jev = picks.get(_question_key(case.get("question") or ""))
        cases.append(
            {
                "id": case.get("id"),
                "expected": expected,
                "qwen_first": first,
                "qwen_final": final,
                "qwen_elapsed_ms": row.get("elapsed_ms"),
                "jev": None if jev is None else jev.get("value"),
                "jev_confidence": None if jev is None else jev.get("confidence"),
                "jev_latency_ms": None if jev is None else jev.get("latency_ms"),
            }
        )
    return {"tag": tag, "cases": cases, "log_files": [str(path) for path in (logs or default_log_paths())]}


def _rate(hits: int, total: int) -> str:
    if not total:
        return "n=0"
    return f"{hits}/{total} ({hits / total:.1%})"


def render(report: dict[str, Any]) -> str:
    cases = report["cases"]
    lines = [f"Compared {len(cases)} model-path cases from tag {report['tag']}."]
    lines.append("Shadow log: " + ", ".join(report["log_files"]))
    qwen_first = qwen_final = jev_hits = 0
    qwen_times = []
    jev_times = []
    for case in cases:
        if case["qwen_first"] == case["expected"]:
            qwen_first += 1
        if case["qwen_final"] == case["expected"]:
            qwen_final += 1
        if case["jev"] is not None and case["jev"] == case["expected"]:
            jev_hits += 1
        if isinstance(case["qwen_elapsed_ms"], (int, float)):
            qwen_times.append(case["qwen_elapsed_ms"])
        if isinstance(case["jev_latency_ms"], (int, float)):
            jev_times.append(case["jev_latency_ms"])
        lines.append(
            f"{case['id']}: qwen_first={case['qwen_first']} qwen_final={case['qwen_final']} "
            f"qwen_elapsed_ms={case['qwen_elapsed_ms']} jev={case['jev']} "
            f"jev_confidence={case['jev_confidence']} jev_latency_ms={case['jev_latency_ms']} "
            f"expected={case['expected']}"
        )
    lines.append(f"qwen first-tool {_rate(qwen_first, len(cases))}")
    lines.append(f"qwen final-tool {_rate(qwen_final, len(cases))}")
    lines.append(f"jev tool_pick {_rate(jev_hits, len(cases))}")
    if qwen_times:
        lines.append(
            f"qwen elapsed ms min={min(qwen_times):.0f} max={max(qwen_times):.0f}"
        )
    if jev_times:
        lines.append(
            f"jev tool_pick latency ms min={min(jev_times):.0f} max={max(jev_times):.0f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    log_paths = None
    if "--log" in args:
        index = args.index("--log")
        log_paths = [Path(args[index + 1])]
        del args[index : index + 2]
    tag = args[0] if args else "jev-shadow"
    rows = _trajectories(tag)
    if not rows:
        print(
            f"No trajectories for {tag} under {RUNS}. "
            "Expected a directory named *__{tag}/trajectories.jsonl."
        )
        return 1
    report = compare(tag, logs=log_paths)
    print(render(report))
    missing = sum(1 for case in report["cases"] if case["jev"] is None)
    if missing:
        print(
            f"{missing} model-path cases had no Jev tool_pick. "
            "The shadow log is AGENT_JEV_LOG_PATH, not a file inside the run folder. "
            "Pass --log with that jsonl if it lives outside this checkout."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
