"""EPSS outage_type and cause, and CAL FIRE incident_type, resolve against the
warehouse's stored values or fail with 400 (#77).

Before, each was an exact match on a free-text column, so cause=vegetation
returned 0 rows while 1,042 rows carry "Vegetation". The endpoint tests seed
the stored-value cache and replace the query functions; the last tests read
the real warehouse.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.data_query import app as dq
from services.data_query.filters import parse_cause, parse_incident_type, parse_outage_type
from services.shared import stored_values as sv

CAUSES = (
    "3RD", "3rd Party", "Animal", "Company Initiated", "EF", "Environmental/External",
    "Equipment", "Equipment Failure/Involved", "UNK", "Unknown", "VEG", "Vegetation",
)
OUTAGE_TYPES = ("C/OUT", "FTS", "HLT", "T-EPSS")
INCIDENT_TYPES = ("Earthquake", "Fire", "Flood", "Hazmat", "Wildfire")


@pytest.fixture(autouse=True)
def seeded_values():
    sv.clear_stored_values_cache()
    sv.set_stored_values("cause", CAUSES)
    sv.set_stored_values("outage_type", OUTAGE_TYPES)
    sv.set_stored_values("incident_type", INCIDENT_TYPES)
    yield
    sv.clear_stored_values_cache()


NO_DB = object()


# ---------------------------------------------------------------- resolver


@pytest.mark.parametrize(
    "value,expected",
    [
        ("vegetation", "Vegetation"),
        ("VEGETATION", "Vegetation"),
        ("  Vegetation ", "Vegetation"),
        ("equipment  failure/involved", "Equipment Failure/Involved"),
        ("veg", "Vegetation"),
        ("3rd party", "3rd Party"),
    ],
)
def test_cause_matches_ignoring_case_and_spacing(value, expected):
    assert parse_cause(NO_DB, value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [("VEG", "Vegetation"), ("veg", "Vegetation"), ("UNK", "Unknown"), ("unk", "Unknown"),
     ("3RD", "3rd Party"), ("Unknown", "Unknown")],
)
def test_a_cause_code_resolves_to_its_word_form(value, expected):
    """Written rule: a code and its word form are one cause, shown as the word."""
    assert parse_cause(NO_DB, value) == expected


def test_an_ambiguous_code_and_the_two_equipment_words_stay_distinct():
    assert parse_cause(NO_DB, "EF") == "EF"
    assert parse_cause(NO_DB, "Equipment") == "Equipment"
    assert parse_cause(NO_DB, "equipment failure/involved") == "Equipment Failure/Involved"


def test_cause_suggestions_are_word_forms():
    with pytest.raises(HTTPException) as info:
        parse_cause(NO_DB, "Vegitation")
    assert "Did you mean Vegetation?" in info.value.detail
    assert "VEG" not in info.value.detail


@pytest.mark.parametrize(
    "value,suggestion",
    [("Vegitation", "Vegetation"), ("vegetation fire", "Vegetation"), ("Animals", "Animal"), ("equip", "Equipment")],
)
def test_unknown_cause_is_400_with_close_matches(value, suggestion):
    with pytest.raises(HTTPException) as info:
        parse_cause(NO_DB, value)
    assert info.value.status_code == 400
    assert info.value.detail.startswith(f"unknown cause {value!r}; it matches no cause in the warehouse.")
    assert suggestion in info.value.detail


def test_a_value_with_no_close_match_lists_the_stored_values():
    with pytest.raises(HTTPException) as info:
        parse_outage_type(NO_DB, "zzz")
    for value in OUTAGE_TYPES:
        assert value in info.value.detail


def test_outage_type_matches_ignoring_case():
    assert parse_outage_type(NO_DB, "fts") == "FTS"
    assert parse_outage_type(NO_DB, "t-epss") == "T-EPSS"
    assert parse_outage_type(NO_DB, None) is None
    assert parse_outage_type(NO_DB, " ") is None


def test_values_differing_only_by_case_are_refused_not_guessed():
    sv.set_stored_values("cause", ("Vegetation", "VEGETATION"))
    with pytest.raises(HTTPException) as info:
        parse_cause(NO_DB, "vegetation")
    assert "differ only by case" in info.value.detail


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        # The default as meta echoes it is the default.
        ("not Earthquake,Flood,Hazmat", None),
        ("NOT earthquake, flood, hazmat", None),
        # The old default is now an explicit list of stored types.
        ("Wildfire,Fire", "Wildfire,Fire"),
        ("fire, wildfire", "Fire,Wildfire"),
        ("Wildfire,Flood", "Wildfire,Flood"),
        ("ALL", "all"),
        ("Untyped", "untyped"),
        ("wildfire", "Wildfire"),
        ("FIRE", "Fire"),
        ("earthquake", "Earthquake"),
    ],
)
def test_incident_type_keywords_default_and_stored_values(value, expected):
    assert parse_incident_type(NO_DB, value) == expected


@pytest.mark.parametrize("value", ["wildfires", "Wildfire,Floods", "brush fire", "structure"])
def test_unknown_incident_type_is_400(value):
    with pytest.raises(HTTPException) as info:
        parse_incident_type(NO_DB, value)
    assert info.value.status_code == 400
    assert "unknown incident_type" in info.value.detail


def test_stored_values_are_read_once_and_cached():
    sv.clear_stored_values_cache()
    executed: list[str] = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            executed.append(sql)

        def fetchall(self):
            return [("Wildfire",), ("Fire",)]

    class Conn:
        def cursor(self):
            return Cursor()

    assert sv.stored_values(Conn(), "incident_type") == ("Wildfire", "Fire")
    assert sv.stored_values(Conn(), "incident_type") == ("Wildfire", "Fire")
    assert len(executed) == 1
    assert "SELECT DISTINCT incident_type FROM wildfire.calfire_incidents" in executed[0]


# ---------------------------------------------------------------- endpoints


@pytest.fixture
def client():
    dq.app.dependency_overrides[dq.get_conn] = lambda: object()
    try:
        yield TestClient(dq.app)
    finally:
        dq.app.dependency_overrides.clear()


def _recorder(seen: dict[str, Any], result: Any):
    def fake(conn, **kwargs):
        seen.update(kwargs)
        return result

    return fake


def test_epss_outages_query_the_stored_spelling(client, monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(dq.queries, "query_epss", _recorder(seen, ([], 0, {})))
    response = client.get("/epss/outages", params={"cause": "vegetation", "outage_type": "fts"})
    assert response.status_code == 200, response.text
    assert (seen["cause"], seen["outage_type"]) == ("Vegetation", "FTS")
    assert response.json()["meta"]["filters"]["cause"] == "Vegetation"


@pytest.mark.parametrize("params", [{"cause": "Vegitation"}, {"outage_type": "FTSS"}])
def test_epss_outages_reject_unknown_values_before_querying(client, monkeypatch, params):
    called: list[Any] = []
    monkeypatch.setattr(dq.queries, "query_epss", lambda conn, **kw: called.append(kw) or ([], 0, {}))
    response = client.get("/epss/outages", params=params)
    assert response.status_code == 400
    assert "Did you mean" in response.json()["detail"]
    assert called == []


def test_calfire_incidents_resolve_and_reject_incident_type(client, monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(dq.queries, "query_calfire", _recorder(seen, ([], 0, {})))
    ok = client.get("/calfire/incidents", params={"incident_type": "wildfire"})
    assert ok.status_code == 200, ok.text
    assert seen["incident_type"] == "Wildfire"
    for keyword in ("ALL", "untyped"):
        assert client.get("/calfire/incidents", params={"incident_type": keyword}).status_code == 200
        assert seen["incident_type"] == keyword.lower()
    seen.clear()
    bad = client.get("/calfire/incidents", params={"incident_type": "wildfires"})
    assert bad.status_code == 400 and "Wildfire" in bad.json()["detail"]
    assert seen == {}


def test_rank_resolves_incident_type_for_calfire_only(client, monkeypatch):
    seen: dict[str, Any] = {}
    monkeypatch.setattr(dq.queries, "query_rank", _recorder(seen, ([], {"total": 0})))
    params = {"dataset": "calfire_incidents", "group_by": "county"}
    assert client.get("/rank", params={**params, "incident_type": "FIRE"}).status_code == 200
    assert seen["incident_type"] == "Fire"
    assert client.get("/rank", params={**params, "incident_type": "fires"}).status_code == 400
    # incident_type is not a CPUC column; it stays ignored there, as before.
    assert (
        client.get("/rank", params={"dataset": "cpuc_ignitions", "group_by": "county", "incident_type": "x"}).status_code
        == 200
    )


def test_visualization_map_and_time_series_use_the_same_parsers(monkeypatch):
    from services.visualization import app as viz

    viz.app.dependency_overrides[viz.get_conn] = lambda: object()
    try:
        client = TestClient(viz.app)
        seen: dict[str, Any] = {}

        def fake_epss(conn, **kwargs):
            seen.update(kwargs)
            return [], 0, {}

        def fake_calfire(conn, **kwargs):
            seen.update(kwargs)
            return [], 0

        def fake_dates(conn, ds, **kwargs):
            seen.update(kwargs)
            return []

        monkeypatch.setattr(viz.queries, "map_epss_circuits", fake_epss)
        monkeypatch.setattr(viz.queries, "map_calfire", fake_calfire)
        monkeypatch.setattr(viz.queries, "time_series_dates", fake_dates)
        monkeypatch.setattr(viz.queries, "calfire_missing_counts", lambda conn, **kwargs: {})

        ok = client.get("/map-layer", params={"dataset": "epss", "year": 2024, "cause": "animal", "outage_type": "hlt"})
        assert ok.status_code == 200, ok.text
        assert (seen["cause"], seen["outage_type"]) == ("Animal", "HLT")
        assert client.get("/map-layer", params={"dataset": "epss", "cause": "Animals"}).status_code == 400

        ok = client.get("/map-layer", params={"dataset": "calfire", "year": 2024, "incident_type": "wildfire"})
        assert ok.status_code == 200 and seen["incident_type"] == "Wildfire"
        assert client.get("/map-layer", params={"dataset": "calfire", "incident_type": "wildfires"}).status_code == 400

        ok = client.get("/time-series", params={"dataset": "calfire", "interval": "monthly", "year": 2024, "incident_type": "fire"})
        assert ok.status_code == 200 and seen["incident_type"] == "Fire"
        bad = client.get("/time-series", params={"dataset": "calfire", "interval": "monthly", "year": 2024, "incident_type": "fires"})
        assert bad.status_code == 400
    finally:
        viz.app.dependency_overrides.clear()


# ---------------------------------------------------------------- warehouse


def test_warehouse_values_and_a_lowercase_cause_counts_the_stored_rows(db_conn):
    from services.data_query import queries

    sv.clear_stored_values_cache()
    causes = sv.stored_values(db_conn, "cause")
    assert {"Vegetation", "VEG", "Unknown", "UNK"} <= set(causes)
    assert set(sv.stored_values(db_conn, "outage_type")) >= {"FTS", "HLT"}
    assert {"Wildfire", "Fire"} <= set(sv.stored_values(db_conn, "incident_type"))

    resolved = parse_cause(db_conn, "veg")
    assert resolved == "Vegetation"
    _rows, total, _notes = queries.query_epss(
        db_conn, circuit_id=None, utility=None, county=None, year=None, start_date=None,
        end_date=None, outage_type=None, cause=resolved, bbox=None, limit=1, offset=0,
    )
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM wildfire.epss_outages WHERE cause IN ('Vegetation', 'VEG')")
        expected = int(cur.fetchone()[0])
    assert total == expected > 0
