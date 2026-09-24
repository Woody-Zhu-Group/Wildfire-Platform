"""Parse, join, and score Jev records without substituting defaults."""

from __future__ import annotations

import hashlib
import math
from typing import Any

from services.agent.decisions.backend import Answer, QuestionSpec


def question_hash(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()


def _as_float_map(raw: dict[Any, Any] | None) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, float] = {}
    for key, value in raw.items():
        out[str(key)] = float(value)
    return out


def parse_raw_answers(
    raw: dict[str, Any],
    questions: dict[str, QuestionSpec] | None = None,
) -> tuple[dict[str, Answer], bool]:
    """Re-parse an unmodified System One body. Never invents a missing answer."""
    body = raw.get("answers")
    if not isinstance(body, dict):
        raise ValueError("raw response has no answers object")
    parsed: dict[str, Answer] = {}
    unexpected = False
    for name, item in body.items():
        if not isinstance(item, dict):
            raise ValueError(f"answer {name} is not an object")
        kind = item.get("type")
        spec = None if questions is None else questions.get(name)
        if kind == "choice":
            choice = item.get("choice")
            probabilities = _as_float_map(item.get("probabilities"))
            if not isinstance(choice, str) or probabilities is None:
                raise ValueError(f"choice answer {name} is incomplete")
            if spec is not None and isinstance(spec.criteria, dict):
                if choice not in spec.criteria:
                    unexpected = True
            parsed[name] = Answer(
                kind="choice",
                value=choice,
                confidence=_optional_float(item.get("confidence")),
                probabilities=probabilities,
            )
        elif kind == "noul":
            if "noul" not in item:
                raise ValueError(f"noul answer {name} is incomplete")
            parsed[name] = Answer(
                kind="noul",
                value=float(item["noul"]),
                confidence=None,
                probabilities=None,
            )
        elif kind == "score":
            if "score" not in item:
                raise ValueError(f"score answer {name} is incomplete")
            parsed[name] = Answer(
                kind="score",
                value=float(item["score"]),
                confidence=_optional_float(item.get("confidence")),
                probabilities=_as_float_map(item.get("probabilities")),
            )
        else:
            raise ValueError(f"answer {name} has unknown type {kind!r}")
    return parsed, unexpected


def answers_equal(left: dict[str, Answer], right: dict[str, Answer]) -> bool:
    if set(left) != set(right):
        return False
    for name, item in left.items():
        other = right[name]
        if item.kind != other.kind or item.value != other.value:
            return False
        if item.confidence != other.confidence:
            return False
        if item.probabilities != other.probabilities:
            return False
    return True


def answer_to_json(answer: Answer) -> dict[str, Any]:
    return {
        "kind": answer.kind,
        "value": answer.value,
        "confidence": answer.confidence,
        "probabilities": answer.probabilities,
    }


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def auroc(labels: list[int], scores: list[float]) -> float | None:
    """Mann-Whitney AUROC. None when a class is missing."""
    if len(labels) != len(scores) or not labels:
        return None
    positives = [score for label, score in zip(labels, scores) if label]
    negatives = [score for label, score in zip(labels, scores) if not label]
    if not positives or not negatives:
        return None
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                wins += 1.0
            elif positive == negative:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


NOISE_ABS = 0.05


def replay_mismatch(stored: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    """Differences beyond small numeric noise. Empty means the wiring agrees."""
    problems: list[str] = []
    stored_answers = (stored.get("jev") or {}).get("answers") or {}
    fresh_answers = (fresh.get("jev") or {}).get("answers") or {}
    names = sorted(set(stored_answers) | set(fresh_answers))
    for name in names:
        left = stored_answers.get(name)
        right = fresh_answers.get(name)
        if left is None or right is None:
            problems.append(f"{name}: missing on one side")
            continue
        if left.get("kind") != right.get("kind"):
            problems.append(f"{name}: kind {left.get('kind')} vs {right.get('kind')}")
            continue
        if left.get("kind") == "choice" and left.get("value") != right.get("value"):
            problems.append(
                f"{name}: choice {left.get('value')} vs {right.get('value')}"
            )
        if left.get("kind") == "noul":
            left_bit = float(left["value"]) >= 0.5
            right_bit = float(right["value"]) >= 0.5
            if left_bit != right_bit:
                problems.append(
                    f"{name}: noul crossed 0.5 ({left['value']} vs {right['value']})"
                )
        left_probs = left.get("probabilities") or {}
        right_probs = right.get("probabilities") or {}
        keys = set(left_probs) | set(right_probs)
        if keys:
            delta = max(
                abs(float(left_probs.get(key, 0)) - float(right_probs.get(key, 0)))
                for key in keys
            )
            if delta > NOISE_ABS:
                problems.append(f"{name}: max probability delta {delta:.4f}")
        if left.get("kind") == "score":
            delta = abs(float(left["value"]) - float(right["value"]))
            if delta > NOISE_ABS:
                problems.append(f"{name}: score delta {delta:.4f}")
    return problems
