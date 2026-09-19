"""Verify demo claims and assets against the actual local source artifacts.

The runtime integration warms one real source interval once. Every policy uses
an isolated temporary journal, and no test generates or replaces demo assets.
"""

import asyncio
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import socket
from time import perf_counter
import wave

import pandas as pd
import pytest

from earshot.config import CONFIG, PROJECT_ROOT
from earshot.detector import create_detector
from earshot import parse as parser
from earshot.policy import PolicyLayer
from earshot.replay import Replayer
from earshot.server import Runtime
from earshot import voice


@pytest.fixture(autouse=True)
def offline_without_network(monkeypatch):
    monkeypatch.setenv("EARSHOT_OFFLINE", "1")
    monkeypatch.setattr(parser, "OFFLINE_MODE", True)
    monkeypatch.setattr(voice, "OFFLINE_MODE", True)
    attempts = []

    def deny(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("Demo artifact validation must stay local")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(parser, "OpenAI", deny)
    monkeypatch.setattr(voice.requests, "post", deny)
    yield
    assert not attempts


@pytest.fixture(scope="module")
def scenario():
    return json.loads((PROJECT_ROOT / "demo/scenario.json").read_text())


@pytest.fixture(scope="module")
def vocabulary():
    return json.loads((PROJECT_ROOT / "demo/vocabulary.json").read_text())


@pytest.fixture(scope="module")
def manifest():
    return json.loads((PROJECT_ROOT / "demo/utterances/manifest.json").read_text())


@pytest.fixture(scope="module")
def alarms():
    path = CONFIG.data.processed_dir / "alarms.parquet"
    if not path.is_file():
        pytest.skip("Source-backed demo validation requires processed alarms.parquet")
    return pd.read_parquet(path)


def _beats(scenario):
    return {beat["id"]: beat for beat in scenario["beats"]}


def _assert_source_event(alarms, event):
    matches = alarms.loc[
        alarms.ts.eq(pd.Timestamp(event["ts"]))
        & alarms.turbine_id.eq(event["turbine_id"])
        & alarms.alarm_code.eq(event["alarm_code"])
    ]
    assert not matches.empty, f"Demo event does not exist in the source: {event}"
    assert (matches.description.eq(event["description"]) & matches.stopping.eq(event["stopping"])).any()


def _wav_facts(payload):
    with wave.open(BytesIO(payload), "rb") as wav:
        assert wav.getcomptype() == "NONE"
        assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
        assert wav.getframerate() == 16000
        frames = wav.readframes(wav.getnframes())
        assert len(frames) == wav.getnframes() * 2
        assert any(frames), "A declared speech recording must contain more than silence"
        return wav.getnframes() / wav.getframerate()


def test_scenario_receipt_identifies_the_actual_processed_source(scenario, alarms):
    assert scenario["status"] == "source_verified"
    relative = Path(scenario["source"]["alarms_path"])
    assert not relative.is_absolute() and ".." not in relative.parts
    source = PROJECT_ROOT / relative
    assert source.resolve() == (CONFIG.data.processed_dir / "alarms.parquet").resolve()
    assert sha256(source.read_bytes()).hexdigest() == scenario["source"]["alarms_sha256"]
    assert [beat["id"] for beat in scenario["beats"]] == ["pain", "teach", "proof", "unplug"]
    assert "unverified" in scenario["source"]["timezone_note"].lower()


def test_declared_alarm_rate_and_teaching_examples_match_real_trailing_hour(scenario, alarms, vocabulary):
    beats = _beats(scenario)
    window = beats["pain"]["trailing_window"]
    start, end = pd.Timestamp(window["start_exclusive"]), pd.Timestamp(window["end_inclusive"])
    observed = alarms.loc[alarms.ts.gt(start) & alarms.ts.le(end)]
    hours = (end - start).total_seconds() / 3600
    assert hours == window["hours"] == 1
    assert len(observed) == window["alarm_records"] == scenario["replay"]["warm_alarm_records"]
    assert len(observed) / hours == window["alarms_per_hour"]
    assert end == pd.Timestamp(scenario["replay"]["warm_until_ts"])
    rule = parser.parse_utterance(beats["teach"]["utterance"], vocabulary)
    assert rule is not None
    actual_matches = sum(rule.matches(event) for event in observed.to_dict("records"))
    assert actual_matches == beats["teach"]["expected_matching_buffer_alarms"]


@pytest.mark.parametrize("beat_id,event_name", [
    ("proof", "suppressed_event"),
    ("proof", "untaught_stopping_event"),
    ("unplug", "later_matching_event"),
])
def test_each_demonstrated_event_exists_in_the_real_alarm_log(scenario, alarms, beat_id, event_name):
    _assert_source_event(alarms, _beats(scenario)[beat_id][event_name])


def test_demo_utterances_match_only_the_declared_proof_scopes(scenario, vocabulary):
    beats = _beats(scenario)
    rules = {}
    for beat_id in ("teach", "unplug"):
        beat = beats[beat_id]
        rule = parser.parse_utterance(beat["utterance"], vocabulary)
        assert rule is not None
        assert rule.scope == beat["expected_rule"]["scope"]
        assert rule.action == beat["expected_rule"]["action"]
        rules[beat_id] = rule
    assert rules["teach"].matches(beats["proof"]["suppressed_event"])
    assert not rules["teach"].matches(beats["proof"]["untaught_stopping_event"])
    assert beats["proof"]["untaught_stopping_event"]["stopping"] == 1
    assert rules["unplug"].matches(beats["unplug"]["later_matching_event"])


def test_proof_timing_is_measured_from_the_declared_warm_resume(scenario):
    proof = _beats(scenario)["proof"]
    start = pd.Timestamp(scenario["replay"]["warm_until_ts"])
    for event_name, seconds in proof["seconds_after_warm_resume"].items():
        source_delta = (pd.Timestamp(proof[event_name]["ts"]) - start).total_seconds()
        assert seconds == pytest.approx(source_delta / scenario["replay"]["speed"])
    assert "seek" in proof["timing_note"].lower()


def test_manifest_declares_five_scripted_inputs_from_real_vocabulary(manifest, vocabulary, scenario):
    fixtures = manifest["fixtures"]
    assert len(fixtures) == 5
    assert {str(row["id"]) for row in fixtures} == {"1", "2", "3", "4", "5"}
    assert {str(row["key"]) for row in fixtures} == {"1", "2", "3", "4", "5"}
    assert "not speech recognition" in manifest["notice"].lower()
    vocab_path = PROJECT_ROOT / "demo/vocabulary.json"
    assert manifest["vocabulary_sha256"] == sha256(vocab_path.read_bytes()).hexdigest()
    by_id = {str(row["id"]): row for row in fixtures}
    for row in fixtures:
        rule = parser.parse_utterance(row["transcript"], vocabulary)
        assert rule is not None
        assert rule.scope == row["expected_scope"] and rule.action == row["expected_action"]
        assert row["confirmation"] == voice.confirmation(rule, vocabulary)
        assert row["kind"] and row["kind"] != "speech recognition"
    for beat_id in ("teach", "unplug"):
        beat = _beats(scenario)[beat_id]
        assert by_id[str(beat["fixture_id"])]["transcript"] == beat["utterance"]


@pytest.mark.parametrize("identifier", ["1", "2", "3", "4", "5"])
def test_fixture_wav_and_local_confirmation_cache_match_manifest(manifest, identifier):
    row = next(row for row in manifest["fixtures"] if str(row["id"]) == identifier)
    base = PROJECT_ROOT / "demo/utterances"
    assert Path(row["audio"]).name == row["audio"]
    audio = (base / row["audio"]).read_bytes()
    assert sha256(audio).hexdigest() == row["sha256"]
    assert _wav_facts(audio) == pytest.approx(row["duration_seconds"], abs=0.0001)
    expected_path = voice.cache_path(row["confirmation"])
    assert (base / row["confirmation_audio"]).resolve() == expected_path.resolve()
    cached = voice.cached_audio(row["confirmation"])
    assert cached is not None, "Confirmation must play locally with provider access blocked"
    assert sha256(cached).hexdigest() == row["confirmation_sha256"]
    assert _wav_facts(cached) == pytest.approx(row["confirmation_duration_seconds"], abs=0.0001)


def test_real_runtime_warms_declared_interval_and_exposes_consistent_console(scenario, vocabulary, alarms, tmp_path, record_property):
    scada = CONFIG.data.processed_dir / "scada.parquet"
    if not scada.is_file():
        pytest.skip("Full demo warm-up requires real processed scada.parquet")
    baseline = json.loads((PROJECT_ROOT / "demo/baseline_stats.json").read_text())
    policy = PolicyLayer(create_detector(), CONFIG.replay.buffer_events, rules_path=tmp_path / "rules.jsonl")
    replayer = Replayer()
    runtime = Runtime(replayer, policy, vocabulary, baseline)
    settings = scenario["replay"]
    rule = parser.parse_utterance(_beats(scenario)["teach"]["utterance"], vocabulary)
    assert rule is not None

    async def verify():
        start = perf_counter()
        try:
            await runtime.prepare_demo(settings)
            elapsed = perf_counter() - start
            observations = policy.observations()
            events = [event for event, verdict in observations]
            alarm_events = [event for event in events if event["alarm_code"] is not None]
            sensor_events = [event for event in events if event["alarm_code"] is None]
            assert len(events) == settings["warm_event_count"]
            assert len(alarm_events) == settings["warm_alarm_records"]
            assert len(sensor_events) == settings["warm_scada_snapshots"]
            assert len(events) == len(replayer.buffer)
            assert sum(rule.matches(event) for event in alarm_events) == _beats(scenario)["teach"]["expected_matching_buffer_alarms"]
            assert all(pd.Timestamp(event["ts"]) <= pd.Timestamp(settings["warm_until_ts"]) for event in events)
            assert replayer.paused and replayer.speed == settings["speed"]
            assert pd.Timestamp(replayer.position) == pd.Timestamp(settings["warm_until_ts"])
            assert policy.stats()["alarms_shown"] == len(alarm_events)
            assert policy.stats()["alarms_suppressed"] == 0
            assert policy.stats()["alarms_per_hour_current"] == _beats(scenario)["pain"]["trailing_window"]["alarms_per_hour"]
            assert not policy.rules_path.exists(), "Warm-up must not create operator corrections"
            console = await runtime.console_snapshot()
            repeated = await runtime.console_snapshot()
            assert console["generation"] == replayer.generation
            assert console["sequence"] == len(events)
            assert len(console["events"]) == min(200, len(alarm_events))
            assert len({row["id"] for row in console["events"]}) == len(console["events"])
            assert [row["id"] for row in console["events"]] == [row["id"] for row in repeated["events"]]
            assert console["events"][-1]["id"] == f"{replayer.generation}:{len(events)}"
            assert all(row["verdict"]["show"] for row in console["events"])
            # Resuming the real producer must honor source timing after the
            # accelerated warm-up; an old accelerated clock emitted a burst.
            next_alarm = alarms.loc[alarms.ts.gt(pd.Timestamp(settings["warm_until_ts"]))].iloc[0].to_dict()
            delay = (next_alarm["ts"] - pd.Timestamp(settings["warm_until_ts"])).total_seconds() / settings["speed"]
            assert delay > 0
            received = asyncio.Queue()

            class Capture:
                async def send_json(self, payload):
                    if payload["type"] == "event":
                        received.put_nowait(payload)

                async def close(self, **kwargs):
                    pass

            runtime.clients.add(Capture())
            await runtime.start()
            resumed_at = perf_counter()
            replayer.resume()
            message = await asyncio.wait_for(received.get(), timeout=delay + 3)
            paced_seconds = perf_counter() - resumed_at
            assert pd.Timestamp(message["event"]["ts"]) == next_alarm["ts"]
            _assert_source_event(alarms, message["event"])
            assert message["id"] == f"{replayer.generation}:{len(events) + 1}"
            assert paced_seconds >= delay * 0.8, "Warm-up left an accelerated clock ahead of source position"
            assert paced_seconds <= delay + 1.5
            return elapsed, paced_seconds
        finally:
            await runtime.close()

    elapsed, paced_seconds = asyncio.run(verify())
    record_property("real_demo_prepare_seconds", elapsed)
    record_property("first_event_after_warm_resume_seconds", paced_seconds)
    assert elapsed < 45, f"Real demo preparation took {elapsed:.3f}s"
