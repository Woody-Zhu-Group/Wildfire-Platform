"""The frozen v3 labels file and the questions-only file stay aligned.

The labels file is the only source of labels. The questions file carries ids
and question text only, in the same order, so scripts index them together.
"""

import json
from pathlib import Path

EVAL = Path(__file__).resolve().parents[2] / "services" / "agent" / "eval"


def _rows(name):
    return json.loads((EVAL / name).read_text(encoding="utf-8"))


def test_every_label_row_has_matching_question_text():
    labels = [row for row in _rows("jev_holdout_v3_labels_chatgpt.json") if "disposition" in row]
    questions = _rows("jev_holdout_v3_questions.json")
    assert len(labels) == len(questions) == 88
    for index, (label, question) in enumerate(zip(labels, questions), start=1):
        assert label["id"] == index
        assert question["n"] == index
        assert question["id"] == f"hv3_{index:03d}"
        assert isinstance(question["question"], str) and question["question"].strip()
        assert set(question) == {"id", "n", "question"}, "questions file must carry no labels"


def test_the_labels_file_is_frozen_and_questions_come_from_the_raw_file():
    labels = _rows("jev_holdout_v3_labels_chatgpt.json")
    assert any(row.get("kind") == "frozen" for row in labels)
    raw = {row["question"] for row in _rows("jev_holdout_v3_raw.json")}
    for question in _rows("jev_holdout_v3_questions.json"):
        assert question["question"] in raw, question["id"]
