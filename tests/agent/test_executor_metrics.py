from services.agent.eval.executor_metrics import score_executors


CASE = {
    "id": "count",
    "split": "test",
    "capability_group": "router_direct",
    "expected_executor": ["router"],
    "expected_tool_calls": [
        [
            "data_query_records",
            {
                "dataset": "cpuc_ignitions",
                "utility": "PGE",
                "year": 2024,
                "result_mode": "count",
            },
        ]
    ],
    "expected_output_selectors": {},
}


def test_correct_disposition_does_not_hide_an_unnecessary_handoff():
    rows = [
        {
            "id": "count",
            "mode": "v4",
            "repeat": 1,
            "path": "model",
            "tool_calls": [],
            "rule": "intent",
        }
    ]
    stats = score_executors({"cases": [CASE]}, rows)["metrics"]["v4"]
    assert stats["correct"] == 0 and stats["unnecessary_handoff"] == 1


def test_extra_unrequested_filter_is_an_incorrect_plan_acceptance():
    rows = [
        {
            "id": "count",
            "mode": "v4",
            "repeat": 1,
            "path": "deterministic",
            "rule": "fixed",
            "tool_calls": [
                [
                    "data_query_records",
                    {**CASE["expected_tool_calls"][0][1], "county": "Butte"},
                ]
            ],
        }
    ]
    stats = score_executors({"cases": [CASE]}, rows)["metrics"]["v4"]
    assert stats["correct"] == 0 and stats["incorrect_plan_acceptance"] == 1


def test_api_error_is_not_a_successful_router_retention():
    row = {"id":"count", "mode":"v3", "repeat":1, "path":"deterministic", "rule":"fallback", "api_error":"timeout", "tool_calls":CASE['expected_tool_calls']}
    stats = score_executors({"cases":[CASE]},[row])["metrics"]["v3"]
    assert stats["correct"] == 0 and stats["router_retained_correctly"] == 0
