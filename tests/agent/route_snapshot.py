"""Print the route snapshot fixture: path and rule for every dev and holdout v1 question.

    python -m tests.agent.route_snapshot > tests/agent/fixtures/route_snapshot.json

Regenerate it only alongside the route report a router change requires.
"""

import json
from pathlib import Path

from services.agent.routing import route_question

EVAL = Path(__file__).resolve().parents[2] / "services" / "agent" / "eval"


def snapshot() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name in ("cases.json", "jev_paraphrases.json", "jev_holdout.json"):
        for row in json.loads((EVAL / name).read_text(encoding="utf-8")):
            if "question" not in row:
                continue
            decision = route_question(row["question"])
            out[f"{name}:{row.get('id')}"] = [decision.path, decision.rule]
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1, sort_keys=True))
