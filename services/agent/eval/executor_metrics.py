"""Executor and concrete-plan scoring; a model handoff is not a router answer."""

from collections import Counter, defaultdict


def plan_matches(case: dict, row: dict) -> bool:
    expected = case["expected_tool_calls"]
    actual = row["tool_calls"]
    if len(expected) != len(actual):
        return False
    for (want_tool, want), (got_tool, got) in zip(expected, actual):
        if want_tool != got_tool:
            return False
        for key, value in want.items():
            observed = got.get(key)
            if key in {"regions", "utilities"}:
                if not isinstance(observed, list) or sorted(observed) != sorted(value):
                    return False
            elif observed != value:
                return False
        for key, value in got.items():
            if key in want or value is None:
                continue
            if key == "limit" and want_tool in {
                "data_query_records",
                "data_query_rank",
            }:
                continue  # benchmark queries do not request a row limit
            if key == "normalize" and value == "none":
                continue
            if key == "year" and all(
                str(want.get(k, "")).startswith(f"{value}-")
                for k in ("start_date", "end_date")
            ):
                continue
            return False  # an extra scope/filter cannot silently pass
    return all(
        row.get("output_selectors", {}).get(key) == value
        for key, value in case["expected_output_selectors"].items()
    )


def score_executors(cases: dict, rows: list[dict]) -> dict:
    labels = {case["id"]: case for case in cases["cases"]}
    metrics = defaultdict(Counter)
    details = []
    for row in rows:
        case = labels[row["id"]]
        expected = case["expected_executor"]
        actual = {"deterministic": "router", "model": "agent"}.get(
            row["path"], row["path"]
        )
        correct = actual in expected
        matched = None
        if actual == "router":
            matched = plan_matches(case, row)
            correct = correct and matched
        if case.get("accepted_rules"):
            correct = correct and row["rule"] in case["accepted_rules"]
        if row.get("api_error"):
            correct = False
            matched = False
        groups = (
            row["mode"],
            f"{row['mode']}:{case['split']}",
            f"{row['mode']}:{case['capability_group']}",
        )
        for group in groups:
            stats = metrics[group]
            stats["n"] += 1
            stats["correct"] += bool(correct)
            stats["router_capable"] += "router" in expected
            stats["router_retained_correctly"] += (
                "router" in expected and actual == "router" and bool(matched)
            )
            stats["unnecessary_handoff"] += "router" in expected and actual == "agent"
            stats["agent_handoffs"] += actual == "agent"
            stats["agent_required"] += "agent" in expected
            stats["correct_agent_handoff"] += actual == "agent" and "agent" in expected
            stats["incorrect_plan_acceptance"] += actual == "router" and not correct
        details.append(
            {
                "id": row["id"],
                "repeat": row["repeat"],
                "mode": row["mode"],
                "expected": expected,
                "actual": actual,
                "plan_matches": matched,
                "correct": bool(correct),
            }
        )
    return {
        "metrics": dict(metrics),
        "rows": details,
        "method": "Frozen executor labels and required tool arguments/selectors; extra scope filters fail. Router and agent are never collapsed. Static plan check, not final-answer execution.",
    }
