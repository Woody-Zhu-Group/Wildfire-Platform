"""Model-proposed filters must come from the question or router slots."""

from __future__ import annotations

from services.agent.grounding import audit_executed_filters, ground_model_filters


def _fields(drops):
    return sorted((d["field"], d["reason"]) for d in drops)


def test_luna_placeholders_are_dropped():
    cases = [
        (
            "Count PG&E ignitions in 2024 inside PG&E territory.",
            {"kind": "summary", "lat": 0, "lon": 0, "utility": "PGE", "hftd_tier": "Tier 2"},
            {"kind": "summary", "utility": "PGE"},
            [("hftd_tier", "not_in_question"), ("lat", "sentinel"), ("lon", "sentinel")],
        ),
        (
            "How many EPSS outages in 2024?",
            {"dataset": "epss_outages", "result_mode": "count", "circuit_id": "000000000"},
            {"dataset": "epss_outages", "result_mode": "count"},
            [("circuit_id", "sentinel")],
        ),
        (
            "Show a weekly ignition time series for 2024.",
            {"kind": "time_series", "dataset": "ignitions", "county": "", "tier": "Tier 2"},
            {"kind": "time_series", "dataset": "ignitions"},
            [("county", "sentinel"), ("tier", "not_in_question")],
        ),
    ]
    for question, args, kept, dropped in cases:
        grounded, drops = ground_model_filters(args, question=question)
        assert grounded == kept, question
        assert _fields(drops) == dropped, question


def test_grounded_filters_are_kept():
    cases = [
        ("Outages on circuit 043371102 in 2024", {"circuit_id": "043371102"}),
        ("Outages on circuit 43371102 in 2024", {"circuit_id": "043371102"}),
        ("Ignitions in HFTD tier 3 during 2023", {"tier": "Tier 3"}),
        ("Ignitions in Sacramento County in 2024", {"county": "Sacramento"}),
        ("What is at 38.58, -121.49?", {"kind": "point", "lat": 38.58, "lon": -121.49}),
    ]
    for question, args in cases:
        grounded, drops = ground_model_filters(args, question=question)
        assert grounded == args and drops == [], question


def test_county_slot_grounds_a_county_the_router_resolved():
    grounded, drops = ground_model_filters(
        {"county": "Los Angeles"}, question="ignitions near LA in 2024", county="Los Angeles"
    )
    assert grounded == {"county": "Los Angeles"} and drops == []


def test_audit_flags_invented_utilities_and_filters():
    drops = audit_executed_filters(
        "data_query_records",
        {"dataset": "epss_outages", "circuit_id": "000000000", "utility": "SCE"},
        question="How many EPSS outages in 2024?",
        utilities=[],
        county=None,
    )
    assert _fields(drops) == [("circuit_id", "sentinel"), ("utility", "not_in_slots")]


def test_model_path_drops_invented_filters_before_the_tool_runs(monkeypatch):
    """Applies on the default (Ollama) settings too, not only OpenRouter."""
    import asyncio
    from dataclasses import replace

    from services.agent.config import AgentSettings
    from services.agent.orchestrator import AgentOrchestrator
    from services.agent.provider import ModelReply
    from services.agent.tools import ToolExecution

    monkeypatch.setattr(
        "services.agent.orchestrator.collect_qualifications",
        lambda *args, **kwargs: _no_caveats(),
    )
    seen: list[dict] = []

    class Executor:
        async def execute(self, tool, arguments, **kwargs):
            seen.append(dict(arguments))
            return ToolExecution(
                tool=tool,
                arguments=arguments,
                ok=True,
                summary={"dataset": "epss_outages", "result_mode": "count", "total": 2787},
                raw={},
                error=None,
                artifact=None,
                latency_ms=1.0,
                evidence_id="evidence_test",
            )

        async def close(self):
            return None

    class Provider:
        async def complete(self, **kwargs):
            if kwargs.get("phase") != "synthesis":
                return ModelReply(
                    content="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "data_query_records",
                                "arguments": '{"dataset":"epss_outages","result_mode":"count",'
                                '"year":2024,"circuit_id":"000000000","county":""}',
                            },
                        }
                    ],
                    raw={},
                    latency_ms=1.0,
                    usage={},
                )
            return ModelReply(
                content='{"status":"answer","answer":"2787 outages.","claims":[{"text":"2787","evidence_ids":["evidence_test"]}]}',
                tool_calls=[],
                raw={"choices": [{"finish_reason": "stop"}]},
                latency_ms=1.0,
                usage={},
            )

    settings = replace(AgentSettings.from_env(), jev_mode="off")
    assert settings.llm_provider == "ollama"
    result = asyncio.run(
        AgentOrchestrator(settings, Provider(), Executor()).ask(
            "How many EPSS outages occurred in 2024?", force_model=True
        )
    )
    assert seen and "circuit_id" not in seen[0] and "county" not in seen[0]
    dropped = [e for e in result.response["trajectory"] if e.get("type") == "filter_dropped"]
    assert sorted(e["field"] for e in dropped) == ["circuit_id", "county"]


async def _no_caveats():
    return [], [], None
