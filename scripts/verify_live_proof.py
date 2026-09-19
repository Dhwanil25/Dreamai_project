"""Verify a running prewarmed EARSHOT app through its real HTTP/WebSocket API.

This makes one online teaching request and one offline teaching request, observes
recorded source events, and undoes only the rules created by this run. It never
reads provider credentials, resets the site's ledger, or fabricates observations.
Use --voice only when live ElevenLabs calls are explicitly wanted; the default
records dated historical voice receipts without calling ElevenLabs again.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from time import perf_counter
from urllib.parse import urlsplit
from uuid import uuid4

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import websockets

from earshot.config import CONFIG, PROJECT_ROOT


class VerificationFailure(Exception):
    """A bounded, safe verification failure suitable for the saved receipt."""


class PreconditionFailure(VerificationFailure):
    """The current app cannot be tested without interfering with site state."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def digest(state: dict) -> str:
    value = state.get("classifier_state_sha256")
    require(isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value),
            "The API did not supply an actual classifier fingerprint")
    return value


def overlaps(rule: dict, event: dict) -> bool:
    """Conservatively protect existing rules whose scope may affect a proof."""
    scope = rule.get("scope", {})
    return all(scope.get(key) in (None, "*", event[key])
               for key in ("turbine_id", "alarm_code"))


def _atomic_json(report: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent,
                                prefix=".live-verification-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def publish(report: dict, destination: Path) -> Path:
    """Archive every attempt; never replace a successful proof with a failure."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    attempt = destination.with_name(f"live_verification_{stamp}_{uuid4().hex[:8]}.json")
    _atomic_json(report, attempt)
    existing = None
    if destination.is_file():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    if isinstance(existing, dict) and existing.get("success") is True and not report["success"]:
        return attempt
    _atomic_json(report, destination)
    return attempt


async def verify(base_url: str, *, live_voice: bool = False) -> dict:
    started = perf_counter()
    report = {
        "schema_version": 1,
        "kind": "live_application_verification",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "status": "running",
        "success": False,
        "checks": {},
        "created_rule_ids": [],
        "interpretation": [
            "The HTTP application is live; its wind-farm observations are recorded source data.",
            "The online parser receipt identifies an actual provider request; offline parsing is local.",
            "Simple scoped rules change visibility explicitly; real local classifier weights also update.",
            "The stopping flag does not establish a dangerous incident or measured detection accuracy.",
            "The application's offline switch blocks its providers; this script does not disable Wi-Fi.",
            "Historical voice receipts are dated observations, not successful calls made by this run.",
            "Cleanup restores rules, classifier weights, online mode and pause state; replay position advances and model versions remain monotonic.",
        ],
    }
    initial = None
    initial_rules = None
    initial_hash = None
    touched = False
    phase = "preconditions"
    learned = report["created_rule_ids"]
    scenario = json.loads((PROJECT_ROOT / "demo/scenario.json").read_text(encoding="utf-8"))
    beats = {beat["id"]: beat for beat in scenario["beats"]}
    websocket_url = base_url.replace("http://", "ws://", 1) + "/stream"

    async with httpx.AsyncClient(base_url=base_url, timeout=30, trust_env=False,
                                 follow_redirects=False) as client:
        async def call(method: str, path: str, body: dict | None = None):
            response = await client.request(method, path, json=body) if body is not None else await client.request(method, path)
            if not response.is_success:
                # Provider errors are already sanitized by the app, but never
                # copy arbitrary error bodies into exception text or stdout.
                raise VerificationFailure(f"{method} {path} returned HTTP {response.status_code}")
            return response.json()

        async def teach(text: str, label: str) -> dict:
            response = await call("POST", "/teach", {"text": text})
            report[label] = response  # Preserve the entire receipt, even on a failed check.
            if response.get("parsed") and isinstance(response.get("rule_id"), str):
                learned.append(response["rule_id"])
            require(response.get("parsed") is True, f"{label} did not produce a rule")
            return response

        try:
            initial = await call("GET", "/stats")
            initial_rules = await call("GET", "/rules")
            evidence = await call("GET", "/evidence")
            report["initial"] = {key: value for key, value in initial.items() if key != "baseline"}
            report["initial_rules"] = initial_rules
            report["initial_evidence"] = evidence
            report["voice"] = {
                "called_this_run": live_voice,
                "historical_observations": {
                    key: {"observation": value, "retested_this_run": False}
                    for key, value in evidence.get("provider_validation", {}).items()
                    if key.startswith("elevenlabs")
                },
            }
            source = evidence.get("source_verification", {})
            require(evidence.get("data_kind") == "recorded_dataset_replay", "The app must label its data as recorded replay")
            require(source.get("status") == "verified", "Current application sources are not verified")
            require(bool(source.get("verified_at")), "Source verification lacks its current startup timestamp")
            require(source.get("alarm_verification", {}).get("all_processed_rows_match_original_zip") is True,
                    "Alarm source reconciliation is not verified")
            require(source.get("files", {}).get("alarms_parquet", {}).get("sha256") == scenario["source"]["alarms_sha256"],
                    "The server and proof scenario use different alarm sources")
            report["checks"]["current_source_verified"] = True
            initial_hash = digest(evidence["current_learning_state"])
            replay = initial["replay"]
            if (not replay.get("paused") or replay.get("position") != scenario["replay"]["warm_until_ts"]
                    or replay.get("speed") != scenario["replay"]["speed"]):
                raise PreconditionFailure("Start with the prewarmed app paused at the scenario warm timestamp and speed; no replay state was changed")
            protected = [beats["proof"]["suppressed_event"], beats["proof"]["untaught_stopping_event"],
                         beats["unplug"]["later_matching_event"]]
            conflicts = [rule.get("rule_id") for rule in initial_rules if any(overlaps(rule, event) for event in protected)]
            if conflicts:
                report["conflicting_rule_ids"] = conflicts
                raise PreconditionFailure("Existing site rules overlap proof scopes; they were preserved and no rules were taught")

            phase = "online_teaching"
            touched = True
            await call("POST", "/killswitch", {"on": False})
            async with websockets.connect(websocket_url, open_timeout=10, close_timeout=3,
                                           max_queue=1024, proxy=None) as stream:
                async def until(expected: dict, timeout: float = 35) -> dict:
                    async with asyncio.timeout(timeout):
                        async for raw in stream:
                            message = json.loads(raw)
                            if message.get("type") == "event" and all(
                                message.get("event", {}).get(key) == expected[key]
                                for key in ("ts", "turbine_id", "alarm_code")
                            ):
                                return message
                    raise VerificationFailure("The WebSocket closed before the expected recorded event")

                teach_started = perf_counter()
                first = await teach(beats["teach"]["utterance"], "online_teach")
                report["online_teach_ms"] = round((perf_counter() - teach_started) * 1000, 3)
                receipt = first.get("parse", {})
                require(receipt.get("source") == "provider" and receipt.get("provider_attempted") is True,
                        "Online teaching did not use a provider response")
                require(bool(receipt.get("request_id")) and bool(receipt.get("model")),
                        "Online parsing lacks an actual provider request ID or returned model")
                require(first["rule"]["scope"] == beats["teach"]["expected_rule"]["scope"]
                        and first["rule"]["action"] == "suppress", "The provider returned the wrong rule scope or action")
                require(first.get("examples_learned", 0) > 0, "Online teaching trained no actual examples")
                require(first.get("newly_suppressed_count") == beats["teach"]["expected_matching_buffer_alarms"],
                        "Online teaching did not suppress the four matching prewarmed alarms")
                receipt = first["evidence"]
                require(digest(receipt["learning_before"]) == initial_hash,
                        "Classifier changed between initial evidence and teaching")
                require(digest(receipt["learning_after"]) != initial_hash,
                        "Online teaching did not change the actual classifier weights")
                require(receipt.get("receipt_id") and receipt.get("receipt_persisted") is not False,
                        "The online teaching receipt was not persisted")
                report["checks"]["online_provider_and_classifier_change"] = True

                phase = "recorded_replay"
                resumed = perf_counter()
                await call("POST", "/replay", {"action": "resume"})
                recurrence = await until(beats["proof"]["suppressed_event"])
                report["recurrence"] = recurrence
                report["recurrence_seconds_after_resume"] = round(perf_counter() - resumed, 3)
                require(recurrence["verdict"]["show"] is False, "The next matching source alarm remained visible")
                require(first["rule_id"] == recurrence["verdict"]["suppressed_by"],
                        "The recurrence was not suppressed by the rule this run created")
                require(9 <= report["recurrence_seconds_after_resume"] <= 16,
                        "The prewarmed replay did not retain its measured 15x pacing")
                stopping = await until(beats["proof"]["untaught_stopping_event"])
                report["untouched_stopping"] = stopping
                report["stopping_seconds_after_resume"] = round(perf_counter() - resumed, 3)
                require(stopping["event"]["stopping"] == 1 and stopping["verdict"]["show"] is True,
                        "The untaught stopping record was not shown")
                report["checks"]["scoped_visibility_on_real_recurrence"] = True
                await call("POST", "/replay", {"action": "pause"})

                if live_voice:
                    phase = "optional_live_voice"
                    report["voice"]["current_calls"] = []
                    audio = await client.get("/speak", params={"text": first["confirmation"]})
                    voice_result = {"operation": "text_to_speech", "http_status": audio.status_code}
                    report["voice"]["current_calls"].append(voice_result)
                    if audio.is_success:
                        voice_result.update(audio_bytes=len(audio.content), audio_sha256=sha256(audio.content).hexdigest(),
                                            receipt_id=audio.headers.get("X-EARSHOT-Receipt-ID"))
                        require(audio.headers.get("content-type", "").startswith("audio/mpeg") and bool(audio.content),
                                "Live synthesis did not return MP3 audio")
                        (CONFIG.data.processed_dir / "live_verification_speech.mp3").write_bytes(audio.content)
                        observed = await call("GET", "/evidence")
                        voice_result["evidence"] = next((row for row in observed["recent_operations"]
                            if row.get("receipt_id") == voice_result["receipt_id"]), None)
                        require(voice_result["evidence"] and voice_result["evidence"].get("success") is True,
                                "Synthesis lacks a persisted successful provider receipt")
                    else:
                        voice_result["response"] = audio.json()
                        raise VerificationFailure(f"Optional live synthesis returned HTTP {audio.status_code}")
                    historical = evidence.get("provider_validation", {}).get("elevenlabs_stt", {})
                    source_audio = CONFIG.data.processed_dir / "human_reference_audio.wav"
                    audio_bytes = source_audio.read_bytes()
                    require(historical.get("input_kind") == "public_human_reference_recording"
                            and sha256(audio_bytes).hexdigest() == historical.get("sha256"),
                            "Optional transcription requires the verified real human reference recording")
                    transcription = await client.post("/listen", content=audio_bytes, headers={"Content-Type": "audio/wav"})
                    result = transcription.json()
                    report["voice"]["current_calls"].append({"operation": "speech_to_text", "http_status": transcription.status_code,
                                                              "response": result, "input_sha256": sha256(audio_bytes).hexdigest()})
                    if result.get("parsed") and isinstance(result.get("rule_id"), str):
                        learned.append(result["rule_id"])
                    require(transcription.is_success and result.get("transcription", {}).get("success") is True,
                            f"Optional live transcription did not succeed (HTTP {transcription.status_code})")
                    require(result.get("parsed") is False,
                            "The nonindustrial human reference unexpectedly produced a teaching rule")
                    after_transcription = await call("GET", "/evidence")
                    require(after_transcription["current_learning_state"] == observed["current_learning_state"],
                            "The nonindustrial human reference unexpectedly changed learning state")
                    report["checks"]["live_speech_and_noninstruction_no_learning"] = True

                phase = "offline_teaching"
                report["killswitch"] = await call("POST", "/killswitch", {"on": True})
                second = await teach(beats["unplug"]["utterance"], "offline_teach")
                receipt = second.get("parse", {})
                require(receipt.get("source") == "local" and receipt.get("provider_attempted") is False,
                        "Offline parsing attempted a provider or did not use local parsing")
                require(second["rule"]["scope"] == beats["unplug"]["expected_rule"]["scope"]
                        and second["rule"]["action"] == "suppress", "Offline teaching returned the wrong rule")
                require(second.get("examples_learned", 0) > 0, "Offline teaching trained no buffered source examples")
                require(digest(second["evidence"]["learning_before"]) != digest(second["evidence"]["learning_after"]),
                        "Offline teaching did not change the actual classifier weights")
                blocked_audio = await client.get("/speak", params={"text": first["confirmation"]})
                report["offline_speak"] = {"http_status": blocked_audio.status_code, "response": blocked_audio.json()}
                require(blocked_audio.status_code == 503
                        and report["offline_speak"]["response"].get("error", {}).get("code") == "OfflineError",
                        "Offline synthesis was not explicitly blocked")
                require(report["offline_speak"]["response"].get("evidence", {}).get("attempted") is False,
                        "Offline speech receipt does not prove the provider was never attempted")
                await call("POST", "/replay", {"action": "seek", "ts": beats["unplug"]["later_proof_seek_ts"]})
                later = await until(beats["unplug"]["later_matching_event"], timeout=12)
                report["offline_recurrence"] = later
                require(later["verdict"]["show"] is False and second["rule_id"] == later["verdict"]["suppressed_by"],
                        "The locally taught rule did not hide its later recorded match")
                report["checks"]["offline_learning_recurrence_and_provider_block"] = True
                report["proof_evidence"] = await call("GET", "/evidence")
                phase = "completed"
        except (Exception, asyncio.CancelledError) as error:
            report["status"] = "precondition_failed" if isinstance(error, PreconditionFailure) else "failed"
            report["failure"] = {"phase": phase, "type": type(error).__name__,
                                 "message": str(error) if isinstance(error, VerificationFailure) else "Verification interrupted or failed; see the last recorded phase and exception type"}
        finally:
            report["cleanup"] = {"undo": [], "errors": []}
            cleanup = report["cleanup"]
            if touched:
                async def restore(label: str, method: str, path: str, body: dict | None = None):
                    try:
                        return await call(method, path, body)
                    except Exception as error:
                        cleanup["errors"].append({"step": label, "type": type(error).__name__})
                        return None

                await restore("pause_before_undo", "POST", "/replay", {"action": "pause"})
                for identifier in reversed(learned):
                    cleanup["undo"].append(await restore("undo_created_rule", "POST", "/undo/" + identifier))
                await restore("online_mode", "POST", "/killswitch", {"on": not initial["online"]})
                await restore("pause_state", "POST", "/replay", {"action": "pause" if initial["replay"]["paused"] else "resume"})
                final_evidence = await restore("final_evidence", "GET", "/evidence")
                final_rules = await restore("final_rules", "GET", "/rules")
                final_stats = await restore("final_stats", "GET", "/stats")
                report["final_evidence"] = final_evidence
                cleanup["remaining_rules"] = final_rules
                cleanup["classifier_hash_restored"] = bool(final_evidence and
                    final_evidence.get("current_learning_state", {}).get("classifier_state_sha256") == initial_hash)
                cleanup["existing_rules_preserved"] = final_rules == initial_rules
                cleanup["online_restored"] = bool(final_stats and final_stats["online"] == initial["online"])
                cleanup["paused_restored"] = bool(final_stats and final_stats["replay"]["paused"] == initial["replay"]["paused"])
                if not all(cleanup[key] for key in ("classifier_hash_restored", "existing_rules_preserved", "online_restored", "paused_restored")):
                    cleanup["errors"].append({"step": "state_restoration", "type": "RestorationMismatch"})
                report["checks"]["undo_restores_actual_classifier_and_existing_rules"] = not cleanup["errors"]
            if phase == "completed" and not cleanup["errors"]:
                report.update(status="passed", success=True)
            elif cleanup["errors"]:
                report["status"] = "failed"
            report["elapsed_seconds"] = round(perf_counter() - started, 3)
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Running local prewarmed EARSHOT app")
    parser.add_argument("--voice", action="store_true", help="Also make live ElevenLabs synthesis/transcription calls through the app")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    address = urlsplit(base_url)
    if (address.scheme != "http" or address.hostname not in {"127.0.0.1", "localhost", "::1"}
            or address.username or address.password or address.path or address.query or address.fragment):
        parser.error("--base-url must be a local HTTP origin with no credentials, path or query")
    report = asyncio.run(verify(base_url, live_voice=args.voice))
    output = CONFIG.data.processed_dir / "live_verification.json"
    attempt = publish(report, output)
    print(json.dumps({"status": report["status"], "success": report["success"],
                      "elapsed_seconds": report["elapsed_seconds"], "checks": report["checks"],
                      "failure": report.get("failure"), "cleanup_errors": report["cleanup"]["errors"],
                      "receipt": str(attempt.relative_to(PROJECT_ROOT)),
                      "canonical_proof": "data/processed/live_verification.json"}, indent=2))
    return 0 if report["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
