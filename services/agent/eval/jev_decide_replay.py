"""Store Jev disposition calls once, then replay AGENT_JEV_MODE=decide from the store.

    capture  one Jev pass (facts, topic, places; v3_hybrid) per scored question -> store
    replay   router alone, Jev alone, and decide mode from the store, against the labels
    live     dev only: the runtime decide_live path, compared question by question
             with the replay of the same store

Sets. dev: cases.json + jev_paraphrases.json (used for tuning). v1: jev_holdout.json.
v2: jev_holdout_v2.json from platform/jev-multi-tool. Holdout rows marked
needs_human_review are left out. v3: jev_holdout_v3_questions.json (renamed from
jev_holdout_v3.json on jev-multi-tool; same ids, questions, and order) with the 65 rows the
independent ChatGPT labels made certain; v3 was partly tuned on and is labeled tuned.

    python -m services.agent.eval.jev_decide_replay capture --cap-usd 0.3
    python -m services.agent.eval.jev_decide_replay replay
    python -m services.agent.eval.jev_decide_replay live --cap-usd 0.1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any

from services.agent.decisions.backend import Answer
from services.agent.decisions.decide_mode import (
    ROUTER_DISPOSITION,
    ask_jev,
    decide_from_answers,
    decide_live,
    exemption,
)
from services.agent.decisions.integrity import answer_to_json
from services.agent.decisions.jev_policy import derive_outcome, facts_from_answers
from services.agent.eval.jev_metrics import INPUT_USD_PER_MILLION
from services.agent.eval.jev_offline_eval import expected_for
from services.agent.routing import route_question
from shared.db import REPO_ROOT

HERE = Path(__file__).resolve().parent
STORE = HERE / "runs" / "jev_decide_store.json"
REPORT = HERE / "runs" / "jev_decide_replay.json"
LIVE = HERE / "runs" / "jev_decide_live_dev.json"
TUNED = {"dev": "used for tuning", "v1": "seen, now development data", "v2": "seen, now development data", "v3": "tuned (router fixes written from its disagreements)"}


def _git_json(ref_path: str) -> Any:
    raw = subprocess.check_output(["git", "show", ref_path], cwd=REPO_ROOT)
    return json.loads(raw.decode("utf-8"))


def _disposition_labels(expected: dict[str, Any]) -> list[str]:
    branches = expected.get("acceptable_outcomes")
    if branches:
        return sorted({branch["disposition"] for branch in branches})
    value = expected.get("disposition")
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value] if value else []


def load_sets() -> dict[str, list[dict[str, Any]]]:
    sets: dict[str, list[dict[str, Any]]] = {"dev": [], "v1": [], "v2": [], "v3": []}
    for name, source in (("cases.json", "cases"), ("jev_paraphrases.json", "paraphrases")):
        for case in json.loads((HERE / name).read_text(encoding="utf-8")):
            expected = expected_for(case, source)
            if expected.get("needs_human_review"):
                continue
            sets["dev"].append({"id": f"{source}:{case['id']}", "question": case["question"], "labels": _disposition_labels(expected)})
    holdouts = {
        "v1": json.loads((HERE / "jev_holdout.json").read_text(encoding="utf-8")),
        "v2": _git_json("platform/jev-multi-tool:services/agent/eval/jev_holdout_v2.json"),
    }
    for name, rows in holdouts.items():
        for row in rows:
            if row.get("needs_human_review"):
                continue
            sets[name].append({"id": row["id"], "question": row["question"], "labels": [row["expected_disposition"]]})
    v3 = _git_json("platform/jev-multi-tool:services/agent/eval/jev_holdout_v3_questions.json")
    chatgpt = json.loads((HERE / "jev_holdout_v3_labels_chatgpt.json").read_text(encoding="utf-8"))
    for index, label in enumerate(chatgpt):
        if "disposition" not in label or label.get("uncertain"):
            continue
        row = v3[index]
        sets["v3"].append({"id": row["id"], "question": row["question"], "labels": [label["disposition"]]})
    return sets


def _answers(stored: dict[str, Any]) -> dict[str, Answer]:
    return {
        name: Answer(kind=item["kind"], value=item["value"], confidence=item["confidence"], probabilities=item["probabilities"])
        for name, item in stored.items()
    }


def _backend(timeout: float):
    from services.agent.config import AgentSettings
    from services.agent.decisions.typesafe_backend import make_backend, reset_for_tests

    reset_for_tests()
    settings = AgentSettings.from_env()
    return settings, make_backend(settings.jev_backend, model=settings.jev_model, timeout_seconds=timeout)


def capture(args: argparse.Namespace) -> int:
    settings, backend = _backend(30)
    store = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {}
    today = store.get("today") or date.today().isoformat()
    store.setdefault("today", today)
    store.setdefault("backend", settings.jev_backend)
    store.setdefault("model_request", settings.jev_model)
    rows = store.setdefault("rows", {})
    tokens = int(store.get("input_tokens", 0))
    for set_name, items in load_sets().items():
        for item in items:
            key = f"{set_name}|{item['id']}"
            if key in rows and not rows[key].get("error"):
                continue
            decision = route_question(item["question"])
            # Exempt routes are stored too, so "Jev alone" is scored on every row.
            # The decide replay, like the runtime, ignores Jev on them.
            if tokens * INPUT_USD_PER_MILLION / 1e6 >= args.cap_usd:
                print(f"STOP at ${tokens * INPUT_USD_PER_MILLION / 1e6:.4f}")
                STORE.write_text(json.dumps(store, indent=1), encoding="utf-8")
                return 2
            answers, error, used = ask_jev(backend, item["question"], today)
            tokens += used
            rows[key] = {
                "question": item["question"],
                "answers": {name: answer_to_json(answer) for name, answer in answers.items()} or None,
                "error": error,
                "exempt": exemption(decision),
            }
            store["input_tokens"] = tokens
            if len(rows) % 20 == 0:
                STORE.write_text(json.dumps(store, indent=1), encoding="utf-8")
                print(f"{len(rows)} stored ${tokens * INPUT_USD_PER_MILLION / 1e6:.4f}", flush=True)
    store["input_tokens"] = tokens
    STORE.write_text(json.dumps(store, indent=1), encoding="utf-8")
    print(f"stored {len(rows)} rows, {tokens} input tokens, ${tokens * INPUT_USD_PER_MILLION / 1e6:.4f}")
    return 0


def _score(labels: list[str], disposition: str | None) -> bool:
    return disposition in labels


def replay(args: argparse.Namespace) -> int:
    store = json.loads(STORE.read_text(encoding="utf-8"))
    today = date.fromisoformat(store["today"])
    report: dict[str, Any] = {"today": store["today"], "gate": args.gate, "answer_gate": args.answer_gate, "sets": {}, "rows": {}}
    for set_name, items in load_sets().items():
        tally = {"n": 0, "router": 0, "jev": 0, "decide": 0, "jev_errors": 0, "exempt": 0, "jev_won": 0, "overrides_right": 0, "overrides_wrong": 0}
        for item in items:
            key = f"{set_name}|{item['id']}"
            stored = store["rows"].get(key)
            if stored is None:
                continue
            decision = route_question(item["question"])
            router_disp = ROUTER_DISPOSITION[decision.path]
            answers = _answers(stored["answers"]) if stored.get("answers") else None
            jev_disp = (
                derive_outcome(facts_from_answers(answers), question=item["question"], today=today).disposition
                if answers
                else None
            )
            result = decide_from_answers(item["question"], decision, answers, gate=args.gate, answer_gate=args.answer_gate, error=stored.get("error"), today=today)
            decide_disp = ROUTER_DISPOSITION[result.decision.path]
            tally["n"] += 1
            tally["router"] += _score(item["labels"], router_disp)
            tally["jev"] += _score(item["labels"], jev_disp)
            tally["decide"] += _score(item["labels"], decide_disp)
            tally["jev_errors"] += bool(stored.get("error"))
            tally["exempt"] += bool(stored.get("exempt"))
            if result.winner == "jev":
                tally["jev_won"] += 1
                if _score(item["labels"], decide_disp) and not _score(item["labels"], router_disp):
                    tally["overrides_right"] += 1
                elif _score(item["labels"], router_disp) and not _score(item["labels"], decide_disp):
                    tally["overrides_wrong"] += 1
            report["rows"][key] = {
                "question": item["question"],
                "labels": item["labels"],
                "router": [decision.path, decision.rule],
                "jev": [jev_disp, result.jev_rule, result.jev_confidence],
                "decide": [result.decision.path, result.decision.rule, result.winner, result.why],
            }
        n = tally["n"] or 1
        report["sets"][set_name] = {
            **tally,
            "router_acc": round(tally["router"] / n, 3),
            "jev_acc": round(tally["jev"] / n, 3),
            "decide_acc": round(tally["decide"] / n, 3),
            "status": TUNED[set_name],
        }
    REPORT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    for name, body in report["sets"].items():
        print(f"{name:3} n={body['n']:3} router {body['router_acc']:.3f}  jev {body['jev_acc']:.3f}  decide {body['decide_acc']:.3f}  "
              f"jev_won {body['jev_won']} (right {body['overrides_right']}, wrong {body['overrides_wrong']})  "
              f"exempt {body['exempt']} errors {body['jev_errors']}  [{body['status']}]")
    return 0


SWEEP_SETS = ("dev", "v1")
SWEEP_ANSWER_GATES = (0.8, 0.85, 0.9, 0.95, 1.01)


def sweep(args: argparse.Namespace) -> int:
    """Choose the answer gate from dev and v1 only. v3 is frozen and never read here."""
    store = json.loads(STORE.read_text(encoding="utf-8"))
    today = date.fromisoformat(store["today"])
    sets = load_sets()
    rows = []
    for answer_gate in SWEEP_ANSWER_GATES:
        row: dict[str, Any] = {"answer_gate": answer_gate}
        for set_name in SWEEP_SETS:
            n = hits = fixed = broken = answer_overrides = 0
            for item in sets[set_name]:
                stored = store["rows"].get(f"{set_name}|{item['id']}")
                if stored is None:
                    continue
                decision = route_question(item["question"])
                answers = _answers(stored["answers"]) if stored.get("answers") else None
                result = decide_from_answers(
                    item["question"], decision, answers, gate=args.gate, answer_gate=answer_gate,
                    error=stored.get("error"), today=today,
                )
                router_ok = _score(item["labels"], ROUTER_DISPOSITION[decision.path])
                decide_ok = _score(item["labels"], ROUTER_DISPOSITION[result.decision.path])
                n += 1
                hits += decide_ok
                if result.winner == "jev":
                    fixed += decide_ok and not router_ok
                    broken += router_ok and not decide_ok
                    answer_overrides += result.decision.rule == "jev_decide_answer"
            row[set_name] = {"n": n, "acc": round(hits / (n or 1), 3), "fixed": fixed, "broken": broken, "answer_overrides": answer_overrides}
        rows.append(row)
        print(
            f"answer_gate {answer_gate:4}: "
            + "  ".join(
                f"{name} acc {row[name]['acc']:.3f} fixed {row[name]['fixed']} broken {row[name]['broken']} answer-overrides {row[name]['answer_overrides']}"
                for name in SWEEP_SETS
            )
        )
    (HERE / "runs" / "jev_decide_answer_gate_sweep.json").write_text(
        json.dumps({"gate": args.gate, "sets": SWEEP_SETS, "v3_used": False, "rows": rows}, indent=1),
        encoding="utf-8",
    )
    return 0


def live(args: argparse.Namespace) -> int:
    """Dev only: run the runtime path and compare with the replay of the store."""
    store = json.loads(STORE.read_text(encoding="utf-8"))
    today = date.fromisoformat(store["today"])
    settings, backend = _backend(30)
    rows = []
    tokens = 0
    for item in load_sets()["dev"]:
        key = f"dev|{item['id']}"
        stored = store["rows"].get(key)
        if stored is None:
            continue
        if tokens * INPUT_USD_PER_MILLION / 1e6 >= args.cap_usd:
            print(f"STOP at ${tokens * INPUT_USD_PER_MILLION / 1e6:.4f}")
            break
        decision = route_question(item["question"])
        started = time.perf_counter()
        runtime = decide_live(item["question"], decision, backend=backend, gate=args.gate, answer_gate=args.answer_gate, today=today)
        tokens += runtime.input_tokens
        answers = _answers(stored["answers"]) if stored.get("answers") else None
        replayed = decide_from_answers(item["question"], decision, answers, gate=args.gate, answer_gate=args.answer_gate, error=stored.get("error"), today=today)
        rows.append({
            "id": item["id"],
            "runtime": [runtime.decision.path, runtime.decision.rule, runtime.winner, runtime.jev_disposition, runtime.jev_confidence],
            "replay": [replayed.decision.path, replayed.decision.rule, replayed.winner, replayed.jev_disposition, replayed.jev_confidence],
            "same_final": (runtime.decision.path, runtime.decision.rule) == (replayed.decision.path, replayed.decision.rule),
            "same_jev": runtime.jev_disposition == replayed.jev_disposition,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": runtime.error,
        })
    same = sum(row["same_final"] for row in rows)
    latencies = sorted(row["latency_ms"] for row in rows if row["runtime"][2] is not None)
    asked = [row for row in rows if row["runtime"][3] is not None or row["error"]]
    asked_lat = sorted(row["latency_ms"] for row in asked)

    def pct(values: list[float], q: float) -> float:
        return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else 0.0

    summary = {
        "questions": len(rows),
        "same_final_decision": same,
        "same_jev_disposition": sum(row["same_jev"] for row in rows),
        "jev_asked": len(asked),
        "errors": sum(1 for row in rows if row["error"]),
        "p50_ms_when_asked": pct(asked_lat, 0.5),
        "p95_ms_when_asked": pct(asked_lat, 0.95),
        "input_tokens": tokens,
        "cost_usd": round(tokens * INPUT_USD_PER_MILLION / 1e6, 4),
    }
    LIVE.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    for row in rows:
        if not row["same_final"]:
            print("DIFF", row)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("capture", "replay", "sweep", "live"))
    parser.add_argument("--cap-usd", type=float, default=0.3)
    parser.add_argument("--gate", type=float, default=0.8)
    parser.add_argument("--answer-gate", type=float, default=0.9)
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    return {"capture": capture, "replay": replay, "sweep": sweep, "live": live}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
