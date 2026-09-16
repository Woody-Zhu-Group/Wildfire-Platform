from fastapi.testclient import TestClient

from services.gpu_control import app as gpu_app
from services.gpu_control.bringup import reset_pipeline

INSTANCE_ID = "i-test"


async def _noop_bring_up(settings):  # noqa: ARG001
    return None


def _describe(ec2_state, private_ip=None):
    return {
        "instance_id": INSTANCE_ID,
        "ec2_state": ec2_state,
        "private_ip": private_ip,
    }


def _install_lifecycle(monkeypatch, *, initial="stopped"):
    box = {"ec2_state": initial, "start_calls": 0}

    def describe(settings):  # noqa: ARG001
        return _describe(box["ec2_state"])

    def start_instance(settings):  # noqa: ARG001
        box["start_calls"] += 1
        box["ec2_state"] = "pending"
        return _describe("pending")

    def stop_instance(settings):  # noqa: ARG001
        box["ec2_state"] = "stopping"
        return _describe("stopping")

    monkeypatch.setattr(gpu_app.aws, "describe_instance", describe)
    monkeypatch.setattr(gpu_app.aws, "start_instance", start_instance)
    monkeypatch.setattr(gpu_app.aws, "stop_instance", stop_instance)
    return box


def _client(monkeypatch, token=None):
    monkeypatch.setenv("GPU_INSTANCE_ID", INSTANCE_ID)
    monkeypatch.setenv("GPU_OLLAMA_URL", "http://ollama.test")
    monkeypatch.setenv("GPU_MODEL", "test-model")
    if token is None:
        # Empty overrides a token loaded from repo .env (dotenv does not override).
        monkeypatch.setenv("GPU_CONTROL_TOKEN", "")
    else:
        monkeypatch.setenv("GPU_CONTROL_TOKEN", token)
    gpu_app._start_requested_at = None
    gpu_app._bring_up_task = None
    reset_pipeline()
    monkeypatch.setattr(gpu_app, "bring_up_gpu", _noop_bring_up)
    return TestClient(gpu_app.app)


def test_missing_resource_config_disables_status_and_start(monkeypatch):
    for name in ("GPU_INSTANCE_ID", "GPU_OLLAMA_URL", "GPU_MODEL"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("GPU_CONTROL_TOKEN", "correct-token")
    monkeypatch.setattr(
        gpu_app.aws,
        "describe_instance",
        lambda settings: (_ for _ in ()).throw(
            AssertionError("disabled status must not call EC2")
        ),
    )
    client = TestClient(gpu_app.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "disabled"
    status = client.get("/gpu/status")
    assert status.status_code == 200
    assert status.json()["state"] == "unavailable"
    started = client.post(
        "/gpu/start", headers={"X-GPU-Control-Token": "correct-token"}
    )
    assert started.status_code == 503
    assert "GPU control is disabled" in started.json()["detail"]


def test_health_and_status_are_unauthenticated(monkeypatch):
    monkeypatch.setattr(
        gpu_app.aws,
        "describe_instance",
        lambda settings: _describe("stopped"),
    )
    client = _client(monkeypatch)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["service"] == "gpu_control"
    status = client.get("/gpu/status")
    assert status.status_code == 200
    assert status.json()["state"] == "stopped"
    assert "eta_seconds" not in status.json()


def test_start_without_env_token_is_503(monkeypatch):
    client = _client(monkeypatch, token=None)
    response = client.post("/gpu/start")
    assert response.status_code == 503


def test_start_wrong_token_is_401(monkeypatch):
    client = _client(monkeypatch, token="correct-token")
    response = client.post(
        "/gpu/start", headers={"X-GPU-Control-Token": "wrong-token"}
    )
    assert response.status_code == 401


def test_start_sets_eta_then_stop_clears_it(monkeypatch):
    _install_lifecycle(monkeypatch)
    client = _client(monkeypatch, token="correct-token")
    started = client.post(
        "/gpu/start", headers={"X-GPU-Control-Token": "correct-token"}
    )
    assert started.status_code == 200
    body = started.json()
    assert body["state"] == "starting"
    assert "eta_seconds" in body
    assert "estimate" in body["eta_copy"]

    stopped = client.post(
        "/gpu/stop", headers={"X-GPU-Control-Token": "correct-token"}
    )
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "stopping"
    assert "eta_seconds" not in stopped.json()


def test_start_schedules_bring_up(monkeypatch):
    async def marked(settings):  # noqa: ARG001
        return None

    _install_lifecycle(monkeypatch)
    client = _client(monkeypatch, token="correct-token")
    monkeypatch.setattr(gpu_app, "bring_up_gpu", marked)
    started = client.post(
        "/gpu/start", headers={"X-GPU-Control-Token": "correct-token"}
    )
    assert started.status_code == 200
    assert started.json()["state"] == "starting"
    assert gpu_app._bring_up_task is not None


def test_start_while_starting_does_not_call_start_instance(monkeypatch):
    box = _install_lifecycle(monkeypatch)
    client = _client(monkeypatch, token="correct-token")
    headers = {"X-GPU-Control-Token": "correct-token"}
    first = client.post("/gpu/start", headers=headers)
    second = client.post("/gpu/start", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["state"] == "starting"
    assert second.json()["state"] == "starting"
    assert box["start_calls"] == 1


def test_start_while_loading_model_does_not_call_start_instance(monkeypatch):
    box = _install_lifecycle(monkeypatch, initial="running")

    async def probe(url, model):  # noqa: ARG001
        return {"reachable": True, "model_resident": False}

    monkeypatch.setattr(gpu_app, "probe_ollama", probe)
    client = _client(monkeypatch, token="correct-token")
    response = client.post(
        "/gpu/start", headers={"X-GPU-Control-Token": "correct-token"}
    )
    assert response.status_code == 200
    assert response.json()["state"] == "loading_model"
    assert box["start_calls"] == 0


def test_redundant_start_still_requires_token(monkeypatch):
    _install_lifecycle(monkeypatch, initial="pending")
    client = _client(monkeypatch, token="correct-token")
    denied = client.post("/gpu/start")
    assert denied.status_code == 401
