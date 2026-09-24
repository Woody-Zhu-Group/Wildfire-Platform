"""No user-facing answer or clarification text contains an em dash.

Two checks. Statically, no string literal in the agent or shared registry
modules (docstrings aside) contains one, since those strings become answers,
clarifications, caveats, notes, and model instructions the model echoes.
At runtime, no routed answer for any eval question, decide-mode reason text,
or unsupported answer contains one.

``services/agent/time_resolve.py`` is not scanned: its em dashes are in
patterns that read the user's own date ranges ("2019—2021"), not text shown
to the user.
"""

import ast
import json
from pathlib import Path

import pytest

from services.agent.decisions.decide_mode import _REASON_TEXT
from services.agent.routing import UNSUPPORTED_ANSWERS, route_question

EM_DASH = "—"
ROOT = Path(__file__).resolve().parents[2]
SCANNED = [
    *sorted((ROOT / "services" / "agent").glob("*.py")),
    *sorted((ROOT / "services" / "agent" / "decisions").glob("*.py")),
    *sorted((ROOT / "services" / "shared").glob("*.py")),
]
NOT_SCANNED = {ROOT / "services" / "agent" / "time_resolve.py"}
EVAL = ROOT / "services" / "agent" / "eval"
EVAL_FILES = (
    "cases.json",
    "jev_paraphrases.json",
    "jev_holdout.json",
    "jev_holdout_v2.json",
    "jev_holdout_v3_questions.json",
)


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _em_dash_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip
            and EM_DASH in node.value
        ):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value.strip()[:80]!r}")
    return found


@pytest.mark.parametrize(
    "path", [path for path in SCANNED if path not in NOT_SCANNED], ids=lambda path: path.name
)
def test_no_string_literal_has_an_em_dash(path):
    assert _em_dash_literals(path) == []


def test_no_routed_answer_has_an_em_dash():
    bad = []
    for name in EVAL_FILES:
        for row in json.loads((EVAL / name).read_text(encoding="utf-8")):
            if "question" not in row:
                continue
            answer = route_question(row["question"]).answer or ""
            if EM_DASH in answer:
                bad.append((name, row.get("id"), answer[:80]))
    assert bad == []


def test_no_decline_text_has_an_em_dash():
    texts = {**UNSUPPORTED_ANSWERS, **_REASON_TEXT}
    assert [key for key, text in texts.items() if EM_DASH in text] == []
