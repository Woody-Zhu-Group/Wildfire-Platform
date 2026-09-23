"""Phase 3 entry points: hash proof, repeated scoring, schema v3, ablations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.agent.decisions.schemas import DOMAIN_CONTEXT
from services.agent.eval.jev_offline_eval import RUNS
from services.agent.eval.jev_repeat import budget_estimate, latest_jsonl, repeat_test


def dispatch(args: Any, jobs: list[dict[str, Any]]) -> int:
    calls_per_job = 4 if args.schema == "v3" or args.ablation else 1
    # One pass unless the caller passed --repeats. Five repeats are for a
    # wording or facts change, not the default.
    repeats = args.repeats if args.repeats else 1
    if args.budget_estimate or args.ablation:
        configs = 5 if args.ablation else 1
        print(budget_estimate(len(jobs), repeats * configs, calls_per_job=calls_per_job))
        if args.budget_estimate and not args.ablation and not args.repeat_test:
            return 0

    if args.repeat_test:
        return _run_repeat_test(args.repeat_test, args.repeat_count, args.model)

    if args.ablation or args.schema == "v3":
        return _run_live(args, jobs, repeats)
    return 0


def _run_live(args: Any, jobs: list[dict[str, Any]], repeats: int) -> int:
    from services.agent.decisions.backend import QuestionSpec
    from services.agent.decisions.integrity import question_hash
    from services.agent.decisions.typesafe_backend import TypeSafeBackend, prepared_api_key
    from services.agent.eval.jev_ablation import CONFIGS, run_ablation
    from services.agent.eval.jev_offline_eval import _today

    if not prepared_api_key():
        print("TYPESAFE_API_KEY is not set. No live calls were made.")
        return 1
    backend = TypeSafeBackend(model=args.model, timeout_seconds=30)
    probe = backend.evaluate(
        {"question": "probe", "today": _today(), "context": "probe"},
        {"ready": QuestionSpec(kind="noul", instructions="The word probe is present.")},
        request_id="probe",
        question_hash=question_hash("probe"),
    )
    if probe is None or probe.error or not probe.model_version:
        print(f"Probe failed: {None if probe is None else probe.error}")
        return 1
    backend.model = probe.model_version
    print(f"Pinned model version {backend.model}")
    chosen = tuple(
        item.strip()
        for item in (getattr(args, "ablation_configs", "") or "").split(",")
        if item.strip()
    )
    selected = chosen or (CONFIGS if args.ablation else ("v3_split",))
    report = run_ablation(jobs, backend, repeats=repeats, configs=selected)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS / f"jev_ablation_{stamp}.json"
    out.write_text(json.dumps(report, default=str), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def _run_repeat_test(payloads: int, repeats: int, model: str) -> int:
    path = latest_jsonl(RUNS)
    if path is None:
        print("No stored jev_offline jsonl to replay.")
        return 1
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    from services.agent.decisions.backend import QuestionSpec
    from services.agent.decisions.integrity import question_hash
    from services.agent.decisions.typesafe_backend import TypeSafeBackend, prepared_api_key
    from services.agent.eval.jev_offline_eval import _today

    if not prepared_api_key():
        print("TYPESAFE_API_KEY is not set. Repeat test not started.")
        return 1
    backend = TypeSafeBackend(model=model, timeout_seconds=30)
    probe = backend.evaluate(
        {"question": "probe", "today": _today(), "context": "probe"},
        {"ready": QuestionSpec(kind="noul", instructions="The word probe is present.")},
        request_id="probe",
        question_hash=question_hash("probe"),
    )
    if probe is None or probe.error or not probe.model_version:
        print(f"Probe failed: {None if probe is None else probe.error}")
        return 1
    backend.model = probe.model_version
    print(f"Pinned model version {backend.model}")
    report = repeat_test(records, backend, payloads=payloads, repeats=repeats)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS / f"jev_repeat_{stamp}.json"
    out.write_text(json.dumps(report, default=str), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"hash_mismatches={report['hash_mismatches']} model={report['model']}")
    _write_determinism_note(report, path)
    return 1 if report["hash_mismatches"] else 0


def _write_determinism_note(report: dict[str, Any], source: Path) -> None:
    example = ""
    if report["rows"]:
        example = report["rows"][0].get("stored_hash", "")
    note = Path(__file__).resolve().parents[3] / "docs" / "JEV_DETERMINISM.md"
    existing = note.read_text(encoding="utf-8") if note.exists() else ""
    block = (
        f"\n\n## Repeat test {datetime.now(timezone.utc).isoformat()}\n\n"
        f"Source log: `{source.name}`. Model: `{report['model']}`.\n"
        f"Example stored payload hash: `{example}`.\n"
        f"Canonical hash mismatches: {report['hash_mismatches']}.\n\n"
        f"```\n{report['table']}\n```\n"
    )
    if "Repeat test" not in existing:
        note.write_text(existing + block, encoding="utf-8")
    else:
        note.write_text(existing + block, encoding="utf-8")
    print(f"Updated {note}")
