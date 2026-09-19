"""One local replay/policy process with live HTTP and WebSocket interfaces.

Data/model initialization belongs to lifespan, never import. Run one Uvicorn
worker: the replay clock, connection set and site journal have one owner.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import json
import logging
import math
from numbers import Integral, Real
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.detector import create_detector
from earshot import parse, voice
from earshot.policy import PolicyLayer
from earshot.replay import Replayer

logger = logging.getLogger("earshot.server")
OFFLINE_MODE = False


def offline_enabled() -> bool:
    return OFFLINE_MODE or parse.OFFLINE_MODE or voice.OFFLINE_MODE or os.getenv("EARSHOT_OFFLINE") == "1"


def set_offline(on: bool) -> None:
    """Change process-local outbound gates before yielding control to any task."""
    global OFFLINE_MODE
    OFFLINE_MODE = on
    parse.OFFLINE_MODE = on
    voice.OFFLINE_MODE = on
    os.environ["EARSHOT_OFFLINE"] = "1" if on else "0"


def _wire(value: Any) -> Any:
    """Convert observed values to strict JSON; never emit NaN/Infinity."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    raise TypeError(f"Unsupported response value type: {type(value).__name__}")


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


async def _work(function, *args):
    """Drain a started worker before cancellation releases the policy lock."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Repeated cancellation must not cancel the asyncio wrapper while its
        # worker still owns an in-progress score or durable journal write.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        with suppress(Exception):
            task.result()
        raise


@dataclass
class Runtime:
    replayer: Replayer
    policy: PolicyLayer
    vocabulary: dict
    baseline: dict
    stats_interval: float = 2.0
    clients: set = field(default_factory=set, init=False)
    tasks: list[asyncio.Task] = field(default_factory=list, init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    replay_error: str | None = field(default=None, init=False)
    replay_finished: bool = field(default=False, init=False)
    _send_locks: dict = field(default_factory=dict, init=False)
    _closed: bool = field(default=False, init=False)
    _generation: int = field(default=0, init=False)
    _events_processed: int = field(default=0, init=False)
    _started_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.stats_interval) or self.stats_interval <= 0:
            raise ValueError("stats_interval must be finite and positive")
        self.policy.bind_replay_buffer(self.replayer.buffer)
        self._generation = getattr(self.replayer, "generation", 0)

    async def start(self) -> None:
        if self.tasks:
            raise RuntimeError("Runtime is already started")
        self._started_at = asyncio.get_running_loop().time()
        self.tasks = [asyncio.create_task(self._pump(), name="earshot-replay"),
                      asyncio.create_task(self._ticker(), name="earshot-statistics")]

    def discard(self, client) -> None:
        self.clients.discard(client)
        self._send_locks.pop(client, None)

    async def broadcast(self, payload: dict) -> None:
        """Send independently, dropping failed/slow peers without losing others."""
        message = _wire(payload)

        async def send(client) -> None:
            try:
                lock = self._send_locks.setdefault(client, asyncio.Lock())
                async with asyncio.timeout(1.0):
                    async with lock:
                        await client.send_json(message)
            except Exception:
                self.discard(client)
                with suppress(Exception):
                    await asyncio.wait_for(client.close(code=1013, reason="Replay client could not keep up"), timeout=0.1)

        await asyncio.gather(*(send(client) for client in tuple(self.clients)))

    async def snapshot(self, *, include_baseline: bool = True) -> dict:
        async with self.lock:
            stats = await asyncio.to_thread(self.policy.stats)
            elapsed = max(asyncio.get_running_loop().time() - self._started_at, 0.0)
            result = {**stats, "online": not offline_enabled(), "replay": {
                "position": self.replayer.position,
                "speed": self.replayer.speed,
                "paused": self.replayer.paused,
                "exhausted": self.replayer.exhausted,
                "events_emitted": getattr(self.replayer, "events_emitted", self._events_processed),
                "events_processed": self._events_processed,
                "wall_seconds": elapsed,
                "events_per_second": self._events_processed / elapsed if elapsed else 0.0,
                "error": self.replay_error,
            }}
            if include_baseline:
                result["baseline"] = self.baseline
            return _wire(result)

    async def _pump(self) -> None:
        iterator = self.replayer.stream()
        try:
            async for event in iterator:
                async with self.lock:
                    generation = getattr(self.replayer, "generation", 0)
                    if generation != self._generation:
                        self.policy.reset_stream()
                        self._generation = generation
                    verdict = await _work(self.policy.score, event)
                    version = self.policy.model_version
                    self._events_processed += 1
                    await self.broadcast({"type": "event", "event": event,
                                          "verdict": asdict(verdict), "model_version": version})
            self.replay_finished = True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.replay_error = f"{type(error).__name__}: {error}"
            logger.exception("Replay stopped; source/model processing failed")
            await self.broadcast({"type": "error", **_error("replay_failed", "Replay stopped; inspect local server logs")})
        finally:
            if hasattr(iterator, "aclose"):
                await iterator.aclose()

    async def _ticker(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.stats_interval)
                await self.broadcast({"type": "stats", **await self.snapshot(include_baseline=False)})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Statistics broadcaster stopped")
            self.replay_error = self.replay_error or "Statistics broadcaster stopped"

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if hasattr(self.replayer, "aclose"):
            await self.replayer.aclose()
        for client in tuple(self.clients):
            with suppress(Exception):
                await asyncio.wait_for(client.close(code=1001, reason="EARSHOT server shutdown"), timeout=0.2)
            self.discard(client)


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ValueError(f"{path.name} must contain an object")
    return result


def load_runtime() -> Runtime:
    """Load local evidence and one site policy; no provider clients are created."""
    vocabulary = _load_json(PROJECT_ROOT / "demo/vocabulary.json")
    parse._vocabulary(vocabulary)
    baseline = _load_json(PROJECT_ROOT / "demo/baseline_stats.json")
    if "rates" not in baseline or "source" not in baseline:
        raise ValueError("Baseline artifact is missing its measured rates or source receipt")
    return Runtime(Replayer(), PolicyLayer(create_detector(), CONFIG.replay.buffer_events), vocabulary, baseline)


class TeachRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


class KillswitchRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    on: bool


def create_app(runtime_factory: Callable[[], Runtime] | None = None) -> FastAPI:
    factory = runtime_factory or load_runtime

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.runtime = None
        application.state.startup_error = None
        os.environ.setdefault("EARSHOT_OFFLINE", "1")
        set_offline(offline_enabled())
        try:
            runtime = await asyncio.to_thread(factory)
            application.state.runtime = runtime
            await runtime.start()
        except Exception as error:
            application.state.startup_error = f"{type(error).__name__}: {error}"
            logger.exception("EARSHOT initialization failed; health and local page remain available")
        try:
            yield
        finally:
            runtime = application.state.runtime
            if runtime is not None:
                await runtime.close()

    application = FastAPI(
        title="EARSHOT", description="Phase 7 local replay, text teaching and WebSocket API.",
        version="0.7.0", lifespan=lifespan, docs_url=None, redoc_url=None,
    )
    application.state.runtime = None
    application.state.startup_error = "Application lifespan has not started"

    @application.middleware("http")
    async def contain_errors(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception("Request failed: %s %s", request.method, request.url.path)
            return JSONResponse(_error("operation_failed", "Operation could not complete; inspect local server logs"), status_code=503)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        result = _error("invalid_request", "Request body did not match the API contract")
        result["error"]["details"] = [{"location": list(item["loc"]), "message": item["msg"], "type": item["type"]} for item in error.errors()]
        return JSONResponse(result, status_code=422)

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        detail = error.detail if isinstance(error.detail, dict) else {"code": f"http_{error.status_code}", "message": str(error.detail)}
        return JSONResponse(_error(detail.get("code", "request_failed"), detail.get("message", "Request failed")), status_code=error.status_code)

    def require_runtime() -> Runtime:
        runtime = application.state.runtime
        if runtime is None or application.state.startup_error:
            raise HTTPException(status_code=503, detail={"code": "runtime_unavailable", "message": "Local replay initialization failed; inspect /health and server logs"})
        return runtime

    @application.get("/", response_class=HTMLResponse)
    async def index():
        path = PROJECT_ROOT / "ui/index.html"
        if not path.is_file():
            raise HTTPException(status_code=503, detail={"code": "ui_unavailable", "message": "Local UI file is missing"})
        return FileResponse(path, media_type="text/html")

    @application.get("/health")
    async def health():
        runtime = application.state.runtime
        startup_error = application.state.startup_error
        if runtime is None or startup_error:
            return {**_error("runtime_unavailable", startup_error or "Runtime is unavailable"), "phase": 7,
                    "status": "unavailable", "replay_position": None, "model_version": None, "online": not offline_enabled()}
        return _wire({"ok": runtime.replay_error is None, "phase": 7,
                      "status": "degraded" if runtime.replay_error else "ready",
                      "replay_position": runtime.replayer.position, "model_version": runtime.policy.model_version,
                      "online": not offline_enabled(), "replay_error": runtime.replay_error,
                      "features": {"ingestion": True, "replay": True, "detection": True, "teaching": True, "parsing": True, "voice": False}})

    @application.get("/stats")
    async def stats():
        return await require_runtime().snapshot()

    @application.get("/rules")
    async def rules():
        runtime = require_runtime()
        async with runtime.lock:
            return [rule.model_dump(mode="json") for rule in runtime.policy.active_rules]

    @application.post("/teach")
    async def teach(request: TeachRequest):
        runtime = require_runtime()
        if runtime.replay_error:
            raise HTTPException(status_code=503, detail={"code": "replay_failed", "message": "Replay is unavailable; inspect local server logs"})
        rule = await asyncio.to_thread(parse.parse_utterance, request.text, runtime.vocabulary)
        # A switch during a provider call must not let its online result be
        # applied after the operator has requested local-only operation.
        if rule is not None and rule.confidence > 0.6 and offline_enabled():
            rule = await asyncio.to_thread(parse.parse_utterance, request.text, runtime.vocabulary)
        if rule is None:
            return {"parsed": False, "message": "no rule found in that"}
        async with runtime.lock:
            # Re-check after waiting for scoring/another teach: the operator
            # may have switched offline while this request waited for the lock.
            if rule.confidence > 0.6 and offline_enabled():
                rule = await _work(parse.parse_utterance, request.text, runtime.vocabulary)
                if rule is None:
                    return {"parsed": False, "message": "no rule found in that"}
                if rule.confidence > 0.6 and offline_enabled():
                    raise HTTPException(status_code=503, detail={"code": "offline_parse_required", "message": "Retry teaching with the offline parser"})
            before = runtime.policy.model_version
            learned = await _work(runtime.policy.learn, rule)
            # learn already rescans the buffer atomically; do not fit/score twice.
            result = {"parsed": True, "rule": rule.model_dump(mode="json"),
                      "model_version_before": before, **learned}
            await runtime.broadcast({"type": "taught", **result})
        return result

    @application.post("/undo/{rule_id}")
    async def undo(rule_id: str):
        runtime = require_runtime()
        async with runtime.lock:
            before = runtime.policy.model_version
            undone = await _work(runtime.policy.undo, rule_id)
            if not undone:
                raise HTTPException(status_code=404, detail={"code": "rule_not_reversible", "message": "No active reversible rule has that ID"})
            result = {"undone": True, "rule_id": rule_id, "model_version_before": before, "model_version": runtime.policy.model_version}
            await runtime.broadcast({"type": "undo", **result})
        return result

    @application.post("/killswitch")
    async def killswitch(request: KillswitchRequest):
        set_offline(request.on)
        result = {"online": not request.on, "offline": request.on}
        runtime = application.state.runtime
        if runtime is not None:
            await runtime.broadcast({"type": "link", **result})
        return result

    @application.websocket("/stream")
    async def stream(websocket: WebSocket):
        runtime = None
        try:
            await websocket.accept()
            if application.state.runtime is None or application.state.startup_error:
                await websocket.send_json({"type": "error", **_error("runtime_unavailable", "Replay is not ready; inspect /health")})
                await websocket.close(code=1013)
                return
            runtime = application.state.runtime
            runtime.clients.add(websocket)
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("WebSocket handler failed")
            with suppress(Exception):
                await websocket.close(code=1011)
        finally:
            if runtime is not None:
                runtime.discard(websocket)

    return application


app = create_app()
