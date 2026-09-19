"""Verify the local console. Browser API mocks are explicitly not provider proof.

Real teaching uses the offline parser and an alarm from the current replay.
Cleanup owns only rule IDs returned to this verifier's pages. Playback is never
seeked: its prior paused/running setting is restored while source time advances.
Requires optional Playwright and installed Chrome.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import monotonic
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import expect, sync_playwright
from earshot.config import CONFIG


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    origin = parser.parse_args().url.rstrip("/")
    assert urlparse(origin).hostname in ("127.0.0.1", "localhost"), "Local server required"
    output = CONFIG.data.processed_dir
    output.mkdir(parents=True, exist_ok=True)
    errors, requests, teaching_requests, cleanup_errors = [], [], [], []
    owned_rule_ids: set[str] = set()
    evidence = {"checked_at": datetime.now(timezone.utc).isoformat(),
                "success": False, "provider_calls_initiated_by_verifier": False,
                "browser_mocks_are_provider_proof": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="en-US")
        context.add_init_script("""
          window.__earshotSockets=[]; window.__earshotAnimations=[];
          const NativeWebSocket=window.WebSocket;
          window.WebSocket=class extends NativeWebSocket {
            constructor(...args){super(...args);window.__earshotSockets.push(this);}
          };
          document.addEventListener('animationstart', e=>window.__earshotAnimations.push({name:e.animationName,id:e.target.id}));
        """)
        context.on("request", lambda request: requests.append(request.url))

        def api(method, path, data=None, *, timeout=30000):
            response = context.request.fetch(origin + path, method=method, data=data, timeout=timeout)
            assert response.ok, (path, response.status, response.text())
            return response.json()

        def track(page):
            page.on("pageerror", lambda error: errors.append(str(error)))

            def requested(request):
                if request.method == "POST" and urlparse(request.url).path in ("/teach", "/listen"):
                    teaching_requests.append(urlparse(request.url).path)

            def responded(response):
                if (response.request.method != "POST" or not response.ok
                        or urlparse(response.url).path not in ("/teach", "/listen")):
                    return
                try:
                    receipt = response.json()
                    rule_id = receipt.get("rule_id") or receipt.get("rule", {}).get("rule_id")
                    if receipt.get("parsed") is True and isinstance(rule_id, str):
                        owned_rule_ids.add(rule_id)
                except Exception as error:
                    errors.append("Could not inspect own teaching receipt: " + type(error).__name__)
            page.on("request", requested)
            page.on("response", responded)

        prior = api("GET", "/stats")
        prior_online, prior_paused = prior["online"], prior["replay"]["paused"]
        evidence["prior_replay"] = prior["replay"]
        try:
            api("POST", "/killswitch", {"on": True})
            api("POST", "/replay", {"action": "pause"})
            page = context.new_page()
            track(page)
            page.goto(origin + "/#console", wait_until="networkidle")
            expect(page.locator("#modelVersion")).not_to_have_text("v—")
            expect(page.locator("#replayToggle")).to_have_text("Resume")
            expect(page.locator("#offlineToggle")).to_have_attribute("aria-checked", "true")
            page.locator("#soundEnabled").uncheck()
            expect(page.locator(".site small")).to_have_text("RECORDED DATA · REPLAY")
            expect(page.get_by_role("link", name="Inspect evidence", exact=False)).to_have_attribute("href", "/evidence")
            expect(page.locator("#teachingProvenance")).to_contain_text("Configured credentials do not verify")
            assert page.locator("#shortcuts, .scripted, #scenarioSelect").count() == 0
            assert "/demo/teach" not in page.content() and "/demo/manifest" not in page.content()
            retired = {}
            for method, path in (("GET", "/demo/manifest"), ("GET", "/demo/scenario"),
                                 ("GET", "/demo/audio/1.wav"), ("POST", "/demo/teach/1")):
                response = context.request.fetch(origin + path, method=method)
                retired[path] = response.status
                assert response.status == 404, (path, response.status)
            evidence["removed_routes"] = retired
            page.locator("body").click(position={"x": 5, "y": 5})
            before_requests = len(teaching_requests)
            for key in "12345":
                page.keyboard.press(key)
            page.wait_for_timeout(400)
            assert len(teaching_requests) == before_requests
            evidence["numeric_keys_do_not_teach"] = True

            # Advance genuine source events without clearing the existing buffer.
            before_replay = api("GET", "/stats")["replay"]
            assert not before_replay["exhausted"], "Choose an unexhausted recording before verification"
            page.locator("#replayToggle").click()
            expect(page.locator("#replayToggle")).to_have_text("Pause")
            # A delayed UI clock update can arrive without a new source event.
            # Wait for authoritative sequence and position advancement instead.
            replay_poll = {"before": before_replay, "last": before_replay,
                           "samples": 0, "advanced": False, "last_error": None}
            evidence["replay_advance_poll"] = replay_poll
            started = monotonic()
            deadline = started + 55
            while monotonic() < deadline:
                remaining_ms = max(1, int((deadline - monotonic()) * 1000))
                try:
                    current = api("GET", "/stats", timeout=min(5000, remaining_ms))["replay"]
                    replay_poll["last"] = current
                    replay_poll["samples"] += 1
                    if (current["generation"] == before_replay["generation"]
                            and current["sequence"] > before_replay["sequence"]
                            and current["position"] != before_replay["position"]):
                        replay_poll["advanced"] = True
                        break
                except Exception as error:
                    replay_poll["last_error"] = type(error).__name__ + ": " + str(error)
                remaining_ms = max(0, int((deadline - monotonic()) * 1000))
                if remaining_ms:
                    page.wait_for_timeout(min(500, remaining_ms))
            replay_poll["elapsed_seconds"] = round(monotonic() - started, 3)
            assert replay_poll["advanced"], "Replay did not advance: " + json.dumps(replay_poll)
            page.locator("#replayToggle").click()
            expect(page.locator("#replayToggle")).to_have_text("Resume")
            snapshot = api("GET", "/console")
            assert snapshot["stats"]["replay"]["sequence"] > before_replay["sequence"]
            assert snapshot["stats"]["replay"]["position"] != before_replay["position"]
            expect(page.locator("#alarmFeed .event").first).to_be_visible()
            evidence["real_replay_advanced"] = True
            evidence["initial_rate"] = page.locator("#rateNumber").inner_text()
            evidence["initial_version"] = page.locator("#modelVersion").inner_text()
            page.screenshot(path=str(output / "phase8_console_before.png"), full_page=True)
            labels = snapshot["vocabulary"]["turbine_labels"]
            candidates = [item for item in reversed(snapshot["events"])
                          if item["event"].get("stopping") == 0 and item["verdict"]["show"]
                          and labels.get(str(item["event"]["turbine_id"]), "").startswith("T")]
            assert candidates, "Need a shown non-stopping source alarm for teaching regression"
            chosen = candidates[0]
            event = chosen["event"]
            turbine = int(labels[str(event["turbine_id"])][1:])
            utterance = f"ignore alarm {event['alarm_code']} on turbine {turbine}"
            page.locator("#teachText").fill(utterance)
            with page.expect_response(lambda response: urlparse(response.url).path == "/teach") as teaching:
                page.locator("#teachText").press("Enter")
            taught = teaching.value.json()
            assert taught["parsed"] and taught["parse"]["source"] == "local"
            assert taught["source"] == "text" and taught["evidence"]
            rule_id = taught["rule"]["rule_id"]
            owned_rule_ids.add(rule_id)
            card = page.locator(f'#ruleLedger .rule-card[data-rule-id="{rule_id}"]')
            expect(card).to_be_visible()
            expect(page.locator("#teachingProvenance")).to_contain_text("Parser: local")
            expect(page.locator("#responseEvidence")).to_be_visible()
            assert json.loads(page.locator("#responseEvidenceJson").text_content())["parse"] == taught["parse"]
            expect(page.locator(f'#alarmFeed [data-event-id="{chosen["id"]}"]')).to_have_attribute("data-show", "false")
            page.wait_for_function("window.__earshotAnimations.some(item=>item.id==='versionBadge')")
            authoritative = api("GET", "/console")
            expected_rate = page.evaluate("value=>Number(value).toLocaleString('en-US',{maximumFractionDigits:0})",
                                          authoritative["stats"]["alarms_per_hour_current"])
            expect(page.locator("#rateNumber")).to_have_text(expected_rate)
            evidence["real_text_teaching"] = {"source_event_id": chosen["id"], "alarm_code": event["alarm_code"],
                "turbine_id": event["turbine_id"], "rule_id": rule_id, "parse": taught["parse"],
                "model_version": taught["model_version"], "newly_suppressed_count": taught["newly_suppressed_count"],
                "operator_label_verified_as_nuisance": False}
            page.screenshot(path=str(output / "phase8_console_taught.png"), full_page=True)
            source = api("GET", "/evidence")
            assert source["data_kind"] == "recorded_dataset_replay"
            evidence["evidence_endpoint"] = {"data_kind": source["data_kind"],
                "source_verification_available": bool(source.get("source_verification")),
                "provider_validation_keys": sorted(source.get("provider_validation", {})),
                "recent_operation_count": len(source.get("recent_operations", []))}
            before = page.evaluate("window.__earshotSockets.length")
            page.evaluate("window.__earshotSockets.at(-1).close(1000,'browser verification reconnect')")
            page.wait_for_function("before=>window.__earshotSockets.length>before && window.__earshotSockets.at(-1).readyState===1", arg=before, timeout=15000)
            expect(card).to_be_visible()
            evidence["websocket_reconnected"] = True

            # Mock provider receipt rendering only: no provider call or fake rule.
            mock_receipt = {"parsed": False, "source": "text", "message": "BROWSER CONTRACT MOCK — no provider call or learning.",
                "parse": {"source": "provider", "provider": "nebius", "model": "browser-contract-mock",
                          "request_id": "browser-mock-request", "fallback_reason": None},
                "evidence": {"kind": "browser_contract_mock", "provider_request_occurred": False}}
            page.route("**/teach", lambda route: route.fulfill(status=200, json=mock_receipt), times=1)
            page.locator("#teachText").fill("Browser contract rendering check")
            page.locator("#teachText").press("Enter")
            expect(page.locator("#teachingProvenance")).to_contain_text("Parser: nebius · browser-contract-mock")
            expect(page.locator("#teachingProvenance")).to_contain_text("browser-mock-request")
            assert json.loads(page.locator("#responseEvidenceJson").text_content())["evidence"]["provider_request_occurred"] is False
            evidence["mock_provider_receipt_rendered"] = {"passed": True, "kind": "browser_contract_mock"}

            voice_page = context.new_page()
            track(voice_page)
            voice_page.add_init_script("window.__browserTestUtterance=" + json.dumps(utterance) + ";" + """
              window.__mockEnded=false;
              window.SpeechRecognition=class {
                constructor(){this.processLocally=false;}
                static async available(){return 'available';}
                start(){setTimeout(()=>{const result=[{transcript:window.__browserTestUtterance}];
                  result.isFinal=true;this.onresult({results:[result]});window.__mockEnded=true;this.onend();},50);}
                stop(){this.onend();} abort(){this.onend();}
              };
            """)
            voice_page.goto(origin + "/#console", wait_until="networkidle")
            voice_page.locator("#soundEnabled").uncheck()
            expect(voice_page.locator("#voiceTier")).to_have_text("LOCAL MICROPHONE")
            voice_page.evaluate("document.activeElement?.blur()")
            voice_page.locator("body").click(position={"x": 5, "y": 5})
            held_count = len(teaching_requests)
            voice_page.keyboard.down("Space")
            voice_page.wait_for_function("window.__mockEnded", timeout=5000)
            assert len(teaching_requests) == held_count
            with voice_page.expect_response(lambda response: urlparse(response.url).path == "/teach") as recognition:
                voice_page.keyboard.up("Space")
            recognized = recognition.value.json()
            assert recognized["parsed"] and recognized["parse"]["source"] == "local"
            owned_rule_ids.add(recognized["rule"]["rule_id"])
            evidence["mocked_local_recognition_waits_for_release"] = {"passed": True,
                "kind": "browser_api_mock", "actual_microphone_verified": False}
            voice_page.close()
            expect(card).to_be_visible()
            with page.expect_response(lambda response: urlparse(response.url).path == "/undo/" + rule_id):
                card.get_by_role("button", name="Undo", exact=False).click()
            expect(card).to_have_count(0)
            owned_rule_ids.discard(rule_id)
            evidence["ui_undo"] = True

            # Mock microphone transport failure only: never an actual recording.
            failure_page = context.new_page()
            track(failure_page)
            failure_page.add_init_script("""
              Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}});
              window.MediaRecorder=class extends EventTarget {
                static isTypeSupported(){return true;}
                constructor(){super();this.state='inactive';this.mimeType='audio/webm';}
                start(){this.state='recording';}
                stop(){this.state='inactive';const event=new Event('dataavailable');
                  event.data=new Blob(['browser protocol mock'],{type:'audio/webm'});
                  this.dispatchEvent(event);this.dispatchEvent(new Event('stop'));}
              };
            """)
            failure_page.route("**/voice/status", lambda route: route.fulfill(status=200, json={
                "online": True, "provider_available": True, "live_voice_available": True}))
            failure_page.route("**/listen", lambda route: route.fulfill(status=503, json={
                "ok": False, "error": {"code": "VoiceProviderError", "message":
                "BROWSER CONTRACT MOCK: Enable the speech_to_text permission in the ElevenLabs API-key settings."},
                "evidence": {"kind": "browser_contract_mock", "provider": "elevenlabs", "operation": "speech_to_text",
                "http_status": 401, "request_id": "browser-mock-permission-failure", "success": False,
                "required_permission": "speech_to_text", "provider_request_occurred": False}}), times=1)
            failure_page.goto(origin + "/#console", wait_until="networkidle")
            failure_page.locator("#soundEnabled").uncheck()
            expect(failure_page.locator("#voiceTier")).to_have_text("MICROPHONE · PROVIDER")
            failure_page.evaluate("document.activeElement?.blur()")
            failure_page.locator("body").click(position={"x": 5, "y": 5})
            failure_page.keyboard.down("Space")
            expect(failure_page.locator("#micButton")).to_have_attribute("aria-pressed", "true")
            failure_page.keyboard.up("Space")
            expect(failure_page.locator("#feedback")).to_contain_text("Enable the speech_to_text permission")
            expect(failure_page.locator("#voiceEvidence")).to_be_visible()
            failure_receipt = json.loads(failure_page.locator("#voiceEvidenceJson").text_content())
            assert "browser-mock-permission-failure" in json.dumps(failure_receipt)
            evidence["mocked_provider_failure_display"] = {"passed": True, "kind": "browser_api_mock"}
            failure_page.close()

            # Screenshots contain actual current state, never mocked receipts.
            page.reload(wait_until="networkidle")
            expect(page.locator("#modelVersion")).not_to_have_text("v—")
            page.locator("#soundEnabled").uncheck()
            page.set_viewport_size({"width": 1366, "height": 768})
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path=str(output / "phase8_console_projector.png"), full_page=True)
            boxes = {name: page.locator("#" + name).bounding_box() for name in ("linkPanel", "versionBadge", "ruleLedger", "teachText")}
            evidence["projector_boxes"] = boxes
            for name in ("linkPanel", "versionBadge", "teachText"):
                assert boxes[name] and boxes[name]["y"] + boxes[name]["height"] <= 768, name
            assert boxes["ruleLedger"]["y"] + min(boxes["ruleLedger"]["height"], 100) <= 768
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(output / "phase8_console_mobile.png"), full_page=True)
            evidence["mobile_overflow"] = page.evaluate("document.documentElement.scrollWidth > innerWidth")
            assert not evidence["mobile_overflow"]
            assert page.locator("#alarmFeed .event").count() <= 200
            external = [url for url in requests if urlparse(url).scheme in ("http", "https", "ws", "wss")
                        and urlparse(url).hostname not in ("127.0.0.1", "localhost")]
            assert not external, external
            assert not errors, errors
            evidence.update(success=True, page_errors=errors, external_browser_requests=external,
                            request_count=len(requests), browser=browser.version)
        finally:
            # These IDs came exclusively from our /teach or /listen page receipts.
            for rule_id in sorted(owned_rule_ids):
                try:
                    response = context.request.post(origin + "/undo/" + rule_id)
                    if response.status not in (200, 404):
                        cleanup_errors.append(f"Owned rule {rule_id}: HTTP {response.status}")
                except Exception as error:
                    cleanup_errors.append("Owned-rule cleanup: " + type(error).__name__)
            for path, data in (("/killswitch", {"on": not prior_online}),
                               ("/replay", {"action": "pause" if prior_paused else "resume"})):
                try:
                    api("POST", path, data)
                except Exception as error:
                    cleanup_errors.append(path + " restoration: " + type(error).__name__)
            evidence["cleanup_errors"] = cleanup_errors
            evidence["restoration"] = {"online": prior_online, "paused": prior_paused,
                "seek_performed": False, "note": "Source time advances during playback; the replay buffer is preserved."}
            if cleanup_errors:
                evidence["success"] = False
            browser.close()
            (output / "phase8_browser_validation.json").write_text(json.dumps(evidence, indent=2) + "\n")
        assert not cleanup_errors, cleanup_errors
        print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
