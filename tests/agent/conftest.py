"""Agent test helpers.

Marker ``requires_service(port, name=None, host="127.0.0.1")`` skips a test
when nothing accepts TCP connections on that port, instead of failing it with
a transport error. Use it only for tests that make real HTTP calls to a
running service (not ones that use httpx.MockTransport). Example:

    @pytest.mark.requires_service(8000, name="data_query")
    def test_needs_data_query(): ...
"""

from __future__ import annotations

import os
import socket

import pytest

# The only LLM provider is OpenRouter, so AgentSettings.from_env() needs a key
# and the remote gate. Tests never call the network (mocked transports and
# fake providers only); a fake key keeps a real one out of the test run.
os.environ.setdefault("OPENROUTER_API_KEY", "sk-or-test-not-a-real-key")
os.environ.setdefault("AGENT_ALLOW_REMOTE_PROVIDER", "true")

_REACHABLE: dict[tuple[str, int], bool] = {}

_START_HINTS = {
    8000: "uvicorn services.data_query.app:app --port 8000 --app-dir .",
    8001: "uvicorn services.risk_forecasting.app:app --port 8001 --app-dir .",
    8002: "uvicorn services.visualization.app:app --port 8002 --app-dir .",
    8003: "uvicorn services.comparison.app:app --port 8003 --app-dir .",
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_service(port, name=None, host='127.0.0.1'): skip unless a live "
        "service accepts connections on host:port",
    )


def _reachable(host: str, port: int) -> bool:
    key = (host, port)
    if key not in _REACHABLE:
        try:
            with socket.create_connection(key, timeout=1.0):
                _REACHABLE[key] = True
        except OSError:
            _REACHABLE[key] = False
    return _REACHABLE[key]


def pytest_runtest_setup(item: pytest.Item) -> None:
    for marker in item.iter_markers(name="requires_service"):
        port = int(marker.args[0] if marker.args else marker.kwargs["port"])
        host = str(marker.kwargs.get("host", "127.0.0.1"))
        name = marker.kwargs.get("name") or f"service on port {port}"
        if not _reachable(host, port):
            hint = _START_HINTS.get(port)
            reason = f"requires live {name} at {host}:{port}, which is not reachable"
            if hint:
                reason += f"; start it with: {hint}"
            pytest.skip(reason)
