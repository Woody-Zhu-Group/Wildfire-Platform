"""Rescore stored answers with rules A, B, and C. No API calls."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from services.agent.eval.jev_metrics import field_applies, labels_match
from services.agent.eval.label_rules import (
    clarify_alternatives,
    intent_alternatives,
    tool_alternatives,
)
from services.agent.routing import candidate_tools

FIELDS = (
    "disposition",
    "intent",
    "dataset",
    "tool_pick",
    "clarify_reason",
    "unsupported_topic",
)
HERE = Path(__file__).resolve().parent


def _adjusted(question: str, field: str, gold):
    if field == "clarify_reason":
        return clarify_alternatives(question, gold) or gold
    if field == "tool_pick":
        return tool_alternatives(question, gold) or gold
    if field == "intent":
        return intent_alternatives(question, gold) or gold
    return gold


def _score_block(name: str, items: list[dict]) -> None:
    """items: question, expected dict, votes dict field -> list of repeat values, candidates."""
    print(f"\n== {name} n={len(items)}")
    changed = []
    for field in FIELDS:
        strict_hits = adj_hits = flips = n = 0
        offered_hits = offered_n = 0
        for item in items:
            expected = item["expected"]
            if not field_applies(field, expected):
                continue
            votes = item["votes"][field]
            if not votes:
                continue
            n += 1
            modal = Counter(votes).most_common(1)[0][0]
            gold = expected.get(field)
            strict_ok = labels_match(gold, modal)
            widened = _adjusted(item["question"], field, gold)
            adj_ok = labels_match(widened, modal)
            if strict_ok:
                strict_hits += 1
            if adj_ok:
                adj_hits += 1
            if len(set(map(str, votes))) > 1:
                flips += 1
            if adj_ok and not strict_ok:
                changed.append((item["id"], field, gold, widened, modal))
            if field == "tool_pick" and any(vote is not None for vote in votes):
                offered = item["candidates"]
                gold_tools = gold if isinstance(gold, list) else [gold]
                if gold_tools and all(tool in offered for tool in gold_tools):
                    offered_n += 1
                    if strict_ok:
                        offered_hits += 1
        if not n:
            print(f"  {field}: n=0")
            continue
        print(
            f"  {field}: strict {strict_hits}/{n} ({strict_hits/n:.3f}) "
            f"adjusted {adj_hits}/{n} ({adj_hits/n:.3f}) flip {flips/n:.3f}"
        )
        if field == "tool_pick":
            if offered_n:
                print(
                    f"  tool_pick where gold was offered: {offered_hits}/{offered_n} "
                    f"({offered_hits/offered_n:.3f})"
                )
            else:
                print("  tool_pick where gold was offered: n=0")
    print("  rows a rule changed:")
    if not changed:
        print("    none")
    for cid, field, gold, widened, modal in changed:
        print(f"    {cid} {field} gold={gold} accepted={widened} modal={modal}")


def _dev_items() -> list[dict]:
    raw = json.loads(
        (HERE / "runs" / "jev_hybrid_raw_20260922T204748Z.json").read_text(encoding="utf-8")
    )
    items = []
    for row in raw["rows"]:
        expected = row["expected"]
        votes = defaultdict(list)
        for repeat in row["repeats"]:
            for field in FIELDS:
                votes[field].append(repeat.get(field))
        items.append(
            {
                "id": row["id"],
                "question": row["question"],
                "expected": expected,
                "votes": votes,
                "candidates": candidate_tools(row["question"]),
            }
        )
    return items


def _holdout_items() -> list[dict]:
    rows = json.loads((HERE / "runs" / "holdout_final_rows.json").read_text(encoding="utf-8"))
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["id"]].append(row)
    items = []
    for cid, repeats in grouped.items():
        raw_expected = repeats[0]["expected"]
        expected = {
            "disposition": raw_expected.get("disposition"),
            "intent": raw_expected.get("intent"),
            "dataset": raw_expected.get("dataset"),
            "tool_pick": raw_expected.get("tool_pick"),
            "clarify_reason": raw_expected.get("clarify_reason"),
            "unsupported_topic": raw_expected.get("unsupported_topic"),
        }
        question = repeats[0].get("question") or ""
        if not question:
            # The row log did not store the question. Join from the frozen file.
            question = ""
        votes = defaultdict(list)
        for repeat in repeats:
            for field in FIELDS:
                votes[field].append((repeat.get("predicted") or {}).get(field))
        items.append(
            {
                "id": cid,
                "question": question,
                "expected": expected,
                "votes": votes,
                "candidates": candidate_tools(question) if question else [],
            }
        )
    return items


def main() -> None:
    holdout_questions = {
        row["id"]: row["question"]
        for row in json.loads((HERE / "jev_holdout.json").read_text(encoding="utf-8"))
    }
    holdout = _holdout_items()
    for item in holdout:
        item["question"] = holdout_questions.get(item["id"], item["question"])
        item["candidates"] = candidate_tools(item["question"])
    _score_block("dev stored hybrid", _dev_items())
    _score_block("seen holdout stored", holdout)


if __name__ == "__main__":
    main()
