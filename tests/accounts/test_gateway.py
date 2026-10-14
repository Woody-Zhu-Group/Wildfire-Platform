"""Run against an isolated real Nginx with contract stubs, never production."""

import os
from urllib.parse import urlsplit

import httpx
import pytest


@pytest.fixture(scope="module")
def gateway():
    base = os.environ.get("ACCOUNTS_GATEWAY_TEST_URL", "")
    if not base:
        pytest.skip("Run the documented isolated Nginx gateway harness for proxy integration")
    target = urlsplit(base)
    if target.hostname not in {"127.0.0.1", "localhost"} or target.port != 18080:
        pytest.fail("Gateway harness must be the isolated loopback port 18080")
    with httpx.Client(base_url=base, timeout=5) as client:
        yield client


@pytest.mark.parametrize("prefix", ["agent", "data-query", "visualization", "risk-forecasting"])
def test_gateway_rejects_anonymous_and_pending(gateway, prefix):
    assert gateway.get(f"/api/{prefix}/probe").status_code == 401
    assert gateway.get(f"/api/{prefix}/probe", headers={"Cookie": "session=pending"}).status_code == 403


def test_gateway_preserves_method_for_csrf_before_dispatch(gateway):
    with httpx.Client(base_url="http://127.0.0.1:18004") as upstream:
        before = upstream.get("/_probe").json()["post_calls"]
        response = gateway.post("/api/agent/ask", headers={"Cookie": "session=active"}, json={"question": "Should not dispatch"})
        assert response.status_code == 403
        assert upstream.get("/_probe").json()["post_calls"] == before
        response = gateway.post("/api/agent/ask", headers={"Cookie": "session=active", "X-CSRF-Token": "csrf-test", "Origin": "https://accounts.test"}, json={"question": "Dispatch once"})
        assert response.status_code == 200
        assert response.json()["method"] == "POST"
        assert upstream.get("/_probe").json()["post_calls"] == before + 1


def test_gateway_overwrites_forged_identity_and_strips_credentials(gateway):
    response = gateway.get("/api/agent/probe", headers={"Cookie": "session=active", "Authorization": "Bearer secret", "X-Account-User-Id": "attacker"})
    assert response.status_code == 200
    assert response.json()["user"] == "verified-user"
    assert response.json()["cookie"] is None and response.json()["authorization"] is None
    assert response.headers["cache-control"] == "no-store"


def test_internal_gateway_endpoint_is_not_public(gateway):
    assert gateway.get("/_accounts_auth", headers={"Cookie": "session=active"}).status_code == 404
    assert gateway.get("/internal/auth/active").status_code == 404


def test_public_accounts_routes_keep_their_original_method(gateway):
    response = gateway.post("/api/access-requests", json={"name": "Example"})
    assert response.status_code == 200
    assert response.json()["path"] == "/api/access-requests"
    assert response.json()["method"] == "POST"
