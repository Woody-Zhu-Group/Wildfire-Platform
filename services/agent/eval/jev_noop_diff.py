"""Compare two scored eval runs and separate shadow effects from LLM variance."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNS = REPO_ROOT / "services" / "agent" / "eval" / "runs"

IGNORE = {
    "elapsed_ms",
    "request_id",
    "raw_payload",
    "raw_sha256",
    "latency_ms",
    "timings",
}


def _runs(tag: str) -> list[Path]:
    return sorted(RUNS.glob(f"*__{tag}/trajectories.jsonl"))


def _load(tag: str) -> dict[str, dict[str, Any]]:
    paths = _runs(tag)
    if not paths:
        raise SystemExit(f"No trajectories for run tag {tag} under {RUNS}")
    rows = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            case_id = (row.get("case") or {}).get("id")
            if case_id:
                rows[case_id] = row
    return rows


def normalize(value: Any) -> Any:
    """Drop IGNORE keys at every nesting level so both sides compare the same keys."""
    if isinstance(value, dict):
        return {
            key: normalize(item) for key, item in value.items() if key not in IGNORE
        }
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def _score_view(row: dict[str, Any]) -> dict[str, Any]:
    score = dict(row.get("score") or {})
    response = row.get("response") or {}
    kept = dict(score)
    for key in ("status", "route", "answer", "caveats", "views"):
        if key in response:
            kept.setdefault(key, response[key])
    return normalize(kept)


_MISSING = object()


def _diff(left: Any, right: Any, prefix: str = "") -> dict[str, tuple[Any, Any]]:
    """Leaf-level differences keyed by dotted path. Ignored keys never appear.

    A key present on one side only is reported with None for the missing side:
    after normalization it is a real difference, not an ignorable one.
    """
    if isinstance(left, dict) and isinstance(right, dict):
        changes: dict[str, tuple[Any, Any]] = {}
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            changes.update(_diff(left.get(key, _MISSING), right.get(key, _MISSING), path))
        return changes
    if left is _MISSING:
        left = None
    if right is _MISSING:
        right = None
    if left != right:
        return {prefix: (left, right)}
    return {}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    baseline = None
    if "--baseline-tag" in args:
        index = args.index("--baseline-tag")
        baseline = args[index + 1]
        del args[index : index + 2]
    if len(args) != 2:
        print("Usage: python -m services.agent.eval.jev_noop_diff TAG_OFF TAG_SHADOW --baseline-tag TAG_OFF_2")
        return 2
    off_tag, shadow_tag = args
    off = _load(off_tag)
    shadow = _load(shadow_tag)
    other = _load(baseline) if baseline else {}
    differences = 0
    variance = 0
    ids = sorted(set(off) | set(shadow))
    for case_id in ids:
        changes = _diff(_score_view(off.get(case_id, {})), _score_view(shadow.get(case_id, {})))
        if not changes:
            continue
        llm = {}
        real = {}
        if baseline and case_id in other:
            baseline_changes = _diff(_score_view(off.get(case_id, {})), _score_view(other[case_id]))
            for key, pair in changes.items():
                if key in baseline_changes:
                    llm[key] = pair
                else:
                    real[key] = pair
        else:
            real = changes
        if llm:
            variance += 1
            print(f"{case_id} llm_variance {sorted(llm)}")
        if real:
            differences += 1
            print(f"{case_id} shadow_effect { {key: real[key] for key in real} }")
    if differences:
        print(f"NO-OP DIFF: {differences} DIFFERENCES")
        if variance:
            print(f"llm_variance cases: {variance}")
        return 1
    print("NO-OP DIFF: CLEAN")
    if variance:
        print(f"llm_variance cases excluded: {variance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
