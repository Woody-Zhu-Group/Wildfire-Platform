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


def test_tier_rule_keeps_both_tiers_when_the_question_asks_across_tiers():
    from services.agent.grounding import _question_tiers

    assert _question_tiers("which hftd tier covered the most utility distribution circuits in 2023?") == {"Tier 2", "Tier 3"}
    assert _question_tiers("ignitions in tier 2 or 3 in 2022") == {"Tier 2", "Tier 3"}
    assert _question_tiers("Ignitions in HFTD tier 3 during 2023") == {"Tier 3"}
    assert _question_tiers("EPSS outages in HFTD areas in 2023") == set()
    grounded, drops = ground_model_filters(
        {"hftd_tier": "Tier 3"}, question="which hftd tier covered the most circuits in 2023?"
    )
    assert grounded == {"hftd_tier": "Tier 3"} and drops == []


def test_record_sample_size_is_not_a_count_claim():
    from services.agent.orchestrator import _quantity_mismatches
    from services.agent.tools import ToolExecution

    execution = ToolExecution(
        tool="data_query_records",
        arguments={"dataset": "cpuc_ignitions", "result_mode": "records", "year": 2023},
        ok=True,
        summary={"dataset": "cpuc_ignitions", "result_mode": "records", "total": 480, "returned": 10, "records": []},
        raw={},
        error=None,
        artifact=None,
        latency_ms=1.0,
    )
    ok = "The dataset records 480 ignitions in 2023; the query returned 10 records as a sample."
    assert _quantity_mismatches(ok, [execution]) == set()
    wrong = "There were 10 ignitions in 2023."
    assert _quantity_mismatches(wrong, [execution]) == {"10"}


def _hosted_settings():
    from dataclasses import replace

    from services.agent.config import AgentSettings

    return replace(
        AgentSettings.from_env(),
        jev_mode="off",
        llm_provider="openrouter",
        model="openai/gpt-6-luna",
        llm_fallback_model="openai/gpt-6-sol",
    )


class _CountingExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, tool, arguments, **kwargs):
        from services.agent.tools import ToolExecution

        self.calls.append(dict(arguments))
        return ToolExecution(
            tool=tool,
            arguments=dict(arguments),
            ok=True,
            summary={"dataset": arguments.get("dataset"), "result_mode": "count", "total": 2},
            raw={},
            error=None,
            artifact=None,
            latency_ms=1.0,
            evidence_id=f"evidence_{len(self.calls)}",
        )

    async def close(self):
        return None


class _ScriptedProvider:
    """Routing turns return the scripted call lists in order; synthesis answers."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.models = []

    async def complete(self, **kwargs):
        import json as _json

        from services.agent.provider import ModelReply

        if kwargs.get("phase") == "synthesis":
            return ModelReply(
                content=_json.dumps({"status": "answer", "answer": "2 events.", "claims": [{"text": "2", "evidence_ids": ["evidence_1"]}]}),
                tool_calls=[],
                raw={"choices": [{"finish_reason": "stop"}]},
                latency_ms=1.0,
                usage={},
            )
        self.models.append(kwargs.get("model"))
        calls = self.turns.pop(0) if self.turns else []
        return ModelReply(
            content="",
            tool_calls=[
                {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": _json.dumps(args)}}
                for i, (name, args) in enumerate(calls)
            ],
            raw={},
            latency_ms=1.0,
            usage={},
        )


def _ask(provider, executor, question, monkeypatch):
    import asyncio

    from services.agent.orchestrator import AgentOrchestrator

    monkeypatch.setattr(
        "services.agent.orchestrator.collect_qualifications",
        lambda *args, **kwargs: _no_caveats(),
    )
    return asyncio.run(AgentOrchestrator(_hosted_settings(), provider, executor).ask(question, force_model=True))


def test_hosted_loop_continues_until_every_named_utility_is_covered(monkeypatch):
    psps = {"dataset": "psps_events", "result_mode": "count", "year": 2021}
    provider = _ScriptedProvider(
        [
            [("data_query_records", {**psps, "utility": "PGE"})],
            [("data_query_records", {**psps, "utility": "SCE"}), ("data_query_records", {**psps, "utility": "SDGE"})],
        ]
    )
    executor = _CountingExecutor()
    result = _ask(provider, executor, "What were the PSPS event counts for PG&E, SCE, and SDGE in 2021, respectively?", monkeypatch)
    assert sorted(call["utility"] for call in executor.calls) == ["PGE", "SCE", "SDGE"]
    events = [e for e in result.response["trajectory"] if e.get("type") == "uncovered_entities_continue"]
    assert events and events[0]["missing"] == ["utility:SCE", "utility:SDGE"]
    # A continuation after a success is not a failure, so it stays on the primary model.
    assert provider.models == ["openai/gpt-6-luna", "openai/gpt-6-luna"]
    assert result.response["status"] == "answer"


def test_hosted_loop_continues_for_each_named_year(monkeypatch):
    base = {"dataset": "calfire_incidents", "result_mode": "count"}
    provider = _ScriptedProvider(
        [
            [("data_query_records", {**base, "year": 2021})],
            [("data_query_records", {**base, "year": 2022})],
        ]
    )
    executor = _CountingExecutor()
    result = _ask(provider, executor, "Were there more wildfire incidents in 2021 or 2022?", monkeypatch)
    assert sorted(call.get("year") for call in executor.calls) == [2021, 2022]
    assert result.response["status"] == "answer"


def test_hosted_loop_refuses_a_partial_answer_at_the_turn_limit(monkeypatch):
    base = {"dataset": "calfire_incidents", "result_mode": "count"}
    # The model keeps repeating the 2021 call and never covers 2022.
    provider = _ScriptedProvider([[("data_query_records", {**base, "year": 2021})]] * 10)
    executor = _CountingExecutor()
    result = _ask(provider, executor, "Were there more wildfire incidents in 2021 or 2022?", monkeypatch)
    assert result.response["status"] == "error"
    assert "2022" in result.response["answer_text"]
    assert any(e.get("type") == "uncovered_entities_stop" for e in result.response["trajectory"])
