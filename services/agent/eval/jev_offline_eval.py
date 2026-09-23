"""Score Jev against eval cases without Ollama or the backend services.

Accuracy is against the case files. Regex-versus-Jev is reported as agreement.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.agent.decisions.backend import QuestionSpec
from services.agent.decisions.integrity import question_hash, replay_mismatch
from services.agent.decisions.mapping import (
    agreement,
    contaminated_case,
    derive_case_labels,
    regex_labels,
)
from services.agent.decisions.schemas import (
    routing_questions,
    state_for,
    tool_pick_questions,
)
from services.agent.eval.jev_metrics import (
    accuracy,
    calibration,
    categorized_summary,
    check_record_parse,
    choice_confidence,
    choice_value,
    confusion,
    cost_line,
    field_applies,
    labels_match,
    latency_line,
    noul_metrics,
    parse_stats,
    rate,
    sanity_lines,
    score_acceptable_outcomes,
    write_review_csv,
)
from services.agent.routing import candidate_tools, route_question
from shared.db import REPO_ROOT

HERE = Path(__file__).resolve().parent
CASES_FILE = HERE / "cases.json"
PARAPHRASE_FILE = HERE / "jev_paraphrases.json"
RUNS = HERE / "runs"


def load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return [
            item
            for item in payload.get("cases") or []
            if isinstance(item, dict) and item.get("id")
        ]
    return payload


def load_blind_cases() -> list[dict[str, Any]]:
    path = HERE / "jev_blind_questions.json"
    if not path.exists():
        return []
    return load_cases(path)


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def build_jobs(
    case: dict[str, Any],
    *,
    source: str,
    tool_pick_all: bool = False,
) -> list[dict[str, Any]]:
    question = str(case["question"])
    decision = route_question(question)
    today = _today()
    state = state_for(question, today)
    jobs = [
        {
            "kind": "routing",
            "case": case,
            "source": source,
            "question": question,
            "decision": decision,
            "state": state,
            "questions": routing_questions(),
        }
    ]
    expected_route = case.get("expected_route")
    disposition = case.get("expected_disposition")
    if source == "cases":
        from services.agent.decisions.mapping import derive_case_labels

        disposition = derive_case_labels(case).get("disposition")
    send_tool_pick = expected_route == "model" or (
        tool_pick_all and disposition == "answer"
    )
    if send_tool_pick:
        tools = candidate_tools(question)
        jobs.append(
            {
                "kind": "tool_pick",
                "diagnostic": bool(tool_pick_all and expected_route != "model"),
                "case": case,
                "source": source,
                "question": question,
                "decision": decision,
                "state": state,
                "questions": tool_pick_questions(tools),
                "candidate_tools": tools,
            }
        )
    return jobs


def payload(job: dict[str, Any], model: str) -> dict[str, Any]:
    questions: dict[str, QuestionSpec] = job["questions"]
    return {
        "state": job["state"],
        "model": model,
        "questions": {name: spec.payload() for name, spec in questions.items()},
    }


def expected_for(case: dict[str, Any], source: str) -> dict[str, Any]:
    if source == "paraphrases":
        expected = {
            "disposition": case.get("expected_disposition"),
            "intent": case.get("expected_intent"),
            "dataset": case.get("expected_dataset"),
            "tool_pick": case.get("expected_tool_pick") or (
                (case.get("expected_tools") or [None])[0]
            ),
            "comparison_kind": case.get("expected_comparison_kind"),
            "utilities": case.get("expected_utilities") or [],
            "clarify_reason": case.get("expected_clarify_reason"),
            "unsupported_topic": case.get("expected_unsupported_topic"),
            "needs_human_review": bool(case.get("needs_human_review")),
            "expected_facts": case.get("expected_facts"),
        }
        if expected.get("disposition") != "answer":
            expected["intent"] = None
            expected["dataset"] = None
            expected["tool_pick"] = None
        return expected
    return derive_case_labels(case)


def _show(value: Any) -> str:
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    if value is None:
        return "None"
    return str(value)


def print_conversion_table(cases: list[dict[str, Any]]) -> None:
    print("Expected-label conversion (cases.json). Review this before trusting intent accuracy.")
    print(
        "case_id | disposition | intent | tool_pick | clarify_reason | "
        "unsupported_topic | review"
    )
    rows = [derive_case_labels(case) for case in cases]
    for row in rows:
        branches = row.get("acceptable_outcomes")
        disposition = (
            "|".join(item["disposition"] for item in branches)
            if branches
            else _show(row["disposition"])
        )
        intent = (
            "|".join(
                str(item.get("intent"))
                for item in branches
                if item.get("intent") is not None
            )
            or _show(row["intent"])
            if branches
            else _show(row["intent"])
        )
        print(
            f"{row['case_id']} | {disposition} | {intent} | "
            f"{_show(row['tool_pick'])} | {_show(row['clarify_reason'])} | "
            f"{_show(row['unsupported_topic'])} | {row['needs_human_review']}"
        )
    intent_n = sum(
        1
        for row in rows
        if row["disposition"] == "answer" and row["intent"] and not row.get("acceptable_outcomes")
    )
    tool_n = sum(
        1
        for row in rows
        if row["expected_route"] == "model"
        and row["disposition"] == "answer"
        and row["tool_pick"]
    )
    clarify_n = sum(
        1
        for row in rows
        if row["disposition"] == "clarify"
        and row["clarify_reason"]
        and not row.get("acceptable_outcomes")
    )
    unsupported_n = sum(
        1
        for row in rows
        if row["disposition"] == "unsupported"
        and row["unsupported_topic"]
        and not row.get("acceptable_outcomes")
    )
    blank = [row["case_id"] for row in rows if row["disposition"] is None]
    print(
        f"scored n: clarify_reason={clarify_n} unsupported_topic={unsupported_n} "
        f"intent={intent_n} tool_pick={tool_n} (model route only) dataset=0 (not in cases.json)"
    )
    print(
        "utility_not_invented_from_place is scored by acceptable branch "
        "(unsupported/unexpressable_county_filter OR answer/calfire_incidents/count), "
        "not in the single-label n above."
    )
    print(f"blank disposition: {blank or 'none'}")


def _field_pairs(
    results: list[dict[str, Any]],
    field: str,
    *,
    source: str,
    applies,
) -> list[tuple[Any, Any]]:
    pairs = []
    for row in results:
        if row["source"] != source or row["kind"] != "routing":
            continue
        if row.get("error") or not row.get("jev_answers"):
            continue
        expected = row["expected"]
        if not applies(field, expected):
            continue
        pairs.append((expected.get(field), row["jev_answers"].get(field)))
    return pairs


def summarize(
    results: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> list[str]:
    stats = parse_stats([row["jev_record"] for row in results if row.get("jev_record")])
    stats["orphaned_joins"] = 0
    stats["replay_mismatches"] = "not run"
    versions = sorted({row.get("model_version") for row in results if row.get("model_version")})
    stats["model_versions"] = {version: sum(1 for row in results if row.get("model_version") == version) for version in versions}
    lines = sanity_lines(stats)
    lines.append("")
    lines.append("cases.json results were written alongside the regex router and structurally favor it.")
    contaminated = [case["id"] for case in cases if contaminated_case(case)]
    lines.append(
        "Potentially contaminated by domain.py SCE 2023 examples: "
        + (", ".join(contaminated) if contaminated else "none")
    )
    for source, title in (
        ("cases", "cases.json"),
        ("paraphrases", "jev_paraphrases.json"),
        ("blind", "jev_blind_questions.json"),
    ):
        if source == "blind" and not any(row["source"] == "blind" for row in results):
            continue
        lines.append("")
        lines.append(f"## {title}")
        subset = [row for row in results if row["source"] == source and row["kind"] == "routing"]
        regex_intent = accuracy(
            [
                (row["expected"].get("intent"), row["regex"].get("intent"))
                for row in subset
                if row["expected"].get("intent") is not None
                and not row["expected"].get("intent_excluded")
            ]
        )
        from services.agent.eval.jev_metrics import field_applies

        jev_intent = accuracy(
            _field_pairs(results, "intent", source=source, applies=field_applies)
        )
        lines.append(f"- intent agreement-with-expected regex {rate(*regex_intent)} (labeled agreement, not Jev accuracy)")
        lines.append(f"- intent accuracy Jev {rate(*jev_intent)} n={jev_intent[1]}")
        for field in (
            "disposition",
            "dataset",
            "clarify_reason",
            "unsupported_topic",
        ):
            pairs = []
            for row in subset:
                if row["expected"].get("acceptable_outcomes"):
                    continue
                if row.get("error") or not row.get("jev_answers"):
                    continue
                if not field_applies(field, row["expected"]):
                    continue
                expected_value = row["expected"].get(field)
                if expected_value is None:
                    continue
                actual = row["jev_answers"].get(field)
                pairs.append((expected_value, actual))
            correct, total = accuracy(pairs)
            lines.append(f"- {field} accuracy {rate(correct, total)} n={total}")
        tool_rows = [
            row
            for row in results
            if row["source"] == source
            and row["kind"] == "tool_pick"
            and not row.get("diagnostic")
        ]
        tool_pairs = []
        for row in tool_rows:
            if row.get("error") or not row.get("jev_answers"):
                continue
            if row["expected"].get("acceptable_outcomes"):
                continue
            if not field_applies("tool_pick", row["expected"]):
                continue
            tool_pairs.append(
                (row["expected"].get("tool_pick"), row["jev_answers"].get("tool_pick"))
            )
        tool_correct, tool_total = accuracy(tool_pairs)
        lines.append(
            f"- tool_pick accuracy {rate(tool_correct, tool_total)} n={tool_total} (model route only)"
        )
        diagnostic_rows = [
            row
            for row in results
            if row["source"] == source and row["kind"] == "tool_pick" and row.get("diagnostic")
        ]
        if diagnostic_rows:
            diagnostic_pairs = [
                (row["expected"].get("tool_pick"), row["jev_answers"].get("tool_pick"))
                for row in diagnostic_rows
                if row.get("jev_answers") and not row.get("error")
            ]
            d_correct, d_total = accuracy(diagnostic_pairs)
            lines.append(
                f"- tool_pick diagnostic (--tool-pick-all, not in the main metric) "
                f"{rate(d_correct, d_total)} n={d_total}"
            )
        from services.agent.eval.jev_metrics import score_acceptable_outcomes

        branched = [
            row
            for row in subset
            if row["expected"].get("acceptable_outcomes") and row.get("jev_answers") and not row.get("error")
        ]
        if branched:
            lines.append(
                "Note: utility_not_invented_from_place reflects a router policy under review. "
                "Either unsupported/unexpressable_county_filter or an answer with dataset "
                "calfire_incidents and intent count is correct. routing.py was not changed."
            )
            hits = sum(
                1
                for row in branched
                if score_acceptable_outcomes(
                    row["jev_answers"], row["expected"]["acceptable_outcomes"]
                )
            )
            lines.append(f"- acceptable-branch accuracy {rate(hits, len(branched))} n={len(branched)}")
        regex_disp = accuracy(
            [(row["expected"].get("disposition"), row["regex"].get("disposition")) for row in subset if row["expected"].get("disposition")]
        )
        lines.append(f"- disposition regex agreement with expected {rate(*regex_disp)}")
        agree_rows = [row for row in subset if row.get("agree")]
        if agree_rows:
            for field in ("disposition", "intent", "dataset", "utilities", "county"):
                hits = sum(1 for row in agree_rows if row["agree"].get(field))
                lines.append(f"- regex/Jev agreement {field} {rate(hits, len(agree_rows))} (agreement, not accuracy)")
        intent_pairs = [
            (str(expected), str(actual))
            for expected, actual in _field_pairs(
                results, "intent", source=source, applies=field_applies
            )
            if expected is not None and actual is not None
        ]
        disp_pairs = [
            (str(expected), str(actual))
            for expected, actual in _field_pairs(results, "disposition", source=source, applies=field_applies)
            if expected is not None and actual is not None
        ]
        lines.append(f"- intent confusion {dict(confusion(intent_pairs))}")
        lines.append(f"- disposition confusion {dict(confusion(disp_pairs))}")
        conf_pairs = []
        for row in subset:
            if row.get("error") or not row.get("jev_record"):
                continue
            expected = row["expected"].get("intent")
            actual = choice_value(row["jev_record"], "intent")
            confidence = choice_confidence(row["jev_record"], "intent")
            if expected is None or actual is None or confidence is None:
                continue
            if source == "cases" and row["expected"].get("intent_excluded"):
                continue
            conf_pairs.append((actual == expected, confidence))
        lines.extend(calibration(conf_pairs))
    latencies = [float(row["latency_ms"]) for row in results if row.get("latency_ms") is not None]
    tokens = sum(int(row.get("input_tokens") or 0) for row in results)
    lines.append("")
    lines.append("Latency " + latency_line(latencies))
    lines.append(cost_line(tokens))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--only-paraphrases", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--replay", type=int, default=0)
    parser.add_argument("--export-payloads", type=int, default=0)
    parser.add_argument("--reword", default="")
    parser.add_argument("--categorized", default="")
    parser.add_argument("--model", default="jev-latest")
    parser.add_argument(
        "--tool-pick-all",
        action="store_true",
        help="Also ask tool_pick for every answer case. Reported separately.",
    )
    parser.add_argument(
        "--blind",
        action="store_true",
        help="Include jev_blind_questions.json in its own table.",
    )
    parser.add_argument("--schema", default="v2", choices=("v2", "v3"))
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="One pass by default. Use 5 only when question wording or facts change.",
    )
    parser.add_argument("--repeat-test", type=int, default=0)
    parser.add_argument("--repeat-count", type=int, default=10)
    parser.add_argument("--budget-estimate", action="store_true")
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument(
        "--ablation-configs",
        default="",
        help="Comma-separated ablation configs. Default is all five.",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")

    cases = [] if args.only_paraphrases else load_cases(CASES_FILE)
    paraphrases = load_cases(PARAPHRASE_FILE)
    blind_cases = load_blind_cases() if args.blind else []
    if args.case_ids:
        wanted = {item.strip() for item in args.case_ids.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted]
        paraphrases = [case for case in paraphrases if case["id"] in wanted]
    print_conversion_table(cases)

    jobs: list[dict[str, Any]] = []
    for case in cases:
        jobs.extend(build_jobs(case, source="cases", tool_pick_all=args.tool_pick_all))
    for case in paraphrases:
        jobs.extend(
            build_jobs(case, source="paraphrases", tool_pick_all=args.tool_pick_all)
        )
    for case in blind_cases:
        jobs.extend(build_jobs(case, source="blind", tool_pick_all=args.tool_pick_all))
    if args.limit:
        jobs = jobs[: args.limit]

    if args.budget_estimate or args.repeat_test or args.ablation or args.schema == "v3":
        from services.agent.eval.jev_phase3 import dispatch

        return dispatch(args, jobs)

    if args.dry_run:
        from services.agent.decisions.schemas import DOMAIN_CONTEXT

        print("\n--- v2 context string ---")
        print(DOMAIN_CONTEXT.strip())
        print(f"\nDry run. {len(jobs)} request payloads. No API call.")
        for index, job in enumerate(jobs, start=1):
            print(f"\n--- [{index}/{len(jobs)}] {job['case'].get('id')} {job['kind']} ---")
            body = json.dumps(payload(job, args.model), indent=2)
            from services.agent.decisions.typesafe_backend import prepared_api_key

            secret = prepared_api_key()
            if secret and secret in body:
                body = body.replace(secret, "[redacted]")
            print(body)
        return 0

    from services.agent.decisions.typesafe_backend import TypeSafeBackend, prepared_api_key

    if not prepared_api_key():
        print(
            "\n".join(
                sanity_lines(
                    {
                        "records": 0,
                        "null_answers": 0,
                        "parse_mismatches": 0,
                        "unexpected_options": 0,
                        "wiring_errors": 0,
                        "orphaned_joins": 0,
                        "replay_mismatches": "not run",
                        "model_versions": {},
                    }
                )
            )
        )
        print("TYPESAFE_API_KEY is not set. Skipping live Jev calls.")
        return 0

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
    pinned = probe.model_version
    print(f"Pinned model version {pinned}")
    backend.model = pinned

    results: list[dict[str, Any]] = []
    repeat_hits: dict[str, list[list[bool]]] = {}
    intent_votes: dict[str, list[Any]] = {}
    total = len(jobs)
    for index, job in enumerate(jobs, start=1):
        case = job["case"]
        digest = question_hash(job["question"])
        shots = []
        for _ in range(max(1, args.repeats)):
            shot = backend.evaluate(
                job["state"],
                job["questions"],
                request_id=case.get("id") or digest[:12],
                question_hash=digest,
            )
            if shot and shot.model_version and shot.model_version != pinned:
                print(
                    f"Abort: response model {shot.model_version} != pinned {pinned}"
                )
                return 1
            shots.append(shot)
        result = shots[0]
        decision = job["decision"]
        regex = regex_labels(decision)
        expected = expected_for(case, job["source"])
        if args.repeats > 1:
            from services.agent.eval.jev_repeat import score_repeats

            for shot_index, shot in enumerate(shots):
                actual = {}
                if shot and shot.answers and not shot.error:
                    actual = {name: answer.value for name, answer in shot.answers.items()}
                for field in (
                    "disposition",
                    "intent",
                    "dataset",
                    "tool_pick",
                    "clarify_reason",
                    "unsupported_topic",
                ):
                    if field == "tool_pick" and job["kind"] != "tool_pick":
                        continue
                    if field != "tool_pick" and job["kind"] == "tool_pick":
                        continue
                    if not field_applies(field, expected):
                        continue
                    repeat_hits.setdefault(field, [[] for _ in range(len(shots))])
                    repeat_hits[field][shot_index].append(
                        labels_match(expected.get(field), actual.get(field))
                    )
                    if field == "intent":
                        intent_votes.setdefault(str(case.get("id")), []).append(actual.get(field))
        jev_answers = {}
        if result and result.answers and not result.error:
            jev_answers = {
                "disposition": None
                if "disposition" not in result.answers
                else result.answers["disposition"].value,
                "intent": None
                if "intent" not in result.answers
                else result.answers["intent"].value,
                "dataset": None
                if "dataset" not in result.answers
                else result.answers["dataset"].value,
                "clarify_reason": None
                if "clarify_reason" not in result.answers
                else result.answers["clarify_reason"].value,
                "unsupported_topic": None
                if "unsupported_topic" not in result.answers
                else result.answers["unsupported_topic"].value,
                "tool_pick": None
                if "tool_pick" not in result.answers
                else result.answers["tool_pick"].value,
            }
        agree = agreement(regex, result.answers) if result and result.answers and not result.error else None
        if job["kind"] == "tool_pick":
            expected_tool = expected.get("tool_pick")
            actual_tool = jev_answers.get("tool_pick")
            status = "OK" if labels_match(expected_tool, actual_tool) else "MISS"
            detail = "" if status == "OK" else f"tool_pick: {expected_tool} vs {actual_tool}"
        else:
            status = "OK" if agree and agree.get("intent") else "MISS"
            detail = ""
            if agree and not agree.get("intent"):
                detail = f"intent: {regex.get('intent')} vs {jev_answers.get('intent')}"
        if result and result.error:
            status = "ERR"
            detail = result.error
        print(f"[{index}/{total}] {case.get('id')} {job['kind']}  regex={'OK' if labels_match(expected.get('intent'), regex.get('intent')) else 'MISS'}  jev={status} {detail}")
        record_body = {
            "type": job["kind"],
            "request_id": case.get("id"),
            "question": job["question"],
            "question_hash": digest,
            "request_payload": payload(job, pinned),
            "model_version": None if result is None else result.model_version,
            "unexpected_option": bool(result and result.unexpected_option),
            "jev": None
            if result is None or result.error
            else {
                "answers": {
                    name: {
                        "kind": answer.kind,
                        "value": answer.value,
                        "confidence": answer.confidence,
                        "probabilities": answer.probabilities,
                    }
                    for name, answer in result.answers.items()
                },
                "raw": result.raw,
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
            },
            "error": None if result is None else result.error,
        }
        results.append(
            {
                **record_body,
                "kind": job["kind"],
                "diagnostic": bool(job.get("diagnostic")),
                "source": job["source"],
                "case_id": case.get("id"),
                "expected": expected,
                "regex": regex,
                "agree": agree,
                "jev_answers": jev_answers,
                "jev_record": record_body,
                "latency_ms": None if result is None else result.latency_ms,
                "input_tokens": None if result is None else result.input_tokens,
                "raw": None if result is None else result.raw,
                "error": None if result is None else result.error,
            }
        )

    lines = summarize(results, cases)
    if args.repeats > 1:
        from services.agent.eval.jev_repeat import score_repeats

        lines.append("")
        lines.append(f"Repeated-run scoring, K={args.repeats}")
        for field, columns in repeat_hits.items():
            stats = score_repeats(columns)
            lines.append(
                f"- {field}: mean {stats['mean']:.1%} stdev {stats['stdev']:.1%} "
                f"majority {stats['majority']:.1%} stable-correct {stats['stable_correct']:.1%} "
                f"flip {stats['flip_rate']:.1%} n={stats['n']} repeats={stats['repeats']}"
            )
    if args.replay:
        replay_problems = _replay(results, backend, args.replay)
        lines[0:0] = []
        # Rebuild sanity with replay count by replacing the replay line.
        count = len(replay_problems)
        lines = [line if not line.startswith("- replay mismatches:") else f"- replay mismatches: {count}" for line in lines]
        if count:
            lines.insert(0, f"RESULTS NOT TRUSTWORTHY: {count} replay mismatches")
            for problem in replay_problems:
                lines.append(f"- replay {problem}")
    if args.reword:
        lines.extend(_reword(results, backend, Path(args.reword)))
    if args.categorized:
        lines.extend(categorized_summary(Path(args.categorized)))
    text = "\n".join(lines)
    print("\n" + text)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RUNS.mkdir(parents=True, exist_ok=True)
    report = RUNS / f"jev_offline_{stamp}.md"
    raw_path = RUNS / f"jev_offline_{stamp}.jsonl"
    report.write_text(text + "\n", encoding="utf-8")
    with raw_path.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row["jev_record"], default=str) + "\n")
    review_rows = []
    for row in results:
        if row["kind"] != "routing" or not row.get("agree"):
            continue
        if all(row["agree"].values()):
            continue
        confidence = choice_confidence(row["jev_record"], "intent")
        votes = intent_votes.get(str(row["case_id"])) or [(row.get("jev_answers") or {}).get("intent")]
        modal = Counter(votes).most_common(1)[0][0] if votes else ""
        expected_intent = row["expected"].get("intent")
        correct_count = sum(1 for vote in votes if labels_match(expected_intent, vote))
        review_rows.append(
            {
                "request_id": row["case_id"],
                "source": row["source"],
                "question": row["question"],
                "expected": json.dumps(expected_intent),
                "regex": row["regex"].get("intent"),
                "jev": (row.get("jev_answers") or {}).get("intent"),
                "repeats": len(votes),
                "correct_count": correct_count,
                "modal_answer": modal,
                "flip_rate": 0 if len(set(map(str, votes))) <= 1 else 1,
                "jev_confidence": confidence if confidence is not None else "",
                "jev_probabilities": json.dumps(
                    (((row.get("jev_record") or {}).get("jev") or {}).get("answers") or {})
                    .get("intent", {})
                    .get("probabilities")
                ),
                "category": "",
                "notes": "",
            }
        )
    review_rows.sort(key=lambda item: float(item["jev_confidence"] or 0), reverse=True)
    write_review_csv(RUNS / f"jev_offline_{stamp}_review.csv", review_rows)
    if args.export_payloads:
        _export(results, args.export_payloads, RUNS / f"jev_offline_{stamp}_curl.sh")
    mismatches = [check_record_parse(row["jev_record"]) for row in results]
    mismatches = [item for item in mismatches if item]
    if mismatches:
        print("PARSE MISMATCHES")
        for item in mismatches:
            print(item)
        return 1
    print(f"Wrote {report}")
    return 0


def _replay(results: list[dict[str, Any]], backend: Any, count: int) -> list[str]:
    from services.agent.decisions.canonical import bytes_hash, payload_hash

    pool = [row for row in results if row.get("raw")]
    if not pool:
        print("Replay skipped: no stored responses.")
        return []
    chosen = random.sample(pool, k=min(count, len(pool)))
    problems: list[str] = []
    print(f"Replay {len(chosen)} stored payloads")
    for row in chosen:
        stored = row["jev_record"]["request_payload"]
        stored_hash = payload_hash(stored)
        captured: list[bytes] = []
        fresh = backend.evaluate(
            stored["state"],
            {
                name: QuestionSpec(
                    kind=body["type"],
                    instructions=body.get("instructions") or "",
                    criteria=body.get("criteria"),
                )
                for name, body in stored["questions"].items()
            },
            request_id=str(row["case_id"]),
            question_hash=row["question_hash"],
            capture=captured,
        )
        sent_hash = bytes_hash(captured[-1]) if captured else "none"
        canonical_sent = "none"
        if captured:
            try:
                canonical_sent = payload_hash(json.loads(captured[-1]))
            except json.JSONDecodeError:
                canonical_sent = "unparsed"
        print(
            f"  {row['case_id']} stored={stored_hash[:12]} "
            f"sent_bytes={sent_hash[:12]} sent_canonical={str(canonical_sent)[:12]}"
        )
        if canonical_sent not in {stored_hash, "none"}:
            problems.append(f"{row['case_id']}: payload hash mismatch")
            continue
        if fresh is None or fresh.error:
            problems.append(f"{row['case_id']}: replay failed {None if fresh is None else fresh.error}")
            continue
        fresh_record = {
            "jev": {
                "answers": {
                    name: {
                        "kind": answer.kind,
                        "value": answer.value,
                        "confidence": answer.confidence,
                        "probabilities": answer.probabilities,
                    }
                    for name, answer in fresh.answers.items()
                }
            }
        }
        delta = replay_mismatch(row["jev_record"], fresh_record)
        if delta:
            print(f"    variance (identical payload): {'; '.join(delta)}")
        else:
            print("    answers unchanged")
    return problems


def _reword(results: list[dict[str, Any]], backend: Any, path: Path) -> list[str]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    lines = ["", "Reword check"]
    for row in results:
        if row["kind"] != "routing" or not row.get("agree") or all(row["agree"].values()):
            continue
        questions = {
            name: QuestionSpec(
                kind=body["type"],
                instructions=body.get("instructions") or "",
                criteria=body.get("criteria"),
            )
            for name, body in row["jev_record"]["request_payload"]["questions"].items()
        }
        for name, replacement in spec.items():
            if name not in questions:
                continue
            current = questions[name]
            questions[name] = QuestionSpec(
                kind=current.kind,
                instructions=replacement.get("instructions", current.instructions),
                criteria=replacement.get("criteria", current.criteria),
            )
        fresh = backend.evaluate(
            row["state"] if "state" in row else row["jev_record"]["request_payload"]["state"],
            questions,
            request_id=str(row["case_id"]) + "-reword",
            question_hash=row["question_hash"],
        )
        before = (row.get("jev_answers") or {}).get("intent")
        after = None if fresh is None or "intent" not in fresh.answers else fresh.answers["intent"].value
        lines.append(f"- {row['case_id']} intent {before} -> {after}")
    return lines


def _export(results: list[dict[str, Any]], count: int, path: Path) -> None:
    lines = ["# API key is $TYPESAFE_API_KEY. It is not written here."]
    for row in results[:count]:
        body = json.dumps(row["jev_record"]["request_payload"])
        lines.append(
            "curl https://api.typesafe.ai/v1/systemone "
            "-H \"Authorization: Bearer $TYPESAFE_API_KEY\" "
            "-H \"Content-Type: application/json\" "
            f"-d {json.dumps(body)}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
