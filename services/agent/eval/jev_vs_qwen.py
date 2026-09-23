"""Compare Jev tool_pick with qwen's first-turn and final tools on a shadow run."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS = REPO_ROOT / "services" / "agent" / "eval" / "runs"
LOGS = REPO_ROOT / "services" / "agent" / "logs"


def _trajectories(tag: str) -> list[dict[str, Any]]:
    paths = sorted(RUNS.glob(f"*__{tag}/trajectories.jsonl"))
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _shadow_tool_picks() -> dict[str, str]:
    picks = {}
    if not LOGS.exists():
        return picks
    for path in sorted(LOGS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "tool_pick":
                continue
            answer = ((row.get("jev") or {}).get("answers") or {}).get("tool_pick") or {}
            if answer.get("value"):
                picks[row.get("request_id") or row.get("question")] = answer["value"]
    return picks


def _tools(response: dict[str, Any]) -> tuple[str | None, str | None]:
    calls = response.get("tool_calls") or response.get("tools") or []
    names = []
    for call in calls:
        if isinstance(call, str):
            names.append(call)
        elif isinstance(call, dict):
            names.append(call.get("name") or call.get("tool"))
    first = names[0] if names else None
    final = names[-1] if names else None
    return first, final


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    tag = args[0] if args else "jev-shadow"
    rows = _trajectories(tag)
    if not rows:
        print(f"No trajectories for {tag}. Run the EC2 shadow eval first.")
        return 1
    picks = _shadow_tool_picks()
    model_rows = [
        row
        for row in rows
        if (row.get("case") or {}).get("expected_route") == "model"
    ]
    qwen_first = qwen_final = jev_hits = compared = 0
    differ = []
    for row in model_rows:
        case = row["case"]
        expected = (case.get("expected_tools") or [None])[0]
        first, final = _tools(row.get("response") or {})
        compared += 1
        qwen_first += int(first == expected)
        qwen_final += int(final == expected)
        jev = picks.get(case.get("id"))
        if jev is not None:
            jev_hits += int(jev == expected)
        if jev != first:
            differ.append(f"{case.get('id')}: qwen_first={first} jev={jev} expected={expected}")
        elapsed = row.get("elapsed_ms")
        print(f"{case.get('id')} qwen_elapsed_ms={elapsed} qwen_first={first} jev={jev}")
    print(
        f"qwen first-tool {qwen_first}/{compared}  "
        f"qwen final-tool {qwen_final}/{compared}  "
        f"jev tool_pick {jev_hits}/{compared}"
    )
    print("Differences:")
    for line in differ:
        print(f"- {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
