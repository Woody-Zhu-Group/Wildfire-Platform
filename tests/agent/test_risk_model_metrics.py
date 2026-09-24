"""Risk model performance: a router-only read of GET /metrics and its card.

Questions about how well the fitted risk model scores route deterministically
to risk_metrics, which is never offered to the model or to Jev. The answer and
the model performance card are grounded on that one read, carry the cNHPP
caveats, and a 503 from /metrics is reported with the service's reason.
"""

from __future__ import annotations

import asyncio
import copy

import httpx
import pytest

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.decisions.decide_mode import exemption
from services.agent.orchestrator import AgentOrchestrator, _unsupported_numbers
from services.agent.provider import ModelReply
from services.agent.routing import route_question
from services.agent.schemas import EXECUTABLE_TOOL_MODELS, TOOL_DESCRIPTIONS, TOOL_MODELS, openai_tools
from services.agent.tools import ToolExecution, ToolExecutor, _summarize_risk_metrics
from services.agent.views import ComponentSpec, GroundingError, ground_views, plan_views

SHA = "ee6fc19c9388c2ab23f693a0a71ef993844c1ad3793d242692657aafe08d9a57"
HPP_REASON = (
    "every cell has the same intensity on every day, so top-k precision and lift "
    "would rank an arbitrary slice of cells"
)

# The shape GET /metrics returns, with the committed metrics_table.csv values.
METRICS = {
    "model": "cNHPP",
    "log_likelihood": -4873.631516436059,
    "top5_precision": 0.004858614323195024,
    "top1_precision": 0.0034860557768924302,
    "lift_top5": 1.9774363590370423,
    "auc": 0.7602070943760917,
    "not_applicable_reason": None,
    "xi": 0.2,
    "train_years": [2020, 2021, 2022, 2023],
    "eval_year": 2024,
    "eval_start": "2024-01-01",
    "eval_end": "2024-12-31",
    "params_sha256": SHA,
    "baselines": [
        {
            "model": "HPP",
            "log_likelihood": -5204.190183871407,
            "top5_precision": None,
            "top1_precision": None,
            "lift_top5": None,
            "auc": 0.5,
            "not_applicable_reason": HPP_REASON,
        },
        {
            "model": "NHPP",
            "log_likelihood": -4877.6157062377415,
            "top5_precision": 0.005344475755514527,
            "top1_precision": 0.00298804780876494,
            "lift_top5": 2.1751799949407467,
            "auc": 0.7587998731448378,
            "not_applicable_reason": None,
        },
    ],
}

PERFORMANCE_QUESTIONS = [
    "How accurate is the risk model?",
    "Show the risk model's performance metrics",
    "How well does the risk model perform?",
    "How good is the ignition risk model?",
    "risk model accuracy",
    "What is the AUC of the cNHPP model?",
    "What is the top 5% precision of the risk model?",
    "What's the log-likelihood of the fitted model?",
    "Compare cNHPP to NHPP and HPP",
    "Is the cNHPP better than the NHPP?",
]

# (question, rule on main before this change). None of these may route to the card.
UNCHANGED_ROUTES = [
    ("What is the fire risk in Butte County on 2024-08-15?", "county_risk"),
    ("Show the risk map for 2024-07-01", "risk_surface"),
    ("How risky is PG&E territory on 2023-09-01?", "utility_risk"),
    ("What was the risk in Sacramento on 2024-07-04?", "county_risk"),
    ("What was the ignition risk on 2024-08-15?", "risk_missing_place"),
    ("What is the risk model?", "risk_missing_place"),
    ("Explain the risk model", "risk_missing_place"),
    ("How accurate are CAL FIRE incident counts?", "open_ended"),
    ("How accurate is the EPSS data?", "open_ended"),
    ("Which model performs best?", "open_ended"),
    ("What are the model evaluation metrics?", "open_ended"),
    ("performance of PG&E EPSS in 2024", "open_ended"),
    # A place, cell, tier, or time the statewide 2024 evaluation cannot honor.
    ("How accurate is the risk model in Butte County?", "forecast_missing_date"),
    ("How accurate is the risk model for PG&E?", "forecast_missing_date"),
    ("How accurate is the risk model for cell 461?", "forecast_missing_date"),
    ("How accurate was the risk model in 2022?", "risk_missing_place"),
    ("risk model precision in Tier 3", "risk_missing_place"),
    ("How reliable is the risk model for CAL FIRE data?", "risk_missing_place"),
    # The future-date backstop still fires first.
    ("How accurate is the risk model at predicting ignitions?", "risk_future_date"),
]


def _execution(raw: dict | None = None, *, evidence_id: str = "ev_metrics") -> ToolExecution:
    raw = raw or METRICS
    return ToolExecution(
        tool="risk_metrics",
        arguments={},
        ok=True,
        summary=_summarize_risk_metrics(raw),
        raw=raw,
        error=None,
        artifact=None,
        latency_ms=1.0,
        evidence_id=evidence_id,
    )


# ---------------------------------------------------------------- tool and route


def test_metrics_tool_is_never_offered_to_the_model_or_jev():
    assert "risk_metrics" not in TOOL_MODELS
    assert "risk_metrics" not in TOOL_DESCRIPTIONS
    assert "risk_metrics" in EXECUTABLE_TOOL_MODELS
    assert "risk_metrics" not in {item["function"]["name"] for item in openai_tools()}


@pytest.mark.parametrize("question", PERFORMANCE_QUESTIONS)
def test_performance_questions_route_to_the_metrics_read(question):
    decision = route_question(question)
    assert decision.path == "deterministic", question
    assert decision.rule == "risk_model_metrics"
    assert decision.tool_calls == [("risk_metrics", {})]
    assert decision.slots["stat_mode"] == "model_metrics"
    # Router-only tool: decide mode leaves the question to the router.
    assert exemption(decision) == "router_only_tool"


@pytest.mark.parametrize("question,rule", UNCHANGED_ROUTES)
def test_ordinary_risk_and_data_questions_keep_their_routes(question, rule):
    decision = route_question(question)
    assert decision.rule == rule, question
    assert all(tool != "risk_metrics" for tool, _args in decision.tool_calls)


def test_force_model_skips_the_rule():
    assert route_question("How accurate is the risk model?", force_model=True).rule != "risk_model_metrics"


# ---------------------------------------------------------------- summary


def test_summary_keeps_every_model_and_the_window():
    summary = _summarize_risk_metrics(METRICS)
    assert [row["model"] for row in summary["models"]] == ["HPP", "NHPP", "cNHPP"]
    hpp = summary["models"][0]
    assert hpp["top5_precision"] is None and hpp["display"]["lift_top5"] is None
    assert hpp["not_applicable_reason"] == HPP_REASON
    cnhpp = summary["models"][2]
    assert cnhpp["display"] == {
        "log_likelihood": "-4873.6", "auc": "0.760",
        "top5_precision": "0.49%", "top1_precision": "0.35%", "lift_top5": "1.98",
    }
    assert (summary["xi"], summary["train_years"], summary["eval_year"]) == (0.2, [2020, 2021, 2022, 2023], 2024)
    assert summary["params_sha256"] == SHA
    assert summary["cnhpp_minus_nhpp_log_likelihood"] == "4.0"


def _broken(change) -> dict:
    raw = copy.deepcopy(METRICS)
    change(raw)
    return raw


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw["baselines"].pop(0),  # HPP missing
        lambda raw: raw.update(model="NHPP"),  # wrong primary row
        lambda raw: raw["baselines"][1].update(top5_precision=None),  # NHPP must rank
        lambda raw: raw["baselines"][0].update(not_applicable_reason=None),  # null without a reason
        lambda raw: raw["baselines"][0].update(top5_precision=0.01),  # partly missing
        lambda raw: raw.update(auc=float("nan")),
        lambda raw: raw.update(params_sha256="abc"),
        lambda raw: raw.update(train_years=[]),
        lambda raw: raw["baselines"].append(dict(raw["baselines"][1])),  # repeated model
    ],
)
def test_summary_rejects_an_incomplete_or_inconsistent_evaluation(change):
    with pytest.raises(ValueError):
        _summarize_risk_metrics(_broken(change))


def test_the_committed_metrics_endpoint_summarizes():
    from services.risk_forecasting.app import metrics

    summary = _summarize_risk_metrics(metrics().model_dump(mode="json"))
    assert {row["model"] for row in summary["models"]} == {"HPP", "NHPP", "cNHPP"}


# ---------------------------------------------------------------- card


def test_planner_grounds_one_model_metrics_card():
    planned = plan_views([_execution()], status="answer", slots={"stat_mode": "model_metrics"})
    assert planned.view_status == "applied"
    [spec] = planned.views
    assert spec.type == "stat_card"
    assert spec.evidence_ids == ["ev_metrics"]
    assert spec.params["stat_mode"] == "model_metrics"
    assert spec.params["view_id"] == "model-metrics"
    assert spec.params["value"] == METRICS["auc"]
    assert (spec.params["eval_year"], spec.params["params_sha256"]) == (2024, SHA)


@pytest.mark.parametrize(
    "patch",
    [{"value": 0.9}, {"eval_year": 2023}, {"params_sha256": "f" * 64}],
)
def test_a_card_that_differs_from_the_cited_evaluation_is_rejected(patch):
    [spec] = plan_views([_execution()], status="answer", slots={"stat_mode": "model_metrics"}).views
    tampered = ComponentSpec(type="stat_card", params={**spec.params, **patch}, evidence_ids=spec.evidence_ids)
    with pytest.raises(GroundingError):
        ground_views([tampered], [_execution()])


def test_a_card_must_cite_the_metrics_read():
    [spec] = plan_views([_execution()], status="answer", slots={"stat_mode": "model_metrics"}).views
    uncited = ComponentSpec(type="stat_card", params=spec.params, evidence_ids=["ev_missing"])
    with pytest.raises(GroundingError):
        ground_views([uncited], [_execution()])


# ---------------------------------------------------------------- end to end


class _UnusedProvider:
    async def complete(self, **kwargs):  # pragma: no cover
        raise AssertionError("deterministic metrics answers never call the model")


def test_performance_answer_is_grounded_end_to_end():
    class MetricsExecutor:
        calls: list[tuple[str, dict]] = []

        async def execute(self, tool, arguments, **kwargs):
            self.calls.append((tool, arguments))
            return _execution()

    async def run():
        executor = MetricsExecutor()
        orchestrator = AgentOrchestrator(AgentSettings(), _UnusedProvider(), executor)  # type: ignore[arg-type]
        question = "How accurate is the risk model?"
        response = (await orchestrator.ask(question)).response
        assert response["status"] == "answer"
        assert executor.calls == [("risk_metrics", {})]
        text = response["answer_text"]
        for piece in ("2024 held-out year", "2020 to 2023", "cNHPP -4873.6", "NHPP -4877.6",
                      "HPP -5204.2", "AUC cNHPP 0.760", "top 5% lift cNHPP 1.98, NHPP 2.18",
                      "not applicable", "statistical tie"):
            assert piece in text, piece
        assert chr(0x2014) not in text
        [card] = response["views"]
        assert card["type"] == "stat_card" and card["params"]["stat_mode"] == "model_metrics"
        assert card["evidence_ids"] == ["ev_metrics"]
        ids = {item["id"] for item in response.get("qualifications") or []}
        assert {"cnhpp_grid_resolution", "cnhpp_contagion_tie"} <= ids
        assert "cnhpp_cell_461" not in ids
        caveats = [item["text"] for item in response.get("qualifications") or []]
        assert _unsupported_numbers(text, question, [_execution()], caveats) == set()

    asyncio.run(run())


def test_a_503_from_metrics_is_an_error_with_the_service_reason_and_no_card():
    detail = "metrics_table.csv was not scored from artifacts/cnhpp_params.npz"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, json={"detail": detail})

    async def run():
        settings = AgentSettings()
        executor = ToolExecutor(settings, ArtifactStore(60))
        executor._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        orchestrator = AgentOrchestrator(settings, _UnusedProvider(), executor)  # type: ignore[arg-type]
        response = (await orchestrator.ask("Show the risk model's performance metrics")).response
        assert response["status"] == "error"
        assert response["answer_text"].startswith("The risk model's performance metrics are unavailable")
        assert detail in response["answer_text"]
        assert response["views"] == []
        assert all(path.endswith("/metrics") for path in calls) and calls

    asyncio.run(run())


def test_the_model_cannot_call_the_router_only_metrics_tool():
    class GuessingProvider:
        async def complete(self, **kwargs):
            if kwargs.get("tool_routing"):
                return ModelReply(
                    content="",
                    tool_calls=[{"id": "call_1", "type": "function",
                                 "function": {"name": "risk_metrics", "arguments": "{}"}}],
                    raw={}, latency_ms=1, usage={},
                )
            return ModelReply(
                content='{"status":"answer","answer":"done","claims":[]}',
                tool_calls=[], raw={"choices": [{"message": {"content": "{}"}}]}, latency_ms=1, usage={},
            )

    class RecordingExecutor:
        calls: list[str] = []

        async def execute(self, tool, arguments, **kwargs):
            self.calls.append(tool)
            return _execution()

    async def run():
        executor = RecordingExecutor()
        orchestrator = AgentOrchestrator(AgentSettings(max_tool_steps=1), GuessingProvider(), executor)  # type: ignore[arg-type]
        result = await orchestrator.ask("Tell me about the model", force_model=True)
        assert "risk_metrics" not in executor.calls
        refused = [step for step in result.response["trajectory"] if step.get("tool") == "risk_metrics"]
        assert refused and all((step.get("error") or {}).get("code") == "unknown_tool" for step in refused)

    asyncio.run(run())
