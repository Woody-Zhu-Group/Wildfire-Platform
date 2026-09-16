import asyncio

from services.gpu_control.bringup import (
    PREFLIGHT_QUESTION,
    bring_up_gpu,
    overlay_boot_pipeline,
    pipeline,
    reset_pipeline,
)
from services.gpu_control.config import GpuControlSettings


def test_overlay_holds_ready_until_preflight_succeeds():
    assert overlay_boot_pipeline("ready", "idle") == "ready"
    assert overlay_boot_pipeline("ready", "succeeded") == "ready"
    assert overlay_boot_pipeline("ready", "warming") == "loading_model"
    assert overlay_boot_pipeline("ready", "preflight") == "loading_model"
    assert overlay_boot_pipeline("starting", "waiting_ollama") == "starting"
    assert overlay_boot_pipeline("loading_model", "failed") == "error"


def test_bring_up_warms_then_prefires(monkeypatch):
    reset_pipeline()
    settings = GpuControlSettings(
        instance_id="i-1",
        region=None,
        ollama_url="http://ollama.test",
        model="test-model",
        control_token="t",
        agent_url="http://agent.test",
    )
    probes = [
        {"reachable": False, "model_resident": False},
        {"reachable": True, "model_resident": False},
    ]
    warmed = []
    asked = []

    async def probe(url, model):
        assert url == settings.ollama_url
        assert model == settings.model
        return probes.pop(0)

    async def warm():
        warmed.append(True)
        return {"effective_num_ctx": 8192}

    async def ask(url):
        asked.append(url)
        return {
            "ok": True,
            "question": PREFLIGHT_QUESTION,
            "status": "answer",
            "answer_preview": "There were 12 CPUC ignitions in 2023.",
            "route": "deterministic",
        }

    async def no_sleep(_seconds):
        return None

    asyncio.run(
        bring_up_gpu(
            settings, probe=probe, warm=warm, ask=ask, sleep=no_sleep
        )
    )
    assert warmed == [True]
    assert asked == ["http://agent.test"]
    assert pipeline["status"] == "succeeded"
    assert pipeline["preflight"]["ok"] is True


def test_bring_up_skips_warmup_when_already_resident():
    reset_pipeline()
    settings = GpuControlSettings(
        instance_id="i-1",
        region=None,
        ollama_url="http://ollama.test",
        model="test-model",
        control_token="t",
        agent_url="http://agent.test",
    )
    warmed = []

    async def probe(url, model):  # noqa: ARG001
        return {"reachable": True, "model_resident": True}

    async def warm():
        warmed.append(True)
        raise AssertionError("must not unload a model that is already resident")

    async def ask(url):  # noqa: ARG001
        return {
            "ok": True,
            "question": PREFLIGHT_QUESTION,
            "status": "answer",
            "answer_preview": "ok",
            "route": "deterministic",
        }

    asyncio.run(bring_up_gpu(settings, probe=probe, warm=warm, ask=ask))
    assert warmed == []
    assert pipeline["status"] == "succeeded"


def test_bring_up_preflight_failure_is_error():
    reset_pipeline()
    settings = GpuControlSettings(
        instance_id="i-1",
        region=None,
        ollama_url="http://ollama.test",
        model="test-model",
        control_token="t",
        agent_url="http://agent.test",
    )

    async def probe(url, model):  # noqa: ARG001
        return {"reachable": True, "model_resident": True}

    async def warm():
        return {}

    async def ask(url):  # noqa: ARG001
        raise RuntimeError("Pre-fire /ask failed (status=error): model offline")

    asyncio.run(bring_up_gpu(settings, probe=probe, warm=warm, ask=ask))
    assert pipeline["status"] == "failed"
    assert "model offline" in pipeline["reason"]
    assert overlay_boot_pipeline("ready", pipeline["status"]) == "error"
