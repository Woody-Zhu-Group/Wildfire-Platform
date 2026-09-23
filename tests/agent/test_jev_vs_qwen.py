"""jev_vs_qwen reads trajectory tools and the shadow log, not response.tool_calls."""

from __future__ import annotations

import json

from services.agent.eval.jev_vs_qwen import compare, render


def test_extracts_qwen_tools_and_joins_jev_on_the_question(tmp_path):
    run = tmp_path / "runs" / "qwen2.5-7b__thinking-off__constrained__jev-shadow"
    run.mkdir(parents=True)
    question = "Tell me about CPUC ignitions in 2023"
    (run / "trajectories.jsonl").write_text(
        json.dumps(
            {
                "case": {
                    "id": "model_cpuc_tell_me_about_2023",
                    "question": question,
                    "expected_route": "model",
                    "expected_tools": ["data_query_records"],
                },
                "elapsed_ms": 180000,
                "response": {
                    "status": "answer",
                    "trajectory": [
                        {"type": "model_turn", "phase": "routing", "tool_call_count": 1},
                        {
                            "type": "tool_call",
                            "tool": "data_query_records",
                            "ok": True,
                            "qualification_call": False,
                        },
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    log = tmp_path / "jev_shadow.jsonl"
    log.write_text(
        json.dumps(
            {
                "type": "routing",
                "request_id": "not-the-case-id",
                "question": question,
                "jev": {
                    "answers": {
                        "tool_pick": {"kind": "choice", "value": "data_query_records", "confidence": 0.91}
                    }
                },
                "calls": [{"name": "tool_pick", "latency_ms": 410}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = compare("jev-shadow", runs=tmp_path / "runs", logs=[log])
    text = render(report)
    assert "qwen first-tool 1/1" in text
    assert "jev tool_pick 1/1" in text
    assert report["cases"][0]["jev_latency_ms"] == 410
    assert report["cases"][0]["qwen_elapsed_ms"] == 180000
