"""Optional EC2/Ollama control service — port 8005."""

from __future__ import annotations

import asyncio
import hmac
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from services.gpu_control import aws
from services.gpu_control.bringup import (
    bring_up_gpu,
    overlay_boot_pipeline,
    pipeline,
    reset_pipeline,
)
from services.gpu_control.config import GpuControlSettings
from services.gpu_control.ollama import probe_ollama
from services.gpu_control.state import EBS_NOTE, classify_state, eta_fields

_start_requested_at: float | None = None
_bring_up_task: asyncio.Task[None] | None = None
_start_lock = asyncio.Lock()
_STARTABLE_STATES = frozenset({"stopped", "error"})


async def _cancel_bring_up() -> None:
    global _bring_up_task
    task = _bring_up_task
    _bring_up_task = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def _schedule_bring_up(settings: GpuControlSettings) -> None:
    global _bring_up_task
    if _bring_up_task is not None and not _bring_up_task.done():
        return
    _bring_up_task = asyncio.create_task(bring_up_gpu(settings))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = GpuControlSettings.from_env()
    token_state = "set" if settings.control_token else "MISSING (POST start/stop return 503)"
    if settings.configured:
        print(
            f"[gpu_control] instance={settings.instance_id} "
            f"region={settings.region or '(boto3 default chain)'} "
            f"ollama={settings.ollama_url} model={settings.model} "
            f"token={token_state}"
        )
    else:
        print(
            "[gpu_control] disabled; missing "
            + ", ".join(settings.missing_required)
        )
    yield
    await _cancel_bring_up()
    print("[gpu_control] Shutdown")


app = FastAPI(
    title="Wildfire Optional GPU Control",
    description=(
        "Start and stop an explicitly configured EC2 instance that runs Ollama. "
        "Does not start the GPU as a side effect of Ask or health polls."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _settings() -> GpuControlSettings:
    return GpuControlSettings.from_env()


def _require_token(request: Request, settings: GpuControlSettings) -> None:
    if not settings.control_token:
        raise HTTPException(
            status_code=503,
            detail="GPU_CONTROL_TOKEN is not configured; start and stop are disabled",
        )
    provided = request.headers.get("X-GPU-Control-Token") or ""
    expected = settings.control_token
    if len(provided) != len(expected) or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing GPU control token",
        )


def _require_configured(settings: GpuControlSettings) -> None:
    if settings.configured:
        return
    raise HTTPException(
        status_code=503,
        detail=(
            "GPU control is disabled; configure "
            + ", ".join(settings.missing_required)
        ),
    )


async def _status_payload(settings: GpuControlSettings) -> dict[str, Any]:
    if not settings.configured:
        return {
            "state": "unavailable",
            "ec2_state": None,
            "ollama_reachable": False,
            "model_resident": False,
            "instance_id": settings.instance_id or None,
            "model": settings.model or None,
            "ebs_note": EBS_NOTE,
            "reason": (
                "GPU control is disabled; missing "
                + ", ".join(settings.missing_required)
            ),
            "preflight": None,
        }
    try:
        instance = aws.describe_instance(settings)
        ec2_state = instance.get("ec2_state") or ""
        reason = None
    except Exception as exc:  # noqa: BLE001
        instance = {
            "instance_id": settings.instance_id,
            "ec2_state": "",
            "private_ip": None,
        }
        ec2_state = ""
        reason = str(exc)

    ollama = {"reachable": False, "model_resident": False}
    if ec2_state == "running":
        ollama = await probe_ollama(settings.ollama_url, settings.model)

    state, classify_reason = classify_state(
        ec2_state,
        ollama_reachable=bool(ollama.get("reachable")),
        model_resident=bool(ollama.get("model_resident")),
    )
    state = overlay_boot_pipeline(state, pipeline.get("status") or "idle")
    if pipeline.get("status") == "failed":
        reason = pipeline.get("reason") or reason
    elif reason is None:
        reason = classify_reason
    elif classify_reason:
        reason = f"{reason}; {classify_reason}"

    payload: dict[str, Any] = {
        "state": state,
        "ec2_state": ec2_state or None,
        "ollama_reachable": bool(ollama.get("reachable")),
        "model_resident": bool(ollama.get("model_resident")),
        "instance_id": instance.get("instance_id") or settings.instance_id,
        "model": settings.model,
        "ebs_note": EBS_NOTE,
        "reason": reason,
        "preflight": pipeline.get("preflight"),
    }
    payload.update(
        eta_fields(
            state,
            _start_requested_at,
            now=time.monotonic(),
            budget_seconds=settings.start_budget_seconds,
        )
    )
    return payload


@app.get("/health")
async def health() -> dict[str, Any]:
    settings = _settings()
    return {
        "status": "ok" if settings.configured else "disabled",
        "service": "gpu_control",
        "instance_id": settings.instance_id or None,
        "missing_required": list(settings.missing_required),
        "token_configured": bool(settings.control_token),
    }


@app.get("/gpu/status")
async def gpu_status() -> dict[str, Any]:
    return await _status_payload(_settings())


@app.post("/gpu/start")
async def gpu_start(request: Request) -> dict[str, Any]:
    global _start_requested_at
    settings = _settings()
    _require_configured(settings)
    _require_token(request, settings)
    async with _start_lock:
        current = await _status_payload(settings)
        if current.get("state") not in _STARTABLE_STATES:
            return current
        try:
            aws.start_instance(settings)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if _start_requested_at is None:
            _start_requested_at = time.monotonic()
        _schedule_bring_up(settings)
        return await _status_payload(settings)


@app.post("/gpu/stop")
async def gpu_stop(request: Request) -> dict[str, Any]:
    global _start_requested_at
    settings = _settings()
    _require_configured(settings)
    _require_token(request, settings)
    await _cancel_bring_up()
    reset_pipeline()
    try:
        aws.stop_instance(settings)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    _start_requested_at = None
    return await _status_payload(settings)
