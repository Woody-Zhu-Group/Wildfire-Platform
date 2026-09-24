"""Side-by-side deterministic vs model-only routing experiment report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "runs"
OUT_FILE = HERE / "ROUTING_EXPERIMENT.md"


def _load_records(path: Path) -> list[dict[str, Any]]:
    """Load JSONL, recovering concatenated/broken lines when possible."""
    rows: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        chunk = line.strip()
        if not chunk:
            continue
        try:
            rows.append(json.loads(chunk))
            continue
        except json.JSONDecodeError:
            pass
        # Recover one or more objects embedded in a corrupted line.
        idx = 0
        while idx < len(chunk):
            start = chunk.find("{", idx)
            if start < 0:
                break
            try:
                value, end = decoder.raw_decode(chunk[start:])
            except json.JSONDecodeError:
                idx = start + 1
                continue
            if isinstance(value, dict) and "case" in value and "score" in value:
                rows.append(value)
            idx = start + end
    # Keep last record per case id (later writes win).
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = ((row.get("case") or {}).get("id")) or None
        if case_id:
            by_id[case_id] = row
    return list(by_id.values()) if by_id else rows


def _case_pass(row: dict[str, Any]) -> bool:
    score = row.get("score") or {}
    return bool(
        score.get("routing_pass")
        and score.get("caveat_pass")
        and score.get("status_pass")
        and score.get("recovery_pass", True)
    )


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1
    passed = [row for row in rows if _case_pass(row)]
    model_rows = [
        row
        for row in rows
        if (row.get("response") or {}).get("route", {}).get("path") == "model"
        or row["case"].get("force_model")
        or row["case"].get("expected_route") == "model"
    ]
    latencies = [float(row.get("elapsed_ms") or 0) for row in rows]
    model_latencies = [
        float(event.get("latency_ms") or 0)
        for row in rows
        for event in ((row.get("response") or {}).get("trajectory") or [])
        if event.get("type") == "model_turn"
    ]

    def pct(values: list[float], p: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        idx = int(round((len(ordered) - 1) * p / 100))
        return round(ordered[idx], 2)

    return {
        "cases": len(rows),
        "e2e_pass_rate": len(passed) / n,
        "routing_pass_rate": sum(
            1 for row in rows if (row.get("score") or {}).get("routing_pass")
        )
        / n,
        "status_pass_rate": sum(
            1 for row in rows if (row.get("score") or {}).get("status_pass")
        )
        / n,
        "caveat_pass_rate": sum(
            1 for row in rows if (row.get("score") or {}).get("caveat_pass")
        )
        / n,
        "recovery_pass_rate": sum(
            1 for row in rows if (row.get("score") or {}).get("recovery_pass", True)
        )
        / n,
        "model_path_cases": len(model_rows),
        "request_p50_ms": pct(latencies, 50),
        "request_p95_ms": pct(latencies, 95),
        "model_call_p50_ms": pct(model_latencies, 50),
        "model_call_p95_ms": pct(model_latencies, 95),
        "passed_ids": {row["case"]["id"] for row in passed},
        "failed_ids": {row["case"]["id"] for row in rows if not _case_pass(row)},
    }


def compare(det_path: Path, model_path: Path) -> str:
    det = _load_records(det_path)
    model = _load_records(model_path)
    det_m = _metrics(det)
    model_m = _metrics(model)
    only_det = sorted(det_m["passed_ids"] - model_m["passed_ids"])
    only_model = sorted(model_m["passed_ids"] - det_m["passed_ids"])
    both = sorted(det_m["passed_ids"] & model_m["passed_ids"])

    alternate_correct: list[str] = []
    det_by_id = {row["case"]["id"]: row for row in det}
    model_by_id = {row["case"]["id"]: row for row in model}
    for case_id in both:
        det_tools = (det_by_id[case_id].get("score") or {}).get("actual_tools") or []
        model_tools = (model_by_id[case_id].get("score") or {}).get("actual_tools") or []
        if det_tools != model_tools:
            alternate_correct.append(
                f"`{case_id}`: det={det_tools} · model-only={model_tools}"
            )

    lines = [
        "# Deterministic vs model-only routing experiment",
        "",
        "Default remains deterministic-first. This report compares identical cases "
        "with `AGENT_DISABLE_DETERMINISTIC_ROUTING` / `--disable-deterministic`.",
        "",
        "Harness guarantees stayed active on both paths: caveat injection, grounding, "
        "argument validation, year guards, retry bounding, relative-date resolution, "
        "and unsupported refusals.",
        "",
        "**Latency note:** these numbers are from the runtime the comparison was "
        "made on and may not reflect production latency.",
        "",
        f"- Deterministic run: `{det_path}`",
        f"- Model-only run: `{model_path}`",
        "",
        "## Summary metrics",
        "",
        "| Metric | Deterministic-first | Model-only |",
        "|---|---:|---:|",
        f"| End-to-end pass | {det_m['e2e_pass_rate']:.1%} | {model_m['e2e_pass_rate']:.1%} |",
        f"| Routing pass | {det_m['routing_pass_rate']:.1%} | {model_m['routing_pass_rate']:.1%} |",
        f"| Status pass | {det_m['status_pass_rate']:.1%} | {model_m['status_pass_rate']:.1%} |",
        f"| Caveat pass | {det_m['caveat_pass_rate']:.1%} | {model_m['caveat_pass_rate']:.1%} |",
        f"| Recovery pass | {det_m['recovery_pass_rate']:.1%} | {model_m['recovery_pass_rate']:.1%} |",
        f"| Model-path cases | {det_m['model_path_cases']} | {model_m['model_path_cases']} |",
        f"| Request p50/p95 (ms) | {det_m['request_p50_ms']}/{det_m['request_p95_ms']} | {model_m['request_p50_ms']}/{model_m['request_p95_ms']} |",
        f"| Model-call p50/p95 (ms) | {det_m['model_call_p50_ms']}/{det_m['model_call_p95_ms']} | {model_m['model_call_p50_ms']}/{model_m['model_call_p95_ms']} |",
        "",
        "## Cases only deterministic-first passed",
        "",
    ]
    lines.extend([f"- `{item}`" for item in only_det] or ["- None"])
    lines.extend(["", "## Cases only model-only passed", ""])
    lines.extend([f"- `{item}`" for item in only_model] or ["- None"])
    lines.extend(
        [
            "",
            "## Alternate but also-correct tool sequences (both passed)",
            "",
        ]
    )
    lines.extend([f"- {item}" for item in alternate_correct] or ["- None"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deterministic", required=True)
    parser.add_argument("--model-only", required=True)
    parser.add_argument("--out", default=str(OUT_FILE))
    args = parser.parse_args()
    report = compare(Path(args.deterministic), Path(args.model_only))
    out = Path(args.out)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"[compare] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
