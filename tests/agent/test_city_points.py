from __future__ import annotations

import asyncio
import csv

import pytest

from services.agent.caveats import collect_qualifications
from services.agent.config import AgentSettings
from services.agent.orchestrator import AgentOrchestrator
from services.agent.places import PLACES_CSV, city_point
from services.agent.routing import _CA_CITIES, route_question
from services.agent.tools import ToolExecution

CHICO = (39.758951, -121.81772)


def _point_call(lat: float, lon: float) -> tuple[str, dict]:
    return ("data_query_spatial", {"kind": "point", "lat": lat, "lon": lon})


def test_places_file_is_california_only_with_known_place_types():
    with PLACES_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0]) == ["geoid", "name", "place_type", "lat", "lon"]
    assert {row["place_type"] for row in rows} == {"city", "town", "CDP"}
    assert all(row["geoid"].startswith("06") for row in rows)
    for row in rows:
        assert 32.0 < float(row["lat"]) < 42.1, row
        assert -124.6 < float(row["lon"]) < -114.0, row
    incorporated = [row for row in rows if row["place_type"] in {"city", "town"}]
    assert len(incorporated) == 483


def test_every_city_behind_city_needs_place_resolves_to_a_point():
    missing = [name for name in _CA_CITIES if city_point(name) is None]
    assert missing == []


def test_resolver_handles_accents_aliases_and_same_named_cdps():
    assert city_point("La Canada Flintridge").name == "La Cañada Flintridge"
    assert city_point("Angels Camp").name == "Angels"
    assert city_point("Paso Robles").name == "El Paso de Robles (Paso Robles)"
    # Paradise is a Butte County town and a Mono County CDP. Use the town.
    paradise = city_point("Paradise")
    assert paradise.place_type == "town"
    assert paradise.lat == pytest.approx(39.76, abs=0.05)
    assert city_point("Atlantis") is None
    # CDPs are not resolved.
    assert city_point("Acampo") is None


@pytest.mark.parametrize(
    "question",
    (
        "Which utility territory contains Chico?",
        "What IOU territory is Chico in?",
        "What HFTD tier is Chico in?",
        "Is Chico in Tier 3?",
        "Is Chico in PG&E territory?",
        "What grid cell is Chico in?",
        "Is the city of Chico inside a Tier 2 or Tier 3 High Fire Threat District?",
    ),
)
def test_city_point_context_calls_the_spatial_point_tool(question):
    decision = route_question(question)
    assert decision.path == "deterministic", question
    assert decision.rule == "city_point_context", question
    assert decision.tool_calls == [_point_call(*CHICO)]
    assert decision.slots["city_point"]["name"] == "Chico"
    assert decision.answer is None


def test_city_point_risk_on_a_past_date_chains_point_to_risk():
    for question in (
        "What was the ignition risk near Chico on 2023-08-01?",
        "What was the fire risk in Chico on August 1, 2023?",
        "what was the modeled ignition risk around Chico on september 2, 2020?",
    ):
        decision = route_question(question)
        assert decision.rule == "city_point_risk_chain", question
        assert decision.tool_calls[0] == _point_call(*CHICO)
        assert decision.tool_calls[1][0] == "risk_forecast"
        assert decision.tool_calls[1][1]["cell_id"] == "$grid_cell_id"


def test_city_point_risk_without_a_past_date_asks_for_the_date_not_a_place():
    missing = route_question("What was the ignition risk in Chico?")
    assert missing.rule == "forecast_missing_date"
    assert "past date" in missing.answer
    assert "coordinates" not in missing.answer.lower()
    after = route_question("What was the ignition risk in Chico on 2026-08-01?")
    assert after.rule == "risk_future_date"
    assert route_question("What is the ignition risk near Chico tomorrow?").rule == (
        "risk_future_date"
    )


def test_city_names_holding_a_county_name_are_not_that_county():
    for question, name in (
        ("What HFTD tier is West Sacramento in?", "West Sacramento"),
        ("Which utility territory contains South San Francisco?", "South San Francisco"),
        ("What utility service territory is South Lake Tahoe in?", "South Lake Tahoe"),
        ("What HFTD tier is Mount Shasta in?", "Mount Shasta"),
    ):
        decision = route_question(question)
        assert decision.rule == "city_point_context", question
        assert decision.slots["city_point"]["name"] == name
        assert decision.slots["county"] is None


@pytest.mark.parametrize(
    "question",
    (
        # Counts and lists in a city: no city boundary field.
        "How many ignitions were there in Chico in 2023?",
        "List CAL FIRE incidents in Chico in 2022.",
        "How many PSPS events affected Chico in 2021?",
        "Show me a map of EPSS outages in Chico in 2023.",
        # A radius the tools do not support.
        "Which utility serves areas within 10 miles of Chico?",
        "How many ignitions were within 10 miles of Chico in 2023?",
        "What was the ignition risk within 5 km of Chico on 2023-08-01?",
        # Part of a city needs its boundary.
        "Is part of Chico in Tier 3?",
        "Is any portion of Chico in a high fire threat district?",
        "What share of Chico is in HFTD Tier 2?",
        # Somewhere specific inside a city.
        "What was the historical ignition risk for a residence in Placerville?",
        "What's the historical ignition risk for downtown Bakersfield?",
        "What HFTD tier is my address in Chico?",
        # Two cities, or a city and a county.
        "Which utility territory contains Chico and Oroville?",
        "Which utility territory contains Chico in Butte County?",
        # Explicit coordinates keep the existing route.
        "Which utility territory contains Chico at 39.7, -121.8?",
        # Near a city without a risk question has no defined area.
        "What utility territory is near Chico?",
        # Who supplies power is not territory containment: the IOU layer
        # covers cities with their own municipal utility.
        "Which utility serves Chico?",
        "Who provides electricity to Redding?",
        "What power company serves Palo Alto?",
    ),
)
def test_questions_a_center_point_cannot_answer_still_clarify(question):
    decision = route_question(question)
    assert decision.path == "clarification", question
    assert not decision.rule.startswith("city_point"), question
    assert decision.tool_calls == [], question


def test_cities_not_in_the_list_are_not_resolved():
    # Census-designated places are not in the municipality list, so these do
    # not become city points.
    for question in (
        "Which utility territory contains Acampo?",
        "What HFTD tier is Atlantis in?",
    ):
        assert not route_question(question).rule.startswith("city_point"), question


def test_county_names_still_resolve_as_counties():
    for question, county in (
        ("Which utility territory contains Sacramento?", "Sacramento"),
        ("What was the ignition risk in Fresno County on 2023-08-01?", "Fresno"),
        ("How many CAL FIRE incidents were there in Butte County in 2023?", "Butte"),
    ):
        decision = route_question(question)
        assert not decision.rule.startswith("city_point"), question
        assert "city_point" not in decision.slots, question
        assert decision.slots["county"] == county, question
    risk = route_question("What was the ignition risk in Fresno on 2023-08-01?")
    assert risk.rule == "county_risk"


def test_common_word_city_names_still_need_a_place_cue():
    for question in (
        "Which utility serves the utility industry?",
        "What HFTD tier do weed abatement crews work in?",
        "Which utility territory has the most pine needles?",
        "Is paradise a tier 3 idea?",
        "What HFTD tier did Admiral Coronado map?",
    ):
        decision = route_question(question)
        assert not decision.rule.startswith("city_point"), question
    for question, name in (
        ("Which utility territory contains the city of Industry?", "Industry"),
        ("What HFTD tier is Weed, California in?", "Weed"),
        ("Which utility territory contains Needles?", "Needles"),
        ("What HFTD tier is the town of Paradise in?", "Paradise"),
        ("What IOU territory is Coronado, California in?", "Coronado"),
    ):
        decision = route_question(question)
        assert decision.rule == "city_point_context", question
        assert decision.slots["city_point"]["name"] == name


def _execution(tool, arguments, summary, *, qualification_call=False):
    return ToolExecution(
        tool=tool,
        arguments=arguments,
        ok=True,
        summary=summary,
        raw={},
        error=None,
        artifact=None,
        latency_ms=1,
        qualification_call=qualification_call,
    )


_POINT_SUMMARY = {
    "kind": "point",
    "lat": CHICO[0],
    "lon": CHICO[1],
    "iou": {"utility": "PGE", "utility_name": "Pacific Gas and Electric"},
    "hftd_tier": None,
    "grid_cell": {"cell_id": 212},
    "county": "Butte",
    "metadata": {},
}


def test_city_point_caveat_attaches_only_to_a_read_at_the_city_point():
    question = "Which utility territory contains Chico?"
    at_city = _execution(
        "data_query_spatial", {"kind": "point", "lat": CHICO[0], "lon": CHICO[1]},
        _POINT_SUMMARY,
    )
    elsewhere = _execution(
        "data_query_spatial", {"kind": "point", "lat": 39.7, "lon": -121.8},
        _POINT_SUMMARY,
    )

    async def run(executions, text):
        caveats, _, error = await collect_qualifications(
            executions, None, request_id="t", start_attempt=1, question=text  # type: ignore[arg-type]
        )
        assert error is None
        return {item["id"]: item for item in caveats}

    found = asyncio.run(run([at_city], question))
    assert "city_center_point" in found
    text = found["city_center_point"]["text"]
    assert "Chico" in text and "2025 Gazetteer" in text
    assert "Parts of the city may be" in text
    assert "municipal utility" in found["iou_territory_not_provider"]["text"]
    assert "city_center_point" not in asyncio.run(run([elsewhere], question))
    assert "city_center_point" not in asyncio.run(
        run([at_city], "How many ignitions were there in Chico in 2023?")
    )


def test_orchestrator_answers_a_city_risk_question_with_the_caveat():
    calls = []

    class FakeExecutor:
        async def execute(self, tool, arguments, **kwargs):
            calls.append((tool, dict(arguments)))
            if tool == "data_query_spatial":
                return _execution(tool, arguments, _POINT_SUMMARY)
            assert tool == "risk_forecast"
            return _execution(
                tool,
                arguments,
                {
                    "cell_id": arguments["cell_id"],
                    "date": arguments["date"],
                    "risk": 0.02,
                    "scope": {},
                },
            )

    class NoModel:
        async def complete(self, **kwargs):
            raise AssertionError("a city point question must not call the model")

    orchestrator = AgentOrchestrator(
        AgentSettings(),
        NoModel(),  # type: ignore[arg-type]
        FakeExecutor(),  # type: ignore[arg-type]
    )
    result = asyncio.run(
        orchestrator.ask("What was the ignition risk near Chico on 2023-08-01?")
    )
    response = result.response
    assert response["status"] == "answer"
    assert calls[0] == (
        "data_query_spatial",
        {"kind": "point", "lat": CHICO[0], "lon": CHICO[1]},
    )
    assert calls[1][0] == "risk_forecast"
    assert calls[1][1]["cell_id"] == 212
    ids = {item["id"] for item in response["qualifications"]}
    assert "city_center_point" in ids
    assert "cnhpp_grid_resolution" in ids
