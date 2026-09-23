"""Run the dev set once through each Jev backend (v3_hybrid) and compare them.

Dev is cases.json plus jev_paraphrases.json. It has been used for tuning, so
accuracy here is not a clean generalization number.

    python -m services.agent.eval.jev_backend_compare --backends typesafe,openrouter
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from services.agent.decisions.backend import Answer, QuestionSpec
from services.agent.decisions.integrity import question_hash
from services.agent.eval.jev_ablation import _run_calls, _values, units_from_jobs
from services.agent.eval.jev_metrics import INPUT_USD_PER_MILLION, field_applies, labels_match
from services.agent.eval.jev_offline_eval import (
    CASES_FILE,
    PARAPHRASE_FILE,
    RUNS,
    _today,
    build_jobs,
    load_cases,
)
from shared.db import REPO_ROOT

FIELDS = ("disposition", "intent", "dataset", "tool_pick", "clarify_reason")
CONFIG = "v3_hybrid"


def answer_confidence(answer: Answer) -> float | None:
    """Choice confidence as returned; for a Noul, max(p, 1 - p), never raw p."""
    if answer.kind == "noul":
        if answer.value is None:
            return None
        p = float(answer.value)
        return max(p, 1 - p)
    return None if answer.confidence is None else float(answer.confidence)


def dev_units() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for case in load_cases(CASES_FILE):
        jobs.extend(build_jobs(case, source="cases", tool_pick_all=False))
    for case in load_cases(PARAPHRASE_FILE):
        jobs.extend(build_jobs(case, source="paraphrases", tool_pick_all=False))
    return units_from_jobs(jobs)


def run_backend(name: str, model: str | None, units: list[dict[str, Any]], cap_usd: float) -> dict[str, Any]:
    from services.agent.decisions.typesafe_backend import make_backend, reset_for_tests

    reset_for_tests()
    default_model = "jev-1.13" if name == "openrouter" else "jev-latest"
    backend = make_backend(name, model=model or default_model, timeout_seconds=30)
    probe = backend.evaluate(
        {"question": "probe", "today": _today(), "context": "probe"},
        {"ready": QuestionSpec(kind="noul", instructions="The word probe is present.")},
        request_id="probe",
        question_hash=question_hash("probe"),
    )
    if probe is None or probe.error or not probe.model_version:
        raise RuntimeError(f"{name} probe failed: {None if probe is None else probe.error}")
    print(f"[{name}] request model {backend.model}, served {probe.model_version}")

    rows: list[dict[str, Any]] = []
    spent = (probe.input_tokens or 0) * INPUT_USD_PER_MILLION / 1_000_000
    served: set[str] = {probe.model_version}
    for index, unit in enumerate(units, start=1):
        shot = _run_calls(backend, unit, CONFIG)
        judged = _values(unit, CONFIG, shot["answers"])
        spent += shot["tokens"] * INPUT_USD_PER_MILLION / 1_000_000
        confidences = {
            key: answer_confidence(answer) for key, answer in shot["answers"].items()
        }
        row = {
            "id": str(unit["case"].get("id")),
            "source": unit["source"],
            "question": unit["question"],
            "has_tools": bool(unit["tools"]),
            "values": {field: judged.get(field) for field in FIELDS},
            "expected": judged["expected"],
            "confidences": confidences,
            "tokens": shot["tokens"],
            "wall_ms": shot["wall_ms"],
            "errors": [e for e in shot["errors"] if e],
        }
        rows.append(row)
        if index % 10 == 0:
            print(f"[{name}] {index}/{len(units)} spent ${spent:.4f}")
        if spent > cap_usd:
            raise RuntimeError(f"{name} spend ${spent:.4f} passed cap ${cap_usd}")
    return {"backend": name, "request_model": backend.model, "served_models": sorted(served), "rows": rows, "cost_usd": spent}


def score(run: dict[str, Any]) -> dict[str, Any]:
    per_field: dict[str, list[bool]] = defaultdict(list)
    confidences: list[float] = []
    walls: list[float] = []
    errors = 0
    for row in run["rows"]:
        walls.append(row["wall_ms"])
        errors += len(row["errors"])
        confidences.extend(c for c in row["confidences"].values() if c is not None)
        for field in FIELDS:
            if field == "tool_pick" and not row["has_tools"]:
                continue
            if not field_applies(field, row["expected"]):
                continue
            per_field[field].append(labels_match(row["expected"].get(field), row["values"][field]))
    hits = sum(sum(v) for v in per_field.values())
    total = sum(len(v) for v in per_field.values())
    walls.sort()

    def pct(q: float) -> float:
        return walls[min(len(walls) - 1, int(round(q * (len(walls) - 1))))] if walls else 0.0

    return {
        "questions": len(run["rows"]),
        "label_accuracy": hits / total if total else 0.0,
        "label_hits": hits,
        "label_n": total,
        "per_field": {f: {"acc": sum(v) / len(v), "n": len(v)} for f, v in per_field.items()},
        "mean_confidence": statistics.fmean(confidences) if confidences else None,
        "p50_ms": pct(0.5),
        "p95_ms": pct(0.95),
        "mean_input_tokens": statistics.fmean(r["tokens"] for r in run["rows"]),
        "cost_usd": run["cost_usd"],
        "call_errors": errors,
    }


def agreement(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    by_id = {(r["source"], r["id"]): r for r in right["rows"]}
    same: dict[str, list[bool]] = defaultdict(list)
    deltas: list[float] = []
    disagreements = []
    for row in left["rows"]:
        other = by_id.get((row["source"], row["id"]))
        if other is None:
            continue
        for field in FIELDS:
            if field == "tool_pick" and not row["has_tools"]:
                continue
            a, b = row["values"][field], other["values"][field]
            same[field].append(a == b)
            if a != b:
                disagreements.append({"id": row["id"], "source": row["source"], "field": field, left["backend"]: a, right["backend"]: b})
        for key, conf in row["confidences"].items():
            twin = other["confidences"].get(key)
            if conf is not None and twin is not None:
                deltas.append(abs(conf - twin))
    total = sum(len(v) for v in same.values())
    return {
        "all_fields": sum(sum(v) for v in same.values()) / total if total else 0.0,
        "per_field": {f: sum(v) / len(v) for f, v in same.items()},
        "mean_abs_confidence_delta": statistics.fmean(deltas) if deltas else None,
        "max_abs_confidence_delta": max(deltas) if deltas else None,
        "disagreements": disagreements,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backends", default="typesafe,openrouter")
    parser.add_argument("--typesafe-model", default=None)
    parser.add_argument("--openrouter-model", default=None)
    parser.add_argument("--cap-usd", type=float, default=0.5, help="Per-backend spend cap.")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    units = dev_units()
    print(f"Dev units: {len(units)} ({CONFIG}, one pass)")
    runs = {}
    for name in [b.strip() for b in args.backends.split(",") if b.strip()]:
        model = args.openrouter_model if name == "openrouter" else args.typesafe_model
        started = time.perf_counter()
        runs[name] = run_backend(name, model, units, args.cap_usd)
        runs[name]["runtime_s"] = time.perf_counter() - started
    report: dict[str, Any] = {
        "eval_set": "dev (cases.json + jev_paraphrases.json), used for tuning",
        "config": CONFIG,
        "scores": {name: score(run) for name, run in runs.items()},
        "models": {name: {"request": run["request_model"], "served": run["served_models"]} for name, run in runs.items()},
    }
    names = list(runs)
    if len(names) == 2:
        report["agreement"] = agreement(runs[names[0]], runs[names[1]])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS / f"jev_backend_compare_{stamp}.json"
    out.write_text(json.dumps({**report, "rows": {n: r["rows"] for n, r in runs.items()}}, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "agreement"}, indent=2, default=str))
    if "agreement" in report:
        brief = {k: v for k, v in report["agreement"].items() if k != "disagreements"}
        brief["disagreement_count"] = len(report["agreement"]["disagreements"])
        print(json.dumps(brief, indent=2))
    print(f"Wrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
