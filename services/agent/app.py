"""FastAPI entrypoint for the read-only agent prototype (port 8004)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.model_setup import ensure_runtime_model
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import OpenAICompatibleProvider
from services.agent.schemas import AskRequest, AskResponse
from services.agent.streaming import iter_sse_queue, make_queue_emitter
from services.agent.tools import ToolExecutor

settings = AgentSettings.from_env()
artifacts = ArtifactStore(settings.artifact_ttl_seconds)
provider: OpenAICompatibleProvider | None = None
executor: ToolExecutor | None = None
orchestrator: AgentOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global provider, executor, orchestrator
    runtime_settings = settings
    try:
        runtime_settings = await ensure_runtime_model(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] model unavailable at startup (alias): {exc}")
        runtime_settings = settings

    provider = OpenAICompatibleProvider(runtime_settings)
    context_info: dict[str, Any] = {}
    try:
        context_info = await provider.ensure_context_loaded()
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] model unavailable at startup: {exc}")

    executor = ToolExecutor(runtime_settings, artifacts)
    orchestrator = AgentOrchestrator(
        runtime_settings, provider, executor
    )
    print(
        f"[agent] Startup model={settings.model} "
        f"runtime_model={runtime_settings.request_model} "
        f"thinking={settings.thinking} structured_mode={settings.structured_mode} "
        f"configured_num_ctx={runtime_settings.num_ctx} "
        f"effective_num_ctx={context_info.get('effective_num_ctx')}"
    )
    if (
        context_info.get("effective_num_ctx") is not None
        and int(context_info["effective_num_ctx"]) < runtime_settings.num_ctx
    ):
        print(
            "[agent] WARNING: loaded context is below configured num_ctx; "
            "synthesis may hang or truncate. Check Ollama memory limits."
        )
    yield
    if orchestrator is not None and orchestrator.shadow is not None:
        orchestrator.shadow.shutdown(timeout=2)
    if provider is not None:
        await provider.close()
    if executor is not None:
        await executor.close()
    print("[agent] Shutdown")


app = FastAPI(
    title="Wildfire Policy Agent Prototype",
    description=(
        "Single-exchange read-only router over local wildfire services. "
        "No database writes, web search, uploads, or external communication."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# Local frontend (other origin/port) calls this API in the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    model_health = (
        await provider.health()
        if provider is not None
        else {"status": "starting", "available": False}
    )
    service_urls = {
        "data_query": settings.data_query_url,
        "risk_forecasting": settings.risk_url,
        "visualization": settings.visualization_url,
        "comparison": settings.comparison_url,
    }

    async def probe(name: str, base: str) -> tuple[str, dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(base + "/health")
                response.raise_for_status()
                payload = response.json()
            if name == "risk_forecasting" and (
                payload.get("status") == "degraded"
                or payload.get("model_loaded") is False
            ):
                return name, {"status": "degraded", "detail": payload.get("detail")}
            return name, {"status": "ok"}
        except Exception as exc:  # noqa: BLE001
            return name, {"status": "unavailable", "detail": str(exc)}

    results = await asyncio.gather(
        *(probe(name, base) for name, base in service_urls.items())
    )
    services = dict(results)
    status = (
        "ok"
        if model_health.get("available")
        and all(item["status"] == "ok" for item in services.values())
        else "degraded"
    )
    return {
        "status": status,
        "model": {
            "provider": settings.provider,
            "name": settings.model,
            "thinking": settings.thinking,
            "structured_mode": settings.structured_mode,
            **model_health,
        },
        "services": services,
        "security": {
            "read_only": True,
            "loopback_only": not settings.allow_remote_provider,
            "single_exchange": True,
        },
    }


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Agent is still starting")
    result = await orchestrator.ask(request.question)
    return AskResponse.model_validate(result.response)


@app.post("/ask/stream")
async def ask_stream(request: AskRequest, http_request: Request) -> StreamingResponse:
    """SSE harness progress for the chat panel. Leaves /ask unchanged for eval."""
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Agent is still starting")

    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
    cancel_event = asyncio.Event()
    on_event = make_queue_emitter(queue)

    async def run() -> None:
        try:
            await orchestrator.ask(
                request.question,
                on_event=on_event,
                cancel_event=cancel_event,
            )
        except Exception as exc:  # noqa: BLE001
            await queue.put(
                (
                    "error",
                    {
                        "status": "error",
                        "answer_text": "The agent failed while streaming.",
                        "code": "orchestrator_error",
                        "detail": str(exc),
                        "qualifications": [],
                        "evidence": [],
                        "artifacts": [],
                        "trajectory": [],
                        "route": {},
                        "timings_ms": {},
                        "model_metrics": {},
                        "request_id": "",
                        "views": [],
                        "view_status": "none",
                        "view_scope": {},
                    },
                )
            )
        finally:
            await queue.put(None)

    task = asyncio.create_task(run())

    async def watch_disconnect() -> None:
        # Model turns can emit no SSE for tens of seconds; poll disconnect so
        # cancel_event trips while Ollama is still generating.
        try:
            while not cancel_event.is_set() and not task.done():
                if await http_request.is_disconnected():
                    cancel_event.set()
                    return
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            return

    watcher = asyncio.create_task(watch_disconnect())

    async def event_generator():
        try:
            async for chunk in iter_sse_queue(queue):
                if await http_request.is_disconnected():
                    cancel_event.set()
                    break
                yield chunk
                if await http_request.is_disconnected():
                    cancel_event.set()
                    break
        finally:
            cancel_event.set()
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/artifacts/{ref}")
async def artifact(ref: str) -> dict[str, Any]:
    item = artifacts.get(ref)
    if item is None:
        raise HTTPException(status_code=404, detail="Artifact not found or expired")
    return {"ref": item.ref, "kind": item.kind, "payload": item.payload}
