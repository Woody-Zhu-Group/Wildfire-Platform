"""One v3 pass for the combined decider, and five passes of the measure Choice.

The measure repeats are only the new topic question. Stops before spend passes $1.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from services.agent.config import AgentSettings
from services.agent.decisions.jev_policy import derive_outcome, facts_from_answers
from services.agent.decisions.typesafe_backend import TypeSafeBackend
from services.agent.eval.jev_ablation import _calls, _run_calls
from services.agent.routing import UNSUPPORTED, candidate_tools

HERE = Path(__file__).resolve().parent
OUT = HERE / "runs" / "v3_gap_score.json"
PRICE = 0.042 / 1_000_000
CAP = 1.0


def _load() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text(encoding="utf-8"))
    return {"v3": [], "measure": [], "tokens": 0, "stopped": False}


def _over(tokens: int) -> bool:
    return tokens * PRICE >= CAP


def _v3_rows() -> list[dict]:
    raw = subprocess.check_output(
        ["git", "show", "platform/jev-plan-archive:services/agent/eval/jev_holdout_v3.json"]
    )
    hold = json.loads(raw.decode("utf-8"))
    labels = json.loads(
        (HERE / "jev_holdout_v3_labels_chatgpt.json").read_text(encoding="utf-8")
    )
    rows = []
    for index, label in enumerate(labels):
        if "disposition" not in label or label.get("uncertain"):
            continue
        item = hold[index]
        rows.append(
            {
                "id": item["id"],
                "question": item["question"],
                "label": label["disposition"],
                "reason": label.get("reason"),
            }
        )
    return rows


def _measure_rows() -> list[dict]:
    rows = []
    for name in ("cases.json", "jev_paraphrases.json"):
        for row in json.loads((HERE / name).read_text(encoding="utf-8")):
            if row.get("needs_human_review"):
                continue
            rows.append(
                {"set": "dev", "id": str(row.get("id")), "question": row["question"]}
            )
    for name, label in (
        ("jev_holdout.json", "holdout"),
        ("jev_holdout_v2.json", "holdout_v2"),
    ):
        raw = subprocess.check_output(
            ["git", "show", f"platform/jev-multi-tool:services/agent/eval/{name}"]
        )
        for row in json.loads(raw.decode("utf-8")):
            if row.get("needs_human_review"):
                continue
            rows.append(
                {"set": label, "id": row["id"], "question": row["question"]}
            )
    return rows


def main() -> None:
    stored = _load()
    tokens = int(stored["tokens"])
    settings = AgentSettings.from_env()
    backend = TypeSafeBackend(model=settings.jev_model, timeout_seconds=20)
    done_v3 = {row["id"] for row in stored["v3"]}
    for row in _v3_rows():
        if row["id"] in done_v3:
            continue
        if _over(tokens):
            stored["stopped"] = True
            stored["tokens"] = tokens
            OUT.write_text(json.dumps(stored), encoding="utf-8")
            print(f"STOP ${tokens * PRICE:.3f}", flush=True)
            return
        unit = {
            "question": row["question"],
            "tools": candidate_tools(row["question"]),
            "case": {"id": row["id"]},
            "source": "holdout",
        }
        shot = _run_calls(backend, unit, "v3_hybrid")
        tokens += int(shot["tokens"])
        facts = facts_from_answers(shot["answers"])
        outcome = derive_outcome(facts, question=row["question"])
        measure = shot["answers"].get("measure")
        stored["v3"].append(
            {
                **row,
                "jev": None if shot["errors"] and not shot["answers"] else outcome.disposition,
                "measure": None if measure is None else measure.value,
                "measure_confidence": None
                if measure is None
                else getattr(measure, "confidence", None),
                "error": bool(shot["errors"] and not shot["answers"]),
            }
        )
        stored["tokens"] = tokens
        if len(stored["v3"]) % 10 == 0:
            OUT.write_text(json.dumps(stored), encoding="utf-8")
            print(f"v3 {len(stored['v3'])} ${tokens * PRICE:.3f}", flush=True)
    done_m = {(row["set"], row["id"], row["repeat"]) for row in stored["measure"]}
    for repeat in range(5):
        for row in _measure_rows():
            key = (row["set"], row["id"], repeat)
            if key in done_m:
                continue
            if _over(tokens):
                stored["stopped"] = True
                stored["tokens"] = tokens
                OUT.write_text(json.dumps(stored), encoding="utf-8")
                print(f"STOP ${tokens * PRICE:.3f}", flush=True)
                return
            unit = {
                "question": row["question"],
                "tools": candidate_tools(row["question"]),
                "case": {"id": row["id"]},
                "source": row["set"],
            }
            calls = _calls(unit, "v3_hybrid")
            topic = next(call for call in calls if call["name"] == "topic")
            result = backend.evaluate(
                topic["state"],
                topic["questions"],
                request_id=str(row["id"]),
                question_hash="topic",
            )
            tokens += int(getattr(result, "input_tokens", 0) or 0)
            answer = None if result is None or result.error else result.answers.get("measure")
            stored["measure"].append(
                {
                    "set": row["set"],
                    "id": row["id"],
                    "repeat": repeat,
                    "value": None if answer is None else answer.value,
                    "confidence": None
                    if answer is None
                    else getattr(answer, "confidence", None),
                    "error": answer is None,
                }
            )
            stored["tokens"] = tokens
            done_m.add(key)
            if len(stored["measure"]) % 25 == 0:
                OUT.write_text(json.dumps(stored), encoding="utf-8")
                print(f"measure {len(stored['measure'])} ${tokens * PRICE:.3f}", flush=True)
    stored["tokens"] = tokens
    stored["stopped"] = False
    stored["backstops"] = sorted(
        {f"unsupported_{key}" for key in UNSUPPORTED}
        | {"risk_future_date", "city_needs_place", "hftd_constraint_unavailable"}
    )
    OUT.write_text(json.dumps(stored), encoding="utf-8")
    print(f"tokens {tokens} spend ${tokens * PRICE:.4f}", flush=True)


if __name__ == "__main__":
    main()
