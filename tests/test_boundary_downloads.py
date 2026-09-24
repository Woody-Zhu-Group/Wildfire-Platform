"""data_query /hftd and /iou-territories: full geometry by default, opt-in simplify (issue #56).

Runs the app in-process against the local warehouse (read-only).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.data_query.app import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _points(feature_geometry: dict) -> int:
    return sum(len(ring) for polygon in feature_geometry["coordinates"] for ring in polygon)


def _stored_points(db_conn, sql: str, params: tuple) -> int:
    with db_conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


def _geometry(body: dict) -> dict:
    """The one returned feature's geometry, in json or geojson format."""
    if body.get("type") == "FeatureCollection":
        return body["features"][0]["geometry"]
    return body["data"][0]["geometry"]


@pytest.mark.parametrize(
    "path,params,sql,key",
    [
        ("/hftd", {"tier": "Tier 3"}, "SELECT ST_NPoints(geom) FROM wildfire.hftd_tiers WHERE tier = %s", "Tier 3"),
        ("/iou-territories", {"utility": "SCE"}, "SELECT ST_NPoints(geom) FROM wildfire.iou_territories WHERE utility = %s", "SCE"),
    ],
)
def test_the_default_is_the_full_stored_geometry(client, db_conn, path, params, sql, key):
    for fmt in ("json", "geojson"):
        response = client.get(path, params={**params, "format": fmt})
        assert response.status_code == 200, response.text
        body = response.json()
        assert _points(_geometry(body)) == _stored_points(db_conn, sql, (key,))
        assert body["meta"]["geometry_simplified_degrees"] is None


@pytest.mark.parametrize(
    "path,params,sql,key",
    [
        ("/hftd", {"tier": "Tier 2"}, "SELECT ST_NPoints(geom) FROM wildfire.hftd_tiers WHERE tier = %s", "Tier 2"),
        ("/iou-territories", {"utility": "PGE"}, "SELECT ST_NPoints(geom) FROM wildfire.iou_territories WHERE utility = %s", "PGE"),
    ],
)
def test_simplify_shrinks_the_payload_and_says_so(client, db_conn, path, params, sql, key):
    full = client.get(path, params={**params, "format": "geojson"})
    small = client.get(path, params={**params, "format": "geojson", "simplify": 0.001})
    assert small.status_code == 200, small.text
    body = small.json()
    geometry = _geometry(body)
    assert geometry["type"] == "MultiPolygon"
    assert _points(geometry) < _stored_points(db_conn, sql, (key,))
    assert len(small.content) < len(full.content) / 2
    assert body["meta"]["geometry_simplified_degrees"] == 0.001


@pytest.mark.parametrize("value", [0, -0.001, 0.02])
@pytest.mark.parametrize("path", ["/hftd", "/iou-territories"])
def test_out_of_range_simplify_is_rejected(client, path, value):
    assert client.get(path, params={"simplify": value}).status_code == 422


def test_geometry_false_still_omits_geometry(client):
    body = client.get("/hftd", params={"geometry": "false"}).json()
    assert all("geometry" not in row for row in body["data"])
