"""One local replay/policy process with live HTTP and WebSocket interfaces.

Data/model initialization belongs to lifespan, never import. Run one Uvicorn
worker: the replay clock, connection set and site journal have one owner.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import logging
import math
from numbers import Integral, Real
import os
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.audit import OperationAudit
from earshot.detector import create_detector
from earshot.evidence import build_source_evidence
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
    _sequence: int = field(default=0, init=False)
    audit: OperationAudit = field(init=False)
    source_evidence: dict = field(default_factory=lambda: {"status": "not_verified"})
    source_file_stamps: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.stats_interval) or self.stats_interval <= 0:
            raise ValueError("stats_interval must be finite and positive")
        self.policy.bind_replay_buffer(self.replayer.buffer)
        self._generation = getattr(self.replayer, "generation", 0)
        self._sequence = len(self.policy.verdicts)
        self.audit = OperationAudit(self.policy.rules_path.with_name("operations.jsonl"))

    async def record_operation(self, operation: str, details: dict) -> dict:
        try:
            return await _work(self.audit.append, operation, _wire(details))
        except Exception:
            logger.exception("Operation evidence could not be persisted")
            # Teaching itself is already durable in the policy journal. Do not
            # invite a duplicate correction by disguising it as a failed teach.
            return {"operation": operation, **details, "receipt_persisted": False}

    async def prepare_demo(self, settings: dict) -> None:
        """Warm on genuine chronological observations and pause for the presenter."""
        self.replayer.seek(settings["seek_ts"])
        self._generation = self.replayer.generation
        self.replayer.speed = 1e9
        target = datetime.fromisoformat(settings["warm_until_ts"])
        iterator = self.replayer.stream()
        try:
            async with asyncio.timeout(45):
                async for event in iterator:
                    await _work(self.policy.score, event)
                    self._sequence += 1
                    self._events_processed += 1
                    if datetime.fromisoformat(event["ts"]) >= target:
                        break
        finally:
            await iterator.aclose()
            self.replayer.pause(at_position=True)
            self.replayer.speed = settings.get("speed", CONFIG.replay.speed)

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

    async def _snapshot_locked(self, *, include_baseline: bool = True) -> dict:
        stats = await asyncio.to_thread(self.policy.stats)
        elapsed = max(asyncio.get_running_loop().time() - self._started_at, 0.0)
        result = {**stats, "online": not offline_enabled(), "replay": {
            "generation": self._generation,
            "sequence": self._sequence,
            "position": self.replayer.position,
            "source_start": getattr(self.replayer, "source_start", None),
            "source_end": getattr(self.replayer, "source_end", None),
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

    async def snapshot(self, *, include_baseline: bool = True) -> dict:
        async with self.lock:
            return await self._snapshot_locked(include_baseline=include_baseline)

    async def console_snapshot(self) -> dict:
        async with self.lock:
            observations = await _work(self.policy.observations)
            first = self._sequence - len(observations) + 1
            events = [{"id": f"{self._generation}:{first + offset}", "event": event,
                       "verdict": asdict(verdict), "model_version": self.policy.model_version}
                      for offset, (event, verdict) in enumerate(observations)
                      if event.get("alarm_code") is not None][-200:]
            return _wire({"stats": await self._snapshot_locked(),
                          "rules": [rule.model_dump(mode="json") for rule in self.policy.active_rules],
                          "events": events, "vocabulary": self.vocabulary,
                          "generation": self._generation, "sequence": self._sequence})

    async def _pump(self) -> None:
        iterator = self.replayer.stream()
        try:
            async for event in iterator:
                emitted_generation = getattr(self.replayer, "generation", 0)
                async with self.lock:
                    generation = getattr(self.replayer, "generation", 0)
                    if emitted_generation != generation:
                        continue
                    if generation != self._generation:
                        self.policy.reset_stream()
                        self._generation = generation
                        self._sequence = 0
                    verdict = await _work(self.policy.score, event)
                    version = self.policy.model_version
                    self._events_processed += 1
                    self._sequence += 1
                    await self.broadcast({"type": "event", "event": event,
                                          "id": f"{generation}:{self._sequence}",
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
                # Preserve ordering with taught/undo/seek event frames.
                async with self.lock:
                    await self.broadcast({"type": "stats", **await self._snapshot_locked(include_baseline=False)})
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
    runtime = Runtime(Replayer(), PolicyLayer(create_detector(), CONFIG.replay.buffer_events), vocabulary, baseline)
    # Never promote an old receipt to current proof. Reconcile the actual files
    # on startup and label the resulting snapshot with its observation time.
    try:
        source = build_source_evidence()
        source["verified_at"] = datetime.now(timezone.utc).isoformat()
        runtime.source_evidence = source
        runtime.source_file_stamps = {
            item["path"]: _file_stamp(PROJECT_ROOT / item["path"])
            for item in source["files"].values()}
    except Exception as error:
        logger.exception("Source verification failed; recorded replay is not verified")
        runtime.source_evidence = {"status": "verification_failed", "error_type": type(error).__name__}
    return runtime


def _file_stamp(path: Path) -> tuple | None:
    try:
        info = path.stat()
        return info.st_size, info.st_mtime_ns, info.st_ino
    except OSError:
        return None


def current_source_evidence(runtime: Runtime) -> dict:
    changed = [name for name, stamp in runtime.source_file_stamps.items()
               if _file_stamp(PROJECT_ROOT / name) != stamp]
    if changed:
        return {"status": "invalidated", "changed_files": changed,
                "message": "Source files changed since startup; restart to verify them again."}
    return runtime.source_evidence


class TeachRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


class KillswitchRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    on: bool


class ReplayRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    action: Literal["pause", "resume", "seek"]
    ts: str | None = Field(default=None, max_length=64)


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
            if os.getenv("EARSHOT_DEMO") == "1" and runtime_factory is None:
                scenario = _load_json(PROJECT_ROOT / "demo/scenario.json")
                await runtime.prepare_demo(scenario["replay"])
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
        title="EARSHOT", description="Local replay, reversible operator teaching, console and optional voice.",
        version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None,
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
            return {**_error("runtime_unavailable", startup_error or "Runtime is unavailable"), "phase": 10,
                    "status": "unavailable", "replay_position": None, "model_version": None, "online": not offline_enabled()}
        return _wire({"ok": runtime.replay_error is None, "phase": 10,
                      "status": "degraded" if runtime.replay_error else "ready",
                      "replay_position": runtime.replayer.position, "model_version": runtime.policy.model_version,
                      "online": not offline_enabled(), "replay_error": runtime.replay_error,
                      "features": {"ingestion": True, "replay": True, "detection": True, "teaching": True, "parsing": True, "voice": True}})

    @application.get("/stats")
    async def stats():
        return await require_runtime().snapshot()

    @application.get("/console")
    async def console():
        return await require_runtime().console_snapshot()

    @application.post("/replay")
    async def replay(request: ReplayRequest):
        runtime = require_runtime()
        if runtime.replay_error:
            raise HTTPException(503, detail="Replay failed; inspect local logs and restart the server")
        async with runtime.lock:
            if request.action == "seek":
                if not request.ts:
                    raise HTTPException(422, detail="Seek requires a timestamp")
                try:
                    target = runtime.replayer.validate_seek(request.ts)
                    runtime.replayer.seek(target)
                except (ValueError, TypeError) as error:
                    raise HTTPException(422, detail={"code": "invalid_replay_timestamp", "message": str(error)}) from error
                runtime.policy.reset_stream()
                runtime._generation = runtime.replayer.generation
                runtime._sequence = 0
                runtime.replayer.resume()
                if runtime.replay_finished:
                    runtime.replay_finished = False
                    runtime.tasks[0] = asyncio.create_task(runtime._pump(), name="earshot-replay")
            elif request.action == "pause":
                runtime.replayer.pause()
            else:
                runtime.replayer.resume()
            result = {"type": "replay", "action": request.action, "generation": runtime._generation}
            await runtime.broadcast(result)
        return result

    @application.get("/rules")
    async def rules():
        runtime = require_runtime()
        async with runtime.lock:
            return [rule.model_dump(mode="json") for rule in runtime.policy.active_rules]

    @application.post("/teach")
    async def teach(request: TeachRequest):
        return await apply_teaching(request.text)

    async def apply_teaching(text: str, *, source: str = "text"):
        try:
            utterance_hash = sha256(text.encode("utf-8")).hexdigest()
        except UnicodeEncodeError as error:
            raise HTTPException(422, detail={"code": "invalid_text_encoding", "message": "Correction must contain valid Unicode text."}) from error
        runtime = require_runtime()
        if runtime.replay_error:
            raise HTTPException(status_code=503, detail={"code": "replay_failed", "message": "Replay is unavailable; inspect local server logs"})
        rule, parse_receipt = await asyncio.to_thread(parse.parse_utterance_with_evidence, text, runtime.vocabulary)
        parse_attempts = [dict(parse_receipt)]
        # A switch during a provider call must not let its online result be
        # applied after the operator has requested local-only operation.
        if rule is not None and parse_receipt["source"] == "provider" and offline_enabled():
            rule, parse_receipt = await asyncio.to_thread(parse.parse_utterance_with_evidence, text, runtime.vocabulary)
            parse_attempts.append(dict(parse_receipt))
        if rule is None:
            return {"parsed": False, "message": "no rule found in that", "parse": parse_receipt, "parse_attempts": parse_attempts, "source": source}
        async with runtime.lock:
            # Re-check after waiting for scoring/another teach: the operator
            # may have switched offline while this request waited for the lock.
            if parse_receipt["source"] == "provider" and offline_enabled():
                rule, parse_receipt = await _work(parse.parse_utterance_with_evidence, text, runtime.vocabulary)
                parse_attempts.append(dict(parse_receipt))
                if rule is None:
                    return {"parsed": False, "message": "no rule found in that", "parse": parse_receipt, "parse_attempts": parse_attempts, "source": source}
                if parse_receipt["source"] == "provider" and offline_enabled():
                    raise HTTPException(status_code=503, detail={"code": "offline_parse_required", "message": "Retry teaching with the offline parser"})
            before = runtime.policy.model_version
            state_before = await _work(runtime.policy.learning_state)
            confirmation = voice.confirmation(rule, runtime.vocabulary)
            learned = await _work(runtime.policy.learn, rule)
            state_after = await _work(runtime.policy.learning_state)
            evidence = await runtime.record_operation("teach", {
                "source": source, "utterance_sha256": utterance_hash,
                "rule_id": rule.rule_id, "scope": rule.scope, "action": rule.action,
                "parse": parse_receipt, "parse_attempts": parse_attempts,
                "learning_before": state_before, "learning_after": state_after,
                "examples_learned": learned["examples_learned"],
                "newly_suppressed_count": learned["newly_suppressed_count"],
                "visibility_method": "explicit_scoped_rule; classifier also updates from buffered examples",
                "offline": offline_enabled()})
            # learn already rescans the buffer atomically; do not fit/score twice.
            result = {"parsed": True, "rule": rule.model_dump(mode="json"),
                      "confirmation": confirmation,
                      "source": source, "parse": parse_receipt, "parse_attempts": parse_attempts, "evidence": evidence,
                      "model_version_before": before, **learned}
            await runtime.broadcast({"type": "taught", **result})
        return result

    @application.get("/voice/status")
    async def voice_status():
        return {"online": not offline_enabled(), "provider_available": voice.provider_available(),
                "live_voice_available": not offline_enabled() and voice.provider_available(),
                "availability_meaning": "Configured key only; successful provider calls are recorded separately",
                "cached_confirmations": False, "scripted_fixtures": False}

    @application.exception_handler(voice.VoiceError)
    async def voice_error(request: Request, error: voice.VoiceError):
        status = 422 if isinstance(error, voice.VoiceInputError) else 503
        result = _error(type(error).__name__, str(error))
        receipt = getattr(error, "evidence", None)
        if receipt:
            result["evidence"] = receipt
            runtime = application.state.runtime
            if runtime is not None:
                await runtime.record_operation(receipt.get("operation", "voice_failure"), receipt)
        return JSONResponse(result, status_code=status)

    @application.post("/listen")
    async def listen(request: Request):
        # Never accept/upload microphone data to a provider after local-only is set.
        if offline_enabled():
            raise voice.OfflineError("Cloud transcription is disabled. Use on-device speech or text teaching.")
        mime = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        audio = bytearray()
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                audio.extend(chunk)
                if len(audio) > voice.MAX_AUDIO_BYTES:
                    raise HTTPException(413, detail="Audio clip exceeds 8 MB")
        transcript, transcription_receipt = await asyncio.to_thread(voice.transcribe_with_evidence, bytes(audio), mime)
        await require_runtime().record_operation("speech_to_text", transcription_receipt)
        if offline_enabled():
            raise voice.OfflineError("Offline mode changed during transcription. Please teach again locally.")
        result = await apply_teaching(transcript, source="microphone")
        return {**result, "transcript": transcript, "source": "microphone", "transcription": transcription_receipt}

    @application.get("/speak")
    async def speak(text: str = Query(min_length=1, max_length=1000)):
        audio, receipt = await asyncio.to_thread(voice.speak_with_evidence, text)
        persisted = await require_runtime().record_operation("text_to_speech", receipt)
        headers = {"X-EARSHOT-Audio": "elevenlabs", "X-EARSHOT-Receipt-ID": persisted.get("receipt_id", "unpersisted")}
        return Response(audio, media_type="audio/mpeg", headers=headers)

    @application.get("/evidence")
    async def evidence():
        runtime = require_runtime()
        source = await asyncio.to_thread(current_source_evidence, runtime)
        providers = {}
        for name, filename in (("nebius", "nebius_live_parse.json"),
                               ("elevenlabs_stt", "elevenlabs_live_stt.json"),
                               ("elevenlabs_tts", "elevenlabs_live_tts.json")):
            path = CONFIG.data.processed_dir / filename
            if path.is_file():
                providers[name] = await asyncio.to_thread(_load_json, path)
        async with runtime.lock:
            current = await _work(runtime.policy.learning_state)
            recent = await _work(runtime.audit.snapshot)
            audit_integrity = await _work(runtime.audit.integrity_status)
        return _wire({"data_kind": "recorded_dataset_replay", "source_verification": source,
                      "current_learning_state": current, "recent_operations": recent,
                      "operation_history_integrity": audit_integrity,
                      "provider_validation": providers,
                      "limits": ["Recorded wind-farm data, not a live plant connection.",
                                 "Operator corrections are labels, not independently verified false alarms.",
                                 "Simple code rules change visibility explicitly; local classifier weights also learn.",
                                 "Provider key presence does not prove API permission or successful processing.",
                                 "Speech synthesis produces computer-generated read-back, never replacement input.",
                                 "No prerecorded demo input or fixed transcript is served by this website."]})

    @application.post("/undo/{rule_id}")
    async def undo(rule_id: str):
        runtime = require_runtime()
        async with runtime.lock:
            before = runtime.policy.model_version
            state_before = await _work(runtime.policy.learning_state)
            undone = await _work(runtime.policy.undo, rule_id)
            if not undone:
                raise HTTPException(status_code=404, detail={"code": "rule_not_reversible", "message": "No active reversible rule has that ID"})
            receipt = await runtime.record_operation("undo", {"rule_id": rule_id,
                "learning_before": state_before, "learning_after": await _work(runtime.policy.learning_state)})
            result = {"undone": True, "rule_id": rule_id, "model_version_before": before,
                      "model_version": runtime.policy.model_version, "evidence": receipt}
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
