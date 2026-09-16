from services.gpu_control.ollama import model_is_resident
from services.gpu_control.state import classify_state, eta_fields


def test_classify_running_is_not_ready_until_ollama_and_model():
    assert classify_state("stopped", ollama_reachable=False, model_resident=False) == (
        "stopped",
        None,
    )
    assert classify_state("pending", ollama_reachable=False, model_resident=False) == (
        "starting",
        None,
    )
    assert classify_state("running", ollama_reachable=False, model_resident=False) == (
        "starting",
        None,
    )
    assert classify_state("running", ollama_reachable=True, model_resident=False) == (
        "loading_model",
        None,
    )
    assert classify_state("running", ollama_reachable=True, model_resident=True) == (
        "ready",
        None,
    )
    assert classify_state("stopping", ollama_reachable=True, model_resident=True)[0] == (
        "stopping"
    )
    state, reason = classify_state(
        "terminated", ollama_reachable=False, model_resident=False
    )
    assert state == "error"
    assert reason and "terminated" in reason


def test_eta_only_after_this_process_started_and_while_booting():
    assert eta_fields("starting", None, now=100, budget_seconds=190) == {}
    assert eta_fields("ready", 10.0, now=20, budget_seconds=190) == {}
    fields = eta_fields("starting", 10.0, now=70.0, budget_seconds=190)
    assert fields["eta_seconds"] == 130
    assert "estimate" in fields["eta_copy"]


def test_model_resident_requires_vram_when_reported():
    assert model_is_resident(
        [{"name": "test-model", "size_vram": 20_000_000_000}], "test-model"
    )
    assert not model_is_resident(
        [{"name": "test-model", "size_vram": 0}], "test-model"
    )
    assert not model_is_resident([{"name": "other-model", "size_vram": 8}], "test-model")
