"""Store Jev disposition calls once, then replay AGENT_JEV_MODE=decide from the store.

    capture  one Jev pass (facts, topic, places; v3_hybrid) per scored question -> store
    replay   router alone, Jev alone, and decide mode from the store, against the labels
    live     dev only: the runtime decide_live path, compared question by question
             with the replay of the same store

Sets. dev: cases.json + jev_paraphrases.json (used for tuning). v1: jev_holdout.json.
v2: jev_holdout_v2.json. Holdout rows marked
needs_human_review are left out. v3: jev_holdout_v3_questions.json (on main since PR #46) with the 65 rows the
independent ChatGPT labels made certain; v3 was partly tuned on and is labeled tuned.
smoke: the six questions scripts/smoke_test.sh asks production (SMOKE_CHECKS), each with
the route the smoke test expects. A smoke row replays from its own stored call, or from a
stored call for the same question text in another set; rows with no stored call are
reported as not stored and are captured on the next capture run. The offline decide
checks on these questions (tests/agent/test_jev_decide_scope.py) also run them against
adverse synthetic Jev answers, so decide-mode regressions on them are caught without
any Jev call.

    python -m services.agent.eval.jev_decide_replay capture --cap-usd 0.3
    python -m services.agent.eval.jev_decide_replay capture --sets smoke --cap-usd 0.05
    python -m services.agent.eval.jev_decide_replay replay
    python -m services.agent.eval.jev_decide_replay sweep-gate
    python -m services.agent.eval.jev_decide_replay live --cap-usd 0.1
"""

from __future__ import annotations

import argparse
import json
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
TUNED = {
    "dev": "used for tuning",
    "v1": "seen, now development data",
    "v2": "seen, now development data",
    "v3": "tuned (router fixes written from its disagreements)",
    "smoke": "production smoke test questions (scripts/smoke_test.sh)",
}

# Mirrors the six /ask checks in scripts/smoke_test.sh: the question, the route the
# smoke test expects (path None where the smoke test accepts more than one route),
# and the acceptable dispositions.
SMOKE_CHECKS: list[dict[str, Any]] = [
    {
        "id": "single",
        "question": "How many PG&E utility-attributed ignitions were there in 2024?",
        "path": "deterministic",
        "rule": "filtered_records",
        "labels": ["answer"],
    },
    {
        "id": "multi",
        "question": "Give me the 2022 ignition count for PG&E, SCE, and SDG&E",
        "path": None,
        "rule": None,
        "labels": ["answer", "clarify"],
    },
    {
        "id": "modesto",
        "question": "What utility service territory contains Modesto?",
        "path": "deterministic",
        "rule": "city_point_context",
        "labels": ["answer"],
    },
    {
        "id": "modesto_count",
        "question": "How many CAL FIRE incidents were there in Modesto in 2023?",
        "path": "clarification",
        "rule": "city_needs_place",
        "labels": ["clarify"],
    },
    {
        "id": "epss_rank",
        "question": "Rank utilities by EPSS events in 2023",
        "path": "unsupported",
        "rule": "unsupported_rank_epss_utility",
        "labels": ["unsupported"],
    },
    {
        "id": "live",
        "question": "What wildfires are burning right now?",
        "path": "unsupported",
        "rule": "unsupported_live_web",
        "labels": ["unsupported"],
    },
]


def smoke_route_matches(check: dict[str, Any], decision: Any) -> bool:
    """True when a route (final or router) is the one the smoke test expects."""
    if check["path"] is None:
        return ROUTER_DISPOSITION[decision.path] in check["labels"]
    return decision.path == check["path"] and decision.rule == check["rule"]


def stored_row(store: dict[str, Any], set_name: str, item: dict[str, Any]) -> dict[str, Any] | None:
    """The stored call for a row: its own key, or for smoke the same question text elsewhere."""
    rows = store["rows"]
    own = rows.get(f"{set_name}|{item['id']}")
    if own is not None or set_name != "smoke":
        return own
    wanted = " ".join(item["question"].split()).lower()
    for row in rows.values():
        if " ".join(row["question"].split()).lower() == wanted and not row.get("error"):
            return row
    return None


def _disposition_labels(expected: dict[str, Any]) -> list[str]:
    branches = expected.get("acceptable_outcomes")
    if branches:
        return sorted({branch["disposition"] for branch in branches})
    value = expected.get("disposition")
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value] if value else []


def load_sets() -> dict[str, list[dict[str, Any]]]:
    sets: dict[str, list[dict[str, Any]]] = {"dev": [], "v1": [], "v2": [], "v3": [], "smoke": []}
    for name, source in (("cases.json", "cases"), ("jev_paraphrases.json", "paraphrases")):
        for case in json.loads((HERE / name).read_text(encoding="utf-8")):
            expected = expected_for(case, source)
            if expected.get("needs_human_review"):
                continue
            sets["dev"].append({"id": f"{source}:{case['id']}", "question": case["question"], "labels": _disposition_labels(expected)})
    holdouts = {
        "v1": json.loads((HERE / "jev_holdout.json").read_text(encoding="utf-8")),
        "v2": json.loads((HERE / "jev_holdout_v2.json").read_text(encoding="utf-8")),
    }
    for name, rows in holdouts.items():
        for row in rows:
            if row.get("needs_human_review"):
                continue
            sets[name].append({"id": row["id"], "question": row["question"], "labels": [row["expected_disposition"]]})
    v3 = json.loads((HERE / "jev_holdout_v3_questions.json").read_text(encoding="utf-8"))
    chatgpt = json.loads((HERE / "jev_holdout_v3_labels_chatgpt.json").read_text(encoding="utf-8"))
    for index, label in enumerate(chatgpt):
        if "disposition" not in label or label.get("uncertain"):
            continue
        row = v3[index]
        sets["v3"].append({"id": row["id"], "question": row["question"], "labels": [label["disposition"]]})
    for check in SMOKE_CHECKS:
        sets["smoke"].append({"id": check["id"], "question": check["question"], "labels": list(check["labels"])})
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
    spent = 0  # input tokens this run; the cap is per run, not the store's lifetime total
    wanted = set(args.sets.split(",")) if args.sets else None
    for set_name, items in load_sets().items():
        if wanted is not None and set_name not in wanted:
            continue
        for item in items:
            key = f"{set_name}|{item['id']}"
            if key in rows and not rows[key].get("error"):
                continue
            if set_name == "smoke" and stored_row(store, set_name, item) is not None:
                continue
            decision = route_question(item["question"])
            # Exempt routes are stored too, so "Jev alone" is scored on every row.
            # The decide replay, like the runtime, ignores Jev on them.
            if spent * INPUT_USD_PER_MILLION / 1e6 >= args.cap_usd:
                print(f"STOP: this run spent ${spent * INPUT_USD_PER_MILLION / 1e6:.4f}, cap ${args.cap_usd}")
                STORE.write_text(json.dumps(store, indent=1), encoding="utf-8")
                return 2
            answers, error, used = ask_jev(backend, item["question"], today)
            tokens += used
            spent += used
            rows[key] = {
                "question": item["question"],
                "answers": {name: answer_to_json(answer) for name, answer in answers.items()} or None,
                "error": error,
                "exempt": exemption(decision, item["question"]),
            }
            if settings.jev_backend != store.get("backend") or settings.jev_model != store.get("model_request"):
                # A row captured through a different backend or model than the store's
                # first pass is labeled, so cross-backend rows are never mistaken for it.
                rows[key]["backend"] = settings.jev_backend
                rows[key]["model_request"] = settings.jev_model
                rows[key]["captured"] = date.today().isoformat()
            store["input_tokens"] = tokens
            if len(rows) % 20 == 0:
                STORE.write_text(json.dumps(store, indent=1), encoding="utf-8")
                print(f"{len(rows)} stored ${tokens * INPUT_USD_PER_MILLION / 1e6:.4f}", flush=True)
    store["input_tokens"] = tokens
    STORE.write_text(json.dumps(store, indent=1), encoding="utf-8")
    print(f"stored {len(rows)} rows, {tokens} input tokens, ${tokens * INPUT_USD_PER_MILLION / 1e6:.4f} lifetime; this run {spent} input tokens, ${spent * INPUT_USD_PER_MILLION / 1e6:.4f}")
    return 0


def _score(labels: list[str], disposition: str | None) -> bool:
    return disposition in labels


def replay(args: argparse.Namespace) -> int:
    store = json.loads(STORE.read_text(encoding="utf-8"))
    today = date.fromisoformat(store["today"])
    report: dict[str, Any] = {"today": store["today"], "gate": args.gate, "answer_gate": args.answer_gate, "sets": {}, "rows": {}}
    for set_name, items in load_sets().items():
        tally = {"n": 0, "router": 0, "jev": 0, "decide": 0, "jev_errors": 0, "exempt": 0, "jev_won": 0, "overrides_right": 0, "overrides_wrong": 0}
        checks = {check["id"]: check for check in SMOKE_CHECKS} if set_name == "smoke" else {}
        for item in items:
            key = f"{set_name}|{item['id']}"
            stored = stored_row(store, set_name, item)
            if stored is None:
                if set_name == "smoke":
                    report["rows"][key] = {"question": item["question"], "labels": item["labels"], "stored": False}
                    print(f"smoke {item['id']}: not stored (captured on the next capture run)")
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
            if item["id"] in checks:
                ok = smoke_route_matches(checks[item["id"]], result.decision)
                report["rows"][key]["smoke_route_ok"] = ok
                if not ok:
                    print(f"SMOKE ROUTE CHANGED {item['id']}: decide gave {result.decision.path}/{result.decision.rule}")
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


GATE_SWEEP_SETS = ("dev", "v1", "v2", "v3")
GATE_SWEEP_VALUES = tuple(round(0.5 + 0.05 * step, 2) for step in range(10))


def _outcome_key(result: Any) -> tuple[str, str | None, str]:
    return (result.decision.path, result.decision.rule, result.winner)


def sweep_gate(args: argparse.Namespace) -> int:
    """Sweep the decline gate from 0.50 to 0.95 with the answer gate fixed. Report only.

    dev, v1, and v2 are already seen data; v3 is tuned and reported separately. This
    changes no default: the gate stays whatever config sets.
    """
    store = json.loads(STORE.read_text(encoding="utf-8"))
    today = date.fromisoformat(store["today"])
    sets = load_sets()
    rows: list[dict[str, Any]] = []
    outcomes: dict[float, dict[str, tuple[str, str | None, str]]] = {}
    for gate in GATE_SWEEP_VALUES:
        row: dict[str, Any] = {"gate": gate}
        outcomes[gate] = {}
        for set_name in GATE_SWEEP_SETS:
            n = hits = fixed = broken = decided = 0
            for item in sets[set_name]:
                key = f"{set_name}|{item['id']}"
                stored = store["rows"].get(key)
                if stored is None:
                    continue
                decision = route_question(item["question"])
                answers = _answers(stored["answers"]) if stored.get("answers") else None
                result = decide_from_answers(
                    item["question"], decision, answers, gate=gate, answer_gate=args.answer_gate,
                    error=stored.get("error"), today=today,
                )
                outcomes[gate][key] = _outcome_key(result)
                router_ok = _score(item["labels"], ROUTER_DISPOSITION[decision.path])
                decide_ok = _score(item["labels"], ROUTER_DISPOSITION[result.decision.path])
                n += 1
                hits += decide_ok
                if result.winner == "jev":
                    decided += 1
                    fixed += decide_ok and not router_ok
                    broken += router_ok and not decide_ok
            row[set_name] = {"n": n, "acc": round(hits / (n or 1), 3), "fixed": fixed, "broken": broken, "jev_decided": decided, "status": TUNED[set_name]}
        rows.append(row)
    changes: list[dict[str, Any]] = []
    for lower, upper in zip(GATE_SWEEP_VALUES, GATE_SWEEP_VALUES[1:]):
        for key, before in outcomes[lower].items():
            after = outcomes[upper][key]
            if before != after:
                set_name, item_id = key.split("|", 1)
                question = next(item["question"] for item in sets[set_name] if item["id"] == item_id)
                changes.append({"from_gate": lower, "to_gate": upper, "set": set_name, "id": item_id, "question": question, "before": list(before), "after": list(after)})
    out = {"answer_gate": args.answer_gate, "sets": GATE_SWEEP_SETS, "status": {name: TUNED[name] for name in GATE_SWEEP_SETS}, "rows": rows, "changes": changes}
    (HERE / "runs" / "jev_decide_gate_sweep.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    header = "| Gate | " + " | ".join(f"{name} acc | {name} fixed / broken | {name} Jev decided" for name in GATE_SWEEP_SETS) + " |"
    print(header)
    print("|" + "---|" * (1 + 3 * len(GATE_SWEEP_SETS)))
    for row in rows:
        cells = [f"{row['gate']:.2f}"]
        for name in GATE_SWEEP_SETS:
            body = row[name]
            cells += [f"{body['acc']:.3f}", f"{body['fixed']} / {body['broken']}", str(body["jev_decided"])]
        print("| " + " | ".join(cells) + " |")
    print()
    for change in changes:
        print(f"{change['from_gate']:.2f} -> {change['to_gate']:.2f} {change['set']} {change['id']}: {change['before']} -> {change['after']}  {change['question']}")
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
    parser.add_argument("command", choices=("capture", "replay", "sweep", "sweep-gate", "live"))
    parser.add_argument("--cap-usd", type=float, default=0.3)
    parser.add_argument("--gate", type=float, default=0.8)
    parser.add_argument("--answer-gate", type=float, default=0.9)
    parser.add_argument("--sets", default=None, help="capture only these comma-separated sets, for example smoke")
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    return {"capture": capture, "replay": replay, "sweep": sweep, "sweep-gate": sweep_gate, "live": live}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
