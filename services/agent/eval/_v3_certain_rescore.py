"""Jev disposition on the 65 certain v3 rows. Live v3_hybrid payload, 5 repeats.

Stops before spend passes $1. No label edits.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

from services.agent.config import AgentSettings
from services.agent.decisions.jev_policy import derive_outcome, facts_from_answers
from services.agent.decisions.typesafe_backend import TypeSafeBackend
from services.agent.eval.jev_ablation import _run_calls
from services.agent.routing import candidate_tools, route_question

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "runs" / "v3_certain_jev.json"
PRICE = 0.042 / 1_000_000
SPEND_CAP = 1.0
# One pass. Use 5 only when question wording or facts change.
REPEATS = 1


def _disposition(path: str) -> str:
    if path == "clarification":
        return "clarify"
    if path == "unsupported":
        return "unsupported"
    return "answer"


def _router(question: str) -> tuple[str, str]:
    decision = route_question(question)
    return _disposition(decision.path), decision.rule


def _questions() -> list[dict]:
    raw = subprocess.check_output(
        ["git", "show", "platform/jev-multi-tool:services/agent/eval/jev_holdout_v3.json"]
    )
    hold = json.loads(raw.decode("utf-8"))
    labels = json.loads(
        (HERE / "jev_holdout_v3_labels_chatgpt.json").read_text(encoding="utf-8")
    )
    rows = []
    for index, label in enumerate(labels):
        if label.get("uncertain"):
            continue
        item = hold[index]
        rows.append(
            {
                "n": label["id"],
                "id": item["id"],
                "question": item["question"],
                "label": label["disposition"],
                "reason": label.get("reason"),
            }
        )
    return rows


def main() -> None:
    rows = _questions()
    assert len(rows) == 65, len(rows)
    stored = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {
        "rows": [],
        "tokens": 0,
        "stopped": False,
    }
    done = {(row["repeat"], row["id"]) for row in stored["rows"]}
    tokens = int(stored["tokens"])
    settings = AgentSettings.from_env()
    backend = TypeSafeBackend(model=settings.jev_model, timeout_seconds=20)
    for repeat in range(REPEATS):
        for row in rows:
            key = (repeat, row["id"])
            if key in done:
                continue
            if tokens * PRICE >= SPEND_CAP:
                stored["stopped"] = True
                stored["tokens"] = tokens
                OUT.write_text(json.dumps(stored), encoding="utf-8")
                print(f"STOP spend ${tokens * PRICE:.3f}", flush=True)
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
            stored["rows"].append(
                {
                    "repeat": repeat,
                    "id": row["id"],
                    "n": row["n"],
                    "disposition": None if shot["errors"] and not shot["answers"] else outcome.disposition,
                    "error": bool(shot["errors"] and not shot["answers"]),
                    "tokens": shot["tokens"],
                }
            )
            stored["tokens"] = tokens
            done.add(key)
            if len(stored["rows"]) % 10 == 0:
                OUT.write_text(json.dumps(stored), encoding="utf-8")
                print(
                    f"repeat {repeat + 1} n={len(stored['rows'])} ${tokens * PRICE:.3f}",
                    flush=True,
                )
        OUT.write_text(json.dumps(stored), encoding="utf-8")
        print(f"repeat {repeat + 1} done ${tokens * PRICE:.3f}", flush=True)
    stored["tokens"] = tokens
    stored["stopped"] = False
    OUT.write_text(json.dumps(stored), encoding="utf-8")
    print(f"tokens {tokens} spend ${tokens * PRICE:.4f}", flush=True)


if __name__ == "__main__":
    main()
