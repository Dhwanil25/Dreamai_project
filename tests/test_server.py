"""In-process API tests with real alarms and temporary rule journals.

Controlled replay changes only scheduling: emitted records come unchanged from
processed alarms. TestClient propagates unexpected server exceptions, while
socket and provider construction guards forbid real network calls.
"""

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
import os
import socket
import threading
from types import SimpleNamespace

import anyio
from fastapi.testclient import TestClient
import pandas as pd
import pytest
from starlette.websockets import WebSocketDisconnect

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.detector import create_detector
from earshot import parse as parser
from earshot.policy import PolicyLayer
from earshot import server, voice


TEACH_TEXT = "ignore the cable untwist alarm on turbine four"


@pytest.fixture(scope="module")
def source():
    paths = {
        "alarms": CONFIG.data.processed_dir / "alarms.parquet",
        "vocabulary": PROJECT_ROOT / "demo/vocabulary.json",
        "baseline": PROJECT_ROOT / "demo/baseline_stats.json",
    }
    if any(not path.is_file() for path in paths.values()):
        pytest.skip("API regressions require real processed alarms, vocabulary and baseline")
    frame = pd.read_parquet(paths["alarms"])
    vocabulary = json.loads(paths["vocabulary"].read_text())
    station = vocabulary["turbine_aliases"]["t04"]
    matches = frame.turbine_id.eq(station) & frame.alarm_code.eq(10105)
    positions = matches.to_numpy().nonzero()[0]
    assert len(positions) > 1
    end = max(2000, int(positions[0]) + 1)
    recent = frame.iloc[end - 2000:end]
    later = frame.iloc[end:]
    matching = later.loc[later.turbine_id.eq(station) & later.alarm_code.eq(10105)]
    other = later.loc[later.turbine_id.ne(station) & later.alarm_code.eq(10105)]
    assert len(recent) == 2000 and not matching.empty and not other.empty
    return SimpleNamespace(
        recent=recent.to_dict("records"),
        matching=matching.iloc[0].to_dict(),
        other=other.iloc[0].to_dict(),
        matching_count=int(matches.iloc[end - 2000:end].sum()),
        vocabulary=vocabulary,
        baseline=json.loads(paths["baseline"].read_text()),
    )


@pytest.fixture(autouse=True)
def offline_and_no_network(monkeypatch):
    monkeypatch.setenv("EARSHOT_OFFLINE", "1")
    monkeypatch.setattr(server, "OFFLINE_MODE", False)
    monkeypatch.setattr(parser, "OFFLINE_MODE", False)
    monkeypatch.setattr(voice, "OFFLINE_MODE", False, raising=False)
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("API tests must not use sockets or an unmocked provider")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(parser, "OpenAI", deny)
    monkeypatch.setattr(voice.requests, "post", deny)
    yield
    assert not attempts, "A caught outbound failure still counts as a network attempt"


class ControlledReplay:
    """Emit supplied real records only when the test releases them."""

    def __init__(self, recent):
        self.buffer = deque(deepcopy(recent), maxlen=2000)
        self.position = recent[-1]["ts"]
        self.speed = 600.0
        self.paused = False
        self.exhausted = False
        self.events_emitted = 0
        self.queue = asyncio.Queue()
        self.started = False
        self.stopped = False
        self.closed = False

    async def stream(self):
        self.started = True
        try:
            while True:
                event = await self.queue.get()
                self.position = event["ts"]
                self.buffer.append(deepcopy(event))
                self.events_emitted += 1
                yield deepcopy(event)
        finally:
            self.stopped = True

    async def emit(self, event):
        await self.queue.put(deepcopy(event))

    async def aclose(self):
        assert self.stopped, "Replay resources must close after the producer stops"
        self.closed = True

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


@pytest.fixture
def runtime(source, tmp_path):
    policy = PolicyLayer(create_detector(), buffer_size=2000, rules_path=tmp_path / "rules.jsonl")
    for event in source.recent:
        policy.score(event)
    return server.Runtime(
        replayer=ControlledReplay(source.recent),
        policy=policy,
        vocabulary=deepcopy(source.vocabulary),
        baseline=deepcopy(source.baseline),
        stats_interval=0.05,
    )


@pytest.fixture
def live(runtime):
    application = server.create_app(runtime_factory=lambda: runtime)
    with TestClient(application) as client:
        assert application.state.runtime is runtime
        assert application.state.startup_error is None
        yield SimpleNamespace(client=client, app=application, runtime=runtime)


def receive_type(websocket, wanted):
    """Bound TestClient's in-memory receive stream, including skipped stats."""
    async def receive_until():
        with anyio.fail_after(5):
            while True:
                message = await websocket._send_rx.receive()
                if message["type"] == "websocket.close":
                    raise WebSocketDisconnect(code=message.get("code", 1000))
                payload = json.loads(message["text"])
                if payload.get("type") == wanted:
                    return payload

    return websocket.portal.call(receive_until)


def assert_error(response, status):
    assert response.status_code == status
    body = response.json()
    assert body["ok"] is False
    assert isinstance(body["error"]["code"], str) and body["error"]["code"]
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body


def teach(client):
    response = client.post("/teach", json={"text": TEACH_TEXT})
    assert response.status_code == 200
    body = response.json()
    assert body["parsed"] is True
    return body


def test_root_serves_local_ui_independently_of_launch_directory(live, tmp_path, monkeypatch):
    alternate = tmp_path / "ui"
    alternate.mkdir()
    (alternate / "index.html").write_text("Incorrect launch-directory UI")
    monkeypatch.chdir(tmp_path)

    response = live.client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == (PROJECT_ROOT / "ui/index.html").read_text(encoding="utf-8")
    assert "EARSHOT" in response.text


def test_health_and_stats_report_live_policy_and_exact_local_baseline(live, source):
    response = live.client.get("/health")
    assert response.status_code == 200
    health = response.json()
    assert health["ok"] is True and health["status"] == "ready"
    assert health["phase"] == 10 and health["model_version"] == 1
    assert pd.Timestamp(health["replay_position"]) == source.recent[-1]["ts"]
    response = live.client.get("/stats")
    assert response.status_code == 200
    stats = response.json()
    for key, expected in live.runtime.policy.stats().items():
        assert stats[key] == expected
    assert stats["baseline"] == source.baseline
    assert stats["online"] is False
    assert pd.Timestamp(stats["replay"]["position"]) == source.recent[-1]["ts"]
    assert stats["replay"]["speed"] == 600
    assert live.client.get("/rules").json() == []


def test_stream_broadcasts_same_real_event_to_two_clients(live, source):
    with live.client.websocket_connect("/stream") as first, live.client.websocket_connect("/stream") as second:
        live.client.portal.call(live.runtime.replayer.emit, source.matching)
        left = receive_type(first, "event")
        right = receive_type(second, "event")

        assert left == right
        assert left["event"]["turbine_id"] == source.matching["turbine_id"]
        assert left["event"]["alarm_code"] == source.matching["alarm_code"]
        assert left["event"]["description"] == source.matching["description"]
        assert pd.Timestamp(left["event"]["ts"]) == source.matching["ts"]
        assert left["verdict"]["show"] is True
        assert len(live.runtime.clients) == 2


def test_periodic_stats_continue_without_new_source_events(live):
    with live.client.websocket_connect("/stream") as websocket:
        first = receive_type(websocket, "stats")
        second = receive_type(websocket, "stats")
        assert first["type"] == second["type"] == "stats"
        assert live.runtime.replayer.events_emitted == 0


def test_stats_publication_completes_before_a_new_teaching_revision(live, monkeypatch):
    """Hold a stats send and contend for its revision with a real teach call."""
    entered = threading.Event()
    parsed = threading.Event()
    release = asyncio.Event()
    original_broadcast = live.runtime.broadcast
    original_parse = parser.parse_utterance_with_evidence
    first_stats = True
    publication = []

    async def gated_broadcast(payload):
        nonlocal first_stats
        if payload["type"] == "stats" and first_stats:
            first_stats = False
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        await original_broadcast(payload)
        if payload["type"] in {"stats", "taught"}:
            publication.append((payload["type"], payload["model_version"]))

    def observed_parse(text, context):
        result = original_parse(text, context)
        parsed.set()
        return result

    monkeypatch.setattr(live.runtime, "broadcast", gated_broadcast)
    monkeypatch.setattr(parser, "parse_utterance_with_evidence", observed_parse)
    with live.client.websocket_connect("/stream") as websocket, ThreadPoolExecutor(max_workers=1) as executor:
        try:
            assert entered.wait(5), "No periodic stats publication reached the gate"
            # Holding publication protects the snapshot's revision, rather than
            # allowing a new version to overtake an already prepared message.
            assert live.runtime.lock.locked()
            request = executor.submit(live.client.post, "/teach", json={"text": TEACH_TEXT})
            assert parsed.wait(5)
            assert not request.done() and live.runtime.policy.model_version == 1
        finally:
            live.client.portal.call(release.set)
        result = request.result(timeout=5)
        assert result.status_code == 200
        stats = receive_type(websocket, "stats")
        taught = receive_type(websocket, "taught")
    assert stats["model_version"] == 1 and taught["model_version"] == 2
    assert publication.index(("stats", 1)) < publication.index(("taught", 2))
    assert "generation" in stats["replay"] and "sequence" in stats["replay"]


def test_teach_rescores_real_buffer_and_broadcasts_to_both_clients(live, source):
    before = live.runtime.policy.classifier.weights.copy()
    with live.client.websocket_connect("/stream") as first, live.client.websocket_connect("/stream") as second:
        result = teach(live.client)
        messages = [receive_type(first, "taught"), receive_type(second, "taught")]

    assert result["model_version_before"] == 1 and result["model_version"] == 2
    assert result["examples_learned"] == 2000
    assert result["newly_suppressed_count"] == source.matching_count
    assert result["rule"]["scope"] == {"turbine_id": "2304513", "alarm_code": 10105, "signal": None}
    assert live.runtime.policy.classifier.weights != before
    assert sum(not verdict.show for verdict in live.runtime.policy.verdicts) == source.matching_count
    assert live.client.get("/rules").json() == [result["rule"]]
    for message in messages:
        assert message["rule"] == result["rule"]
        assert message["model_version"] == 2
        assert message["newly_suppressed_count"] == source.matching_count


def test_taught_scope_hides_later_matching_event_but_preserves_other_turbine(live, source):
    result = teach(live.client)
    with live.client.websocket_connect("/stream") as websocket:
        for event in sorted([source.matching, source.other], key=lambda record: record["ts"]):
            live.client.portal.call(live.runtime.replayer.emit, event)
            message = receive_type(websocket, "event")
            if event["turbine_id"] == result["rule"]["scope"]["turbine_id"]:
                assert message["verdict"]["show"] is False
                assert message["verdict"]["suppressed_by"] == result["rule_id"]
            else:
                assert message["verdict"]["show"] is True
                assert message["verdict"]["suppressed_by"] is None


def test_unparseable_teaching_returns_200_without_mutation(live):
    before = live.runtime.policy.stats()

    response = live.client.post("/teach", json={"text": "what is the weather like"})

    assert response.status_code == 200
    result = response.json()
    assert result["parsed"] is False and result["message"] == "no rule found in that"
    assert result["parse"]["source"] == "none" and result["source"] == "text"
    assert live.runtime.policy.stats() == before
    assert not live.runtime.policy.rules_path.exists()


def test_undo_restores_visibility_model_and_broadcasts(live):
    learned = teach(live.client)
    with live.client.websocket_connect("/stream") as first, live.client.websocket_connect("/stream") as second:
        response = live.client.post("/undo/" + learned["rule_id"])
        assert response.status_code == 200
        result = response.json()
        messages = [receive_type(first, "undo"), receive_type(second, "undo")]
    assert result["undone"] is True
    assert result["model_version_before"] == 2 and result["model_version"] == 3
    assert all(verdict.show for verdict in live.runtime.policy.verdicts)
    assert not live.runtime.policy.classifier.weights
    assert live.client.get("/rules").json() == []
    assert all(message["model_version"] == 3 for message in messages)
    assert_error(live.client.post("/undo/" + learned["rule_id"]), 404)
    assert live.runtime.policy.model_version == 3


def test_killswitch_blocks_provider_and_teaching_still_works(live, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "server-test-placeholder")
    monkeypatch.setenv("LLM_MODEL", "server-test-model")
    with live.client.websocket_connect("/stream") as websocket:
        response = live.client.post("/killswitch", json={"on": True})
        assert response.status_code == 200
        assert response.json() == {"online": False, "offline": True}
        assert receive_type(websocket, "link")["online"] is False
        assert os.environ["EARSHOT_OFFLINE"] == "1"
        assert parser.OFFLINE_MODE is True and voice.OFFLINE_MODE is True
        assert teach(live.client)["rule"]["confidence"] == 0.6
        response = live.client.post("/killswitch", json={"on": False})
        assert response.status_code == 200
        assert response.json() == {"online": True, "offline": False}
        assert receive_type(websocket, "link")["online"] is True
        assert os.getenv("EARSHOT_OFFLINE") != "1"
        assert parser.OFFLINE_MODE is False and voice.OFFLINE_MODE is False


@pytest.mark.parametrize("path,payload", [
    ("/teach", {}),
    ("/teach", {"text": None}),
    ("/teach", {"text": 42}),
    ("/teach", {"text": "x" * 4001}),
    ("/killswitch", {}),
    ("/killswitch", {"on": "true"}),
    ("/killswitch", {"on": 1}),
])
def test_malformed_request_bodies_return_structured_422(live, path, payload):
    before = live.runtime.policy.stats()
    assert_error(live.client.post(path, json=payload), 422)
    assert live.runtime.policy.stats() == before


def test_invalid_json_returns_structured_422(live):
    response = live.client.post("/teach", content="{invalid", headers={"Content-Type": "application/json"})
    assert_error(response, 422)


def test_journal_commit_failure_returns_503_without_model_change(live, monkeypatch):
    policy = live.runtime.policy
    before = policy.stats()
    weights = policy.classifier.weights.copy()
    original_append = policy._append

    def fail_commit(record):
        if record["op"] == "commit":
            raise OSError("Simulated journal commit failure")
        original_append(record)

    monkeypatch.setattr(policy, "_append", fail_commit)
    response = live.client.post("/teach", json={"text": TEACH_TEXT})

    assert_error(response, 503)
    assert policy.stats() == before
    assert policy.classifier.weights == weights
    assert all(verdict.show for verdict in policy.verdicts)
    assert not policy.active_rules
    restarted = PolicyLayer(create_detector(), buffer_size=2000, rules_path=policy.rules_path)
    assert restarted.model_version == 1 and not restarted.active_rules


def test_one_failed_socket_does_not_break_broadcast_to_healthy_clients(live):
    class FailedSocket:
        async def send_json(self, payload):
            raise RuntimeError("Simulated disconnected WebSocket")

        async def close(self, **kwargs):
            pass

    dead = FailedSocket()
    with live.client.websocket_connect("/stream") as first, live.client.websocket_connect("/stream") as second:
        async def add_failed_client():
            live.runtime.clients.add(dead)
        live.client.portal.call(add_failed_client)
        result = teach(live.client)
        assert receive_type(first, "taught")["rule_id"] == result["rule_id"]
        assert receive_type(second, "taught")["rule_id"] == result["rule_id"]
        assert dead not in live.runtime.clients


def test_missing_dataset_reports_unavailable_and_closes_socket_cleanly():
    def missing_runtime():
        raise FileNotFoundError("Dataset not present: real alarm file missing")

    application = server.create_app(runtime_factory=missing_runtime)
    with TestClient(application) as client:
        response = client.get("/health")
        assert response.status_code == 200
        health = response.json()
        assert health["ok"] is False and health["status"] == "unavailable"
        assert health["phase"] == 10
        assert health["replay_position"] is None and health["model_version"] is None
        assert isinstance(application.state.startup_error, str)
        for method, path, payload in [
            ("GET", "/stats", None),
            ("GET", "/rules", None),
            ("POST", "/teach", {"text": TEACH_TEXT}),
            ("POST", "/undo/missing-rule", None),
        ]:
            assert_error(client.request(method, path, json=payload), 503)
        with client.websocket_connect("/stream") as websocket:
            message = receive_type(websocket, "error")
            assert message.get("error", message).get("code")
            with pytest.raises(WebSocketDisconnect) as closed:
                receive_type(websocket, "event")
            assert closed.value.code == 1013


def test_lifespan_shutdown_stops_replay_and_all_background_tasks(runtime):
    application = server.create_app(runtime_factory=lambda: runtime)
    with TestClient(application) as client:
        assert client.get("/health").json()["ok"] is True
        tasks = list(runtime.tasks)
        assert tasks and runtime.replayer.started
    assert all(task.done() for task in tasks)
    assert runtime.replayer.stopped and runtime.replayer.closed
    assert not runtime.clients


def test_openapi_contract_available_without_cdn_documentation(live):
    response = live.client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    assert document["info"]["title"] == "EARSHOT"
    assert {"/", "/health", "/stats", "/rules", "/teach", "/undo/{rule_id}", "/killswitch"}.issubset(document["paths"])
    assert live.client.get("/docs").status_code == 404
    assert live.client.get("/redoc").status_code == 404


def test_killswitch_reparses_online_proposal_after_waiting_for_policy_lock(live, monkeypatch):
    """Force the switch after initial parsing but before policy-lock entry."""
    assert live.client.post("/killswitch", json={"on": False}).status_code == 200
    original_parse = parser.parse_utterance_with_evidence
    original_lock = live.runtime.lock
    parsed = threading.Event()
    waiting_for_policy = threading.Event()
    calls = []
    online_ids = []

    def controlled_parse(text, context):
        offline = server.offline_enabled()
        rule, evidence = original_parse(text, context)
        assert rule is not None
        calls.append(offline)
        if not offline:
            # Provider confidence is a test control on a real-vocabulary rule.
            rule = rule.model_copy(update={"confidence": 0.9})
            evidence = {**evidence, "source": "provider", "provider": "unit-test-mock",
                        "provider_attempted": True, "attempts": 1,
                        "request_id": "unit-test-online-proposal", "fallback_reason": None}
            online_ids.append(rule.rule_id)
        parsed.set()
        return rule, evidence

    class ObservedLock:
        async def __aenter__(self):
            task = asyncio.current_task()
            if parsed.is_set() and not task.get_name().startswith("earshot-"):
                waiting_for_policy.set()
            await original_lock.acquire()
            return self

        async def __aexit__(self, *args):
            original_lock.release()

    monkeypatch.setattr(parser, "parse_utterance_with_evidence", controlled_parse)
    monkeypatch.setattr(live.runtime, "lock", ObservedLock())
    live.client.portal.call(original_lock.acquire)
    released = False
    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(live.client.post, "/teach", json={"text": TEACH_TEXT})
        try:
            assert waiting_for_policy.wait(5), "Teach did not reach the held policy lock"
            response = live.client.post("/killswitch", json={"on": True})
            assert response.status_code == 200 and response.json()["offline"] is True
            assert not request.done() and live.runtime.policy.model_version == 1
            live.client.portal.call(original_lock.release)
            released = True
            response = request.result(timeout=5)
        finally:
            if not released:
                live.client.portal.call(original_lock.release)

    assert response.status_code == 200
    result = response.json()
    assert calls == [False, True]
    assert result["rule"]["confidence"] == 0.6
    assert result["parse"]["source"] == "local"
    assert [attempt["source"] for attempt in result["parse_attempts"]] == ["provider", "local"]
    assert result["parse_attempts"][0]["provider_attempted"] is True
    assert result["parse_attempts"][0]["request_id"] == "unit-test-online-proposal"
    assert result["evidence"]["parse_attempts"] == result["parse_attempts"]
    assert result["rule_id"] not in online_ids
    assert result["model_version_before"] == 1 and result["model_version"] == 2
    assert [rule.rule_id for rule in live.runtime.policy.active_rules] == [result["rule_id"]]


@pytest.mark.parametrize("cancellations", [1, 2])
def test_cancelled_worker_drains_before_releasing_policy_lock(cancellations):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    order = []

    def mutation():
        order.append("worker-started")
        started.set()
        assert release.wait(5), "Test did not release the controlled mutation"
        order.append("worker-finished")
        finished.set()

    async def scenario():
        lock = asyncio.Lock()

        async def locked_work():
            try:
                async with lock:
                    await server._work(mutation)
            except asyncio.CancelledError:
                order.append("caller-cancelled")
                raise

        task = asyncio.create_task(locked_work())
        try:
            assert await asyncio.to_thread(started.wait, 5)
            for _ in range(cancellations):
                task.cancel()
                # A scheduled checkpoint lets each cancellation reach _work
                # without wall-clock sleeps or worker-speed assumptions.
                checkpoint = asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
                await checkpoint
                assert not task.done()
                assert lock.locked() and not finished.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
            assert finished.is_set() and not lock.locked()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert order == ["worker-started", "worker-finished", "caller-cancelled"]


def test_console_rescores_stable_ids_and_undo(live):
    initial = live.client.get('/console').json()
    assert initial['events'] and len(initial['events']) <= 200
    assert all(item['event']['alarm_code'] is not None for item in initial['events'])
    ids = [item['id'] for item in initial['events']]
    assert len(ids) == len(set(ids))
    assert all(item['verdict']['show'] for item in initial['events'])
    result = teach(live.client)
    updated = live.client.get('/console').json()
    assert [item['id'] for item in updated['events']] == ids
    assert updated['stats']['model_version'] == result['model_version']
    assert updated['rules'][0]['rule_id'] == result['rule_id']
    matches = [item for item in updated['events'] if item['event']['turbine_id'] == '2304513' and item['event']['alarm_code'] == 10105]
    assert matches and all(not item['verdict']['show'] for item in matches)
    assert all(item['model_version'] == result['model_version'] for item in updated['events'])
    live.client.post('/undo/' + result['rule_id']).raise_for_status()
    restored = live.client.get('/console').json()
    assert all(item['verdict']['show'] for item in restored['events'])
    assert restored['rules'] == []


def test_console_snapshot_cannot_mutate_scored_event(live):
    observations = live.runtime.policy.observations()
    observations[-1][0]['alarm_code'] = 999999
    assert live.runtime.policy.observations()[-1][0]['alarm_code'] != 999999


def test_voice_offline_rejects_upload_without_provider(live, monkeypatch):
    called = []
    monkeypatch.setattr(voice, 'transcribe_with_evidence', lambda *args: called.append(args))
    response = live.client.post('/listen', content=b'pretend microphone bytes', headers={'Content-Type': 'audio/wav'})
    assert_error(response, 503)
    assert not called
    assert live.client.get('/voice/status').json()['live_voice_available'] is False


def test_microphone_uses_common_teaching_pipeline(live, monkeypatch):
    server.set_offline(False)
    received = []
    transcription_receipt = {"provider": "unit-test-mock", "operation": "speech_to_text",
                             "attempted": True, "success": True, "request_id": "unit-test-stt",
                             "mocked": True}
    def transcription(data, mime):
        received.append((data, mime))
        return TEACH_TEXT, transcription_receipt
    monkeypatch.setattr(voice, 'transcribe_with_evidence', transcription)
    result = live.client.post('/listen', content=b'recorded', headers={'Content-Type': 'audio/webm;codecs=opus'}).json()
    assert received == [(b'recorded', 'audio/webm')]
    assert result['parsed'] and result['source'] == 'microphone'
    assert result['transcript'] == TEACH_TEXT
    assert result['transcription'] == transcription_receipt
    assert result['parse']['source'] == 'local'
    assert result['rule_id'] in result['confirmation'] or 'turbine' in result['confirmation'].lower()
    assert len(live.client.get('/rules').json()) == 1
    receipts = live.runtime.audit.snapshot()
    assert [receipt['operation'] for receipt in receipts] == ['speech_to_text', 'teach']
    assert receipts[0]['request_id'] == 'unit-test-stt'
    assert receipts[1]['source'] == 'microphone'


def test_microphone_result_discarded_if_switched_offline(live, monkeypatch):
    server.set_offline(False)
    def transcription(*args):
        server.set_offline(True)
        return TEACH_TEXT, {"provider": "unit-test-mock", "operation": "speech_to_text", "mocked": True}
    monkeypatch.setattr(voice, 'transcribe_with_evidence', transcription)
    response = live.client.post('/listen', content=b'recorded', headers={'Content-Type': 'audio/wav'})
    assert_error(response, 503)
    assert live.client.get('/rules').json() == []


def test_speech_uses_dynamic_provider_audio_and_persists_its_receipt(live, monkeypatch):
    server.set_offline(False)
    called = []
    mp3 = b'ID3unit-test-provider-audio'
    details = {"provider": "unit-test-mock", "operation": "text_to_speech", "attempted": True,
               "success": True, "request_id": "unit-test-tts", "mocked": True,
               "output_sha256": sha256(mp3).hexdigest()}

    def forbidden_cache(*args):
        pytest.fail('The website must not substitute a cached recording for dynamic speech')

    def synthesis(text):
        called.append(text)
        return mp3, details

    monkeypatch.setattr(voice, 'cached_audio', forbidden_cache, raising=False)
    monkeypatch.setattr(voice, 'speak_with_evidence', synthesis)
    response = live.client.get('/speak', params={'text': 'Got it.'})
    assert response.status_code == 200
    assert response.headers['content-type'] == 'audio/mpeg'
    assert response.headers['x-earshot-audio'] == 'elevenlabs'
    assert response.content == mp3 and called == ['Got it.']
    receipts = live.runtime.audit.snapshot()
    assert len(receipts) == 1 and receipts[0]['operation'] == 'text_to_speech'
    assert receipts[0]['request_id'] == 'unit-test-tts'
    assert response.headers['x-earshot-receipt-id'] == receipts[0]['receipt_id']
    assert receipts[0]['output_sha256'] == sha256(response.content).hexdigest()


def test_prerecorded_input_routes_are_unavailable_without_learning(live):
    before = live.runtime.policy.learning_state()
    for method, path in [("GET", "/demo/manifest"), ("GET", "/demo/audio/1.wav"),
                         ("POST", "/demo/teach/1"), ("POST", "/demo/teach/unknown")]:
        assert_error(live.client.request(method, path), 404)
    assert live.runtime.policy.learning_state() == before
    assert live.runtime.audit.snapshot() == []
    status = live.client.get('/voice/status').json()
    assert status['cached_confirmations'] is False and status['scripted_fixtures'] is False


def test_replay_controls_and_bad_timestamp(live):
    assert live.client.post('/replay', json={'action': 'pause'}).status_code == 200
    assert live.runtime.replayer.paused
    assert live.client.post('/replay', json={'action': 'resume'}).status_code == 200
    assert not live.runtime.replayer.paused
    assert_error(live.client.post('/replay', json={'action': 'seek'}), 422)
    assert_error(live.client.post('/replay', json={'action': 'erase'}), 422)


def test_failed_replay_control_returns_actionable_error(live):
    live.runtime.replay_error = 'Source read failed'
    response = live.client.post('/replay', json={'action':'resume'})
    body = assert_error(response, 503)
    assert 'restart' in body['error']['message'].lower()
    assert live.client.get('/health').json()['status'] == 'degraded'


def test_teaching_and_undo_receipts_fingerprint_actual_classifier_changes(live):
    policy = live.runtime.policy

    def actual_fingerprint():
        state = {"weights": dict(policy.classifier.weights), "intercept": policy.classifier.intercept}
        return sha256(json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    initial_weights = dict(policy.classifier.weights)
    initial_intercept = policy.classifier.intercept
    initial_hash = actual_fingerprint()
    result = teach(live.client)
    learned_hash = actual_fingerprint()
    receipt = result['evidence']

    assert receipt['operation'] == 'teach' and receipt['source'] == 'text'
    assert receipt['parse']['source'] == 'local' and not receipt['parse']['provider_attempted']
    assert receipt['learning_before']['classifier_state_sha256'] == initial_hash
    assert receipt['learning_after']['classifier_state_sha256'] == learned_hash != initial_hash
    assert policy.classifier.weights != initial_weights
    assert receipt['learning_after']['nonzero_weights'] == sum(value != 0 for value in policy.classifier.weights.values())
    assert receipt['learning_after']['training_examples'] == result['examples_learned'] == 2000
    assert receipt['learning_after']['training_batches'] == 1
    assert receipt['utterance_sha256'] == sha256(TEACH_TEXT.encode()).hexdigest()
    assert 'explicit_scoped_rule' in receipt['visibility_method']
    response = live.client.post('/undo/' + result['rule_id'])
    assert response.status_code == 200
    undone = response.json()['evidence']
    assert undone['operation'] == 'undo'
    assert undone['learning_before']['classifier_state_sha256'] == learned_hash
    assert undone['learning_after']['classifier_state_sha256'] == initial_hash == actual_fingerprint()
    assert dict(policy.classifier.weights) == initial_weights and policy.classifier.intercept == initial_intercept
    assert undone['learning_after']['training_examples'] == 0
    assert undone['learning_after']['model_version'] == 3
    persisted = [json.loads(line) for line in live.runtime.audit.path.read_text().splitlines()]
    assert persisted == [receipt, undone]


def test_evidence_reports_recorded_source_current_state_and_real_operation_receipts(live):
    initial = live.client.get('/evidence')
    assert initial.status_code == 200
    document = initial.json()
    assert document['data_kind'] == 'recorded_dataset_replay'
    assert document['current_learning_state'] == live.runtime.policy.learning_state()
    assert document['recent_operations'] == []
    assert any('not a live plant' in limit for limit in document['limits'])
    for provider, filename in [('nebius', 'nebius_live_parse.json'),
                               ('elevenlabs_stt', 'elevenlabs_live_stt.json'),
                               ('elevenlabs_tts', 'elevenlabs_live_tts.json')]:
        path = CONFIG.data.processed_dir / filename
        if path.is_file():
            assert document['provider_validation'][provider] == json.loads(path.read_text())
        else:
            assert provider not in document['provider_validation']
    result = teach(live.client)
    response = live.client.post('/undo/' + result['rule_id'])
    assert response.status_code == 200
    document = live.client.get('/evidence').json()
    assert document['current_learning_state'] == live.runtime.policy.learning_state()
    assert document['recent_operations'] == [result['evidence'], response.json()['evidence']]
    assert [row['operation'] for row in document['recent_operations']] == ['teach', 'undo']


def test_source_proof_is_invalidated_when_verified_files_change(live, tmp_path):
    source = tmp_path / 'recorded.csv'
    source.write_text('timestamp,value\n2025-02-21,12\n')
    live.runtime.source_evidence = {'status': 'verified', 'verified_at': 'startup'}
    live.runtime.source_file_stamps = {str(source): server._file_stamp(source)}
    assert live.client.get('/evidence').json()['source_verification']['status'] == 'verified'
    source.write_text('timestamp,value\n2025-02-21,1200\n')
    proof = live.client.get('/evidence').json()['source_verification']
    assert proof['status'] == 'invalidated'
    assert proof['changed_files'] == [str(source)]
    source.unlink()
    assert live.client.get('/evidence').json()['source_verification']['status'] == 'invalidated'


def test_old_receipt_file_does_not_verify_current_runtime(live, monkeypatch, tmp_path):
    (tmp_path / 'source_evidence.json').write_text('{"status":"verified","old":true}')
    monkeypatch.setattr(server.CONFIG.data, 'processed_dir', tmp_path)
    assert live.client.get('/evidence').json()['source_verification'] == {'status': 'not_verified'}


def test_invalid_unicode_cannot_fail_after_committing_teaching(live):
    before = live.runtime.policy.learning_state()
    payload = json.dumps({'text': TEACH_TEXT + '\ud800'}, ensure_ascii=True)
    response = live.client.post('/teach', content=payload, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    # Pydantic may reject the surrogate before the shared teaching guard.
    assert response.json()['error']['code'] in {'invalid_request', 'invalid_text_encoding'}
    assert live.runtime.policy.learning_state() == before
    assert live.runtime.audit.snapshot() == []


def test_invalid_provider_transcript_is_rejected_before_learning(live, monkeypatch):
    before = live.runtime.policy.learning_state()
    monkeypatch.setattr(voice, 'transcribe_with_evidence', lambda *_: (
        TEACH_TEXT + '\ud800', {'operation': 'speech_to_text', 'provider': 'unit-test-mock'}))
    live.client.post('/killswitch', json={'on': False})
    response = live.client.post('/listen', content=b'unit-test-mocked-audio', headers={'Content-Type': 'audio/wav'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'invalid_text_encoding'
    assert live.runtime.policy.learning_state() == before
    assert [receipt['operation'] for receipt in live.runtime.audit.snapshot()] == ['speech_to_text']
