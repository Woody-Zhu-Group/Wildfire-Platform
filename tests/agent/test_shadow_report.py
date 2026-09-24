from __future__ import annotations

import shutil
from pathlib import Path

from services.agent.eval import shadow_report

FIXTURES = Path(__file__).parent / "fixtures" / "shadow"


def _report(tmp_path: Path, *, with_rotated: bool = True, examples: int = 10) -> str:
    shutil.copy(FIXTURES / "jev_shadow.jsonl", tmp_path / "jev_shadow.jsonl")
    if with_rotated:
        shutil.copy(FIXTURES / "jev_shadow.jsonl.1", tmp_path / "jev_shadow.jsonl.1")
    path = tmp_path / "jev_shadow.jsonl"
    loaded = shadow_report.load_log(path)
    return "\n".join(shadow_report.build_report(loaded, log_path=path, examples=examples))


def test_reads_rotated_backups_oldest_first_and_skips_bad_lines(tmp_path: Path) -> None:
    shutil.copy(FIXTURES / "jev_shadow.jsonl", tmp_path / "jev_shadow.jsonl")
    shutil.copy(FIXTURES / "jev_shadow.jsonl.1", tmp_path / "jev_shadow.jsonl.1")
    loaded = shadow_report.load_log(tmp_path / "jev_shadow.jsonl")
    assert [file.name for file in loaded.files] == ["jev_shadow.jsonl.1", "jev_shadow.jsonl"]
    assert loaded.bad_lines == 1
    assert loaded.records[0]["request_id"] == "r0"
    assert len(loaded.records) == 10


def test_question_counts(tmp_path: Path) -> None:
    text = _report(tmp_path)
    assert "| distinct questions (routing and tool pick) | 8 |" in text
    assert "| routing rows | 5 |" in text
    assert "| tool_pick_decision rows | 3 |" in text
    assert "| daily_cap | 1 |" in text
    assert "Routing rows with a Jev error: 1 (20.0%)" in text


def test_disposition_agreement_uses_labels_outcome_and_path_fallback(tmp_path: Path) -> None:
    text = _report(tmp_path)
    assert "Rows with a Jev disposition: 4. Agree: 2 (50.0%). Disagree: 2 (50.0%)." in text
    assert "| router \\ jev | answer | clarify | unsupported | total |" in text
    assert "| answer | 1 | 1 | 1 | 3 |" in text
    assert "| unsupported | 0 | 0 | 1 | 1 |" in text


def test_disagreement_examples_rank_confident_jev_first_with_reason(tmp_path: Path) -> None:
    text = _report(tmp_path)
    live = text.index("| answer | open_ended | unsupported | 0.900 | live_web | What wildfires are burning right now? |")
    modesto = text.index(
        "| answer | open_ended | clarify | 0.700 | city_needs_place | What utility service territory contains Modesto? |"
    )
    assert live < modesto


def test_examples_limit(tmp_path: Path) -> None:
    text = _report(tmp_path, examples=1)
    assert "Top 1 disagreements" in text
    assert "contains Modesto?" not in text.split("Top 1 disagreements", 1)[1].split("##", 1)[0]


def test_confidence_distribution_and_low_share(tmp_path: Path) -> None:
    text = _report(tmp_path)
    assert "Rows with confidence: 4. Mean: 0.857. Under 0.8: 1 (25.0%)." in text
    assert "| 0.7 to 0.8 | 1 | 25.0% |" in text
    assert "| 0.9 to 1.0 | 2 | 50.0% |" in text


def test_tool_pick_decision_by_path_and_reason(tmp_path: Path) -> None:
    text = _report(tmp_path)
    assert "| jev | picked | 1 | 33.3% | 0.910 |" in text
    assert "| qwen | low_confidence | 1 | 33.3% | 0.600 |" in text
    assert "| qwen | arguments_unavailable | 1 | 33.3% | 0.850 |" in text
    assert "| data_query_records | 1 |" in text


def test_cost_sums_jev_call_and_row_tokens(tmp_path: Path) -> None:
    text = _report(tmp_path)
    assert "Input tokens: 5,000. At $0.042 per million: $0.0002." in text


def test_noul_confidence_is_distance_from_even() -> None:
    assert shadow_report._answer_confidence({"kind": "noul", "value": 0.1, "confidence": 0.1}) == 0.9
    assert shadow_report._answer_confidence({"kind": "choice", "value": "answer", "confidence": 0.6}) == 0.6


def test_main_writes_markdown_and_fails_on_missing_log(tmp_path: Path, capsys) -> None:
    shutil.copy(FIXTURES / "jev_shadow.jsonl", tmp_path / "jev_shadow.jsonl")
    out = tmp_path / "report.md"
    assert shadow_report.main(["--log", str(tmp_path / "jev_shadow.jsonl"), "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "# Jev shadow log report" in printed
    assert out.read_text(encoding="utf-8").startswith("# Jev shadow log report")
    assert shadow_report.main(["--log", str(tmp_path / "missing.jsonl")]) == 1


def test_env_var_sets_default_log(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_JEV_LOG_PATH", str(tmp_path / "x.jsonl"))
    assert shadow_report.resolve_log_path(None) == tmp_path / "x.jsonl"
    assert shadow_report.resolve_log_path(str(tmp_path / "y.jsonl")) == tmp_path / "y.jsonl"
