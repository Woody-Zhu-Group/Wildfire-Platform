"""Jev-first decide payload. V3 remains frozen for historical replay and shadow."""

from services.agent.decisions.backend import QuestionSpec
from services.agent.decisions import v3

SCHEMA_VERSION = "v4"


def topic_questions() -> dict[str, QuestionSpec]:
    questions = v3.topic_questions()
    intents = dict(questions["intent"].criteria)
    intents["risk_surface"] = (
        "A map of fitted ignition risk or model residuals across the whole California grid."
    )
    intents["model_metrics"] = (
        "How well the fitted ignition model performs: evaluation scores, accuracy, or comparison of fitted models."
    )
    intents["risk"] = (
        "A fitted historical risk score at one place on one past day, not a statewide grid map or a model evaluation."
    )
    intents["map"] = (
        "A map of recorded events or inventory layers, not fitted risk or model residuals."
    )
    questions["intent"] = QuestionSpec(
        kind="choice",
        instructions="What single result is the question asking for?",
        criteria=intents,
    )
    topics = dict(questions["off_topic"].criteria)
    topics["advice_or_judgment"] = (
        "Recommendations about what someone should do, or judgments about blame, responsibility, or penalties. Excludes resource optimization and scheduling."
    )
    topics["on_topic"] = (
        "Historical wildfire records, maps, rankings, comparisons, fitted risk, or fitted-model evaluation. Excludes recommendations and blame judgments."
    )
    questions["off_topic"] = QuestionSpec(
        kind="choice",
        instructions="What is the question mainly about?",
        criteria=topics,
    )
    measures = dict(questions["measure"].criteria)
    measures["model_performance"] = (
        "Evaluation scores or predictive skill of the fitted ignition models."
    )
    measures["risk_grid"] = (
        "Fitted ignition intensity or residuals across the statewide grid."
    )
    questions["measure"] = QuestionSpec(
        kind="choice",
        instructions="Which measure is the question asking the warehouse to return?",
        criteria=measures,
    )
    questions["risk_map_kind"] = QuestionSpec(
        kind="choice",
        instructions="If a statewide model map is requested, which values should it show?",
        criteria={
            "risk": "Fitted historical ignition intensity across the grid.",
            "residual": "Observed training events minus fitted intensity across the grid.",
            "none": "No statewide fitted-model map is requested.",
        },
    )
    return questions


def calls_for(question: str, today: str) -> list[dict]:
    calls = v3.calls_for(question, today, glossary_mode="per_call")
    for call in calls:
        call["state"]["schema_version"] = SCHEMA_VERSION
        if call["name"] == "topic":
            call["questions"] = topic_questions()
    return calls
