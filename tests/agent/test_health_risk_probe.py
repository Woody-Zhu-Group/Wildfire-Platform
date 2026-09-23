"""Agent /health keeps the risk load error when risk answers 503."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from services.agent import app as agent_app


def _run_health(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(agent_app.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(agent_app, "provider", None)
    return asyncio.run(agent_app.health())


def _handler(risk_response):
    risk_port = httpx.URL(agent_app.settings.risk_url).port

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port == risk_port:
            return risk_response(request)
        return httpx.Response(200, json={"status": "ok"})

    return handler


def test_risk_503_with_load_error_is_degraded_with_detail(monkeypatch):
    body = {
        "status": "degraded",
        "model_loaded": False,
        "detail": "Fitted params, grid, or adjacency did not load: FileNotFoundError: Missing adjacency pickle",
        "ready": False,
        "failed_stage": "fitted_params",
    }
    result = _run_health(monkeypatch, _handler(lambda _req: httpx.Response(503, json=body)))
    risk = result["services"]["risk_forecasting"]
    assert risk == {
        "status": "degraded",
        "detail": body["detail"],
        "failed_stage": "fitted_params",
    }
    assert result["services"]["data_query"] == {"status": "ok"}
    assert result["status"] == "degraded"


def test_risk_503_after_covariates_fail_is_degraded(monkeypatch):
    body = {
        "status": "degraded",
        "model_loaded": True,
        "detail": "Covariate data did not load",
        "ready": False,
        "failed_stage": "covariates",
    }
    result = _run_health(monkeypatch, _handler(lambda _req: httpx.Response(503, json=body)))
    assert result["services"]["risk_forecasting"]["status"] == "degraded"
    assert result["services"]["risk_forecasting"]["detail"] == "Covariate data did not load"


def test_risk_not_responding_is_unavailable(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("All connection attempts failed", request=request)

    result = _run_health(monkeypatch, _handler(refuse))
    risk = result["services"]["risk_forecasting"]
    assert risk["status"] == "unavailable"
    assert "All connection attempts failed" in risk["detail"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, text="Service Unavailable"),
        httpx.Response(503, json={"unexpected": True}),
        httpx.Response(500, json={"status": "degraded", "model_loaded": False}),
    ],
)
def test_other_risk_errors_stay_unavailable(monkeypatch, response):
    result = _run_health(monkeypatch, _handler(lambda _req: response))
    assert result["services"]["risk_forecasting"]["status"] == "unavailable"


def test_risk_200_ready_is_ok(monkeypatch):
    body = {"status": "ok", "model_loaded": True, "detail": None, "ready": True}
    result = _run_health(monkeypatch, _handler(lambda _req: httpx.Response(200, json=body)))
    assert result["services"]["risk_forecasting"] == {"status": "ok"}


def test_old_risk_200_degraded_body_is_still_degraded(monkeypatch):
    body = {"status": "degraded", "model_loaded": False, "detail": "Fitted model not loaded"}
    result = _run_health(monkeypatch, _handler(lambda _req: httpx.Response(200, json=body)))
    assert result["services"]["risk_forecasting"] == {
        "status": "degraded",
        "detail": "Fitted model not loaded",
    }
