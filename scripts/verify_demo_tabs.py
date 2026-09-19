"""Read-only Chrome checks for EARSHOT's six presentation tabs.

Navigation may read the local backend but must never teach, record audio, change
replay settings, or request provider output. Mutation attempts are blocked and
reported as failures. Requires optional Playwright and installed Chrome.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import expect, sync_playwright

from earshot.config import CONFIG


TABS = ("overview", "console", "learning", "market", "architecture", "proof")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    origin = parser.parse_args().url.rstrip("/")
    assert urlparse(origin).hostname in ("127.0.0.1", "localhost"), "A local server is required"
    output = CONFIG.data.processed_dir
    output.mkdir(parents=True, exist_ok=True)
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "success": False,
              "read_only": True, "provider_success_verified": False, "screenshots": []}
    page_errors, requests, blocked = [], [], []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 768}, locale="en-US")

        def guard(route) -> None:
            request = route.request
            url = urlparse(request.url)
            mutation = request.method not in ("GET", "HEAD", "OPTIONS")
            provider_output = url.path == "/speak"
            external = url.scheme in ("http", "https", "ws", "wss") and url.hostname not in ("127.0.0.1", "localhost")
            if mutation or provider_output or external:
                blocked.append({"method": request.method, "path": url.path,
                                "reason": "external" if external else "provider_output" if provider_output else "mutation"})
                route.abort("blockedbyclient")
            else:
                route.continue_()

        context.route("**/*", guard)
        context.on("request", lambda request: requests.append({"method": request.method, "url": request.url}))
        context.add_init_script("""
          window.__tabChecks={sockets:[],microphoneStarts:0,recognitionStarts:0};
          const NativeSocket=window.WebSocket;
          window.WebSocket=class extends NativeSocket {
            constructor(...args){super(...args);window.__tabChecks.sockets.push(this);}
          };
          if(navigator.mediaDevices) navigator.mediaDevices.getUserMedia=async()=>{
            window.__tabChecks.microphoneStarts++;
            throw new DOMException('Read-only browser verification blocks capture','NotAllowedError');
          };
          for(const Recognition of [window.SpeechRecognition,window.webkitSpeechRecognition]) {
            if(Recognition?.prototype?.start) Recognition.prototype.start=function(){
              window.__tabChecks.recognitionStarts++;
              throw new DOMException('Read-only browser verification blocks recognition','NotAllowedError');
            };
          }
        """)
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("websocket", lambda socket: requests.append({"method": "WEBSOCKET", "url": socket.url}))

        def read(path: str) -> dict:
            response = context.request.get(origin + path)
            assert response.ok, (path, response.status)
            return response.json()

        def selected(name: str, *, hash_required: bool = True) -> None:
            tab = page.locator("#tab-" + name)
            expect(tab).to_have_attribute("role", "tab")
            expect(tab).to_have_attribute("aria-selected", "true")
            expect(tab).to_have_attribute("aria-controls", "panel-" + name)
            expect(tab).to_have_attribute("tabindex", "0")
            expect(page.locator("#panel-" + name)).to_be_visible()
            expect(page.locator("#modelVersion")).to_be_visible()
            expect(page.locator("#linkPanel")).to_be_visible()
            for other in TABS:
                if other != name:
                    expect(page.locator("#tab-" + other)).to_have_attribute("aria-selected", "false")
                    expect(page.locator("#tab-" + other)).to_have_attribute("tabindex", "-1")
                    expect(page.locator("#panel-" + other)).to_be_hidden()
            if hash_required:
                expect(page).to_have_url(origin + "/#" + name)

        def click_tab(name: str) -> None:
            page.locator("#tab-" + name).click()
            selected(name)

        try:
            report["before"] = read("/health")
            page.goto(origin + "/", wait_until="networkidle")
            selected("overview", hash_required=False)
            assert page.evaluate("location.hash") in ("", "#overview")
            expect(page.locator('[role="tab"]')).to_have_count(6)
            expect(page.locator("#modelVersion")).not_to_have_text("v—")
            page.wait_for_function("window.__tabChecks.sockets.some(socket=>socket.readyState===1)", timeout=10000)
            report["default_overview"] = True
            baseline = read("/stats")["baseline"]
            overview_counts = {"overviewRecords": baseline["counts"]["record_count"],
                               "overviewTurbines": baseline["counts"]["turbine_count"]}
            for identifier, count in overview_counts.items():
                expect(page.locator("#" + identifier)).to_have_text(f"{count:,}")
            pareto = page.evaluate("value=>Number(value).toLocaleString('en-US',{maximumFractionDigits:1})+'%'",
                                   baseline["pareto"]["top10_share_pct"])
            expect(page.locator("#overviewPareto")).to_have_text(pareto)
            report["overview_matches_baseline"] = {**overview_counts, "overviewPareto": pareto}

            click_tab("console")
            expect(page.locator("#connectionText")).to_contain_text("connected")
            expect(page.locator("#alarmFeed .event").first).to_be_visible()
            expect(page.locator("#rateNumber")).not_to_have_text("—")

            # An unavailable date must be rejected by native input validation
            # before any replay request. A paused stream makes unchanged-row
            # assertions independent of normal playback advancement.
            range_before = read("/stats")["replay"]
            assert range_before["paused"], "Run read-only seek validation with the replay already paused"
            start, end = range_before["source_start"], range_before["source_end"]
            assert start and end, "The backend must expose the actual recording bounds"
            timestamp = page.locator("#replayTimestamp")
            expect(timestamp).to_have_attribute("min", start)
            expect(timestamp).to_have_attribute("max", end)
            expect(page.locator("#replayNote")).to_contain_text(start.replace("T", " "))
            expect(page.locator("#replayNote")).to_contain_text(end.replace("T", " "))
            previous_timestamp = timestamp.input_value()
            previous_edited = timestamp.get_attribute("data-edited")
            rows_before = page.locator("#alarmFeed .event").evaluate_all("rows=>rows.map(row=>row.dataset.eventId)")
            request_offset = len(requests)
            range_check = {"source_start": start, "source_end": end,
                           "invalid_timestamp": "2026-09-01T06:40:00", "generation_before": range_before["generation"]}
            report["replay_input_bounds"] = range_check
            try:
                # Chrome normalizes zero seconds out of datetime-local values.
                timestamp.fill(range_check["invalid_timestamp"].removesuffix(":00"))
                page.locator("#seekReplay").click()
                page.wait_for_timeout(250)
                validity = timestamp.evaluate("input=>({valid:input.validity.valid,rangeOverflow:input.validity.rangeOverflow,message:input.validationMessage})")
                range_check["native_validity"] = validity
                assert not validity["valid"] and validity["rangeOverflow"] and validity["message"]
                attempted = [request for request in requests[request_offset:]
                             if request["method"] == "POST" and urlparse(request["url"]).path == "/replay"]
                assert not attempted, "Out-of-range input attempted a /replay POST"
                range_after = read("/stats")["replay"]
                rows_after = page.locator("#alarmFeed .event").evaluate_all("rows=>rows.map(row=>row.dataset.eventId)")
                assert range_after["generation"] == range_before["generation"]
                assert range_after["sequence"] == range_before["sequence"]
                assert range_after["position"] == range_before["position"]
                assert rows_after == rows_before
                range_check.update(no_replay_post=True, rows_unchanged=True, generation_unchanged=True)
            finally:
                timestamp.fill(previous_timestamp)
                timestamp.evaluate("(input,previous)=>{if(previous===null)delete input.dataset.edited;else input.dataset.edited=previous;}", previous_edited)
                range_check["field_restored"] = timestamp.input_value() == previous_timestamp
            assert range_check["field_restored"]

            page.locator("#teachText").fill("Unsaved browser navigation check — do not submit")
            page.evaluate("window.__tabChecks.feed=document.getElementById('alarmFeed');window.__tabChecks.socketCount=window.__tabChecks.sockets.length")
            initial_ids = page.locator("#alarmFeed .event").evaluate_all("rows=>rows.map(row=>row.dataset.eventId)")
            before_stream = read("/stats")["replay"]
            report["initial_alarm_rows"] = len(initial_ids)

            for name in TABS:
                click_tab(name)
            expect(page.locator("#demoSourceStatus")).to_be_visible()
            expect(page.locator("#demoModelHash")).to_be_visible()
            page.wait_for_function("""()=>{
              const value=document.getElementById('demoModelHash').textContent;
              return /[a-f0-9]{12}/i.test(value);
            }""", timeout=10000)
            actual_evidence = read("/evidence")
            actual_hash = actual_evidence["current_learning_state"]["classifier_state_sha256"]
            displayed_hash = page.locator("#demoModelHash").inner_text()
            assert actual_hash[:12] in displayed_hash, (displayed_hash, actual_hash)
            source_status = page.locator("#demoSourceStatus").inner_text().strip()
            assert source_status and source_status not in ("—", "Loading…", "Waiting")
            report["dynamic_proof"] = {"source_status": source_status, "displayed_model_hash": displayed_hash,
                                       "matches_backend_model_hash": True}
            click_tab("market")
            expect(page.locator("#panel-market")).to_contain_text("DOM")
            assert "hypothesis" in page.locator("#panel-market").inner_text().lower()

            def market_values(sam: str, arr: str, winning: str, reachable: str) -> None:
                for identifier, expected in (("marketModelSam", sam), ("marketModelArr", arr),
                                             ("marketWinningSites", winning), ("marketReachableSites", reachable)):
                    expect(page.locator("#" + identifier)).to_have_text(expected)
                expect(page.locator("#marketModelError")).to_have_text("")

            # Independently calculated expectations test the planning arithmetic,
            # not the validity of its market-size, price, or adoption assumptions.
            calculator = {"assumptions_are_not_market_validation": True, "valid_scenarios": [],
                          "invalid_inputs_checked": [], "reset_restores_defaults": False}
            report["market_calculator"] = calculator
            expect(page.locator("#marketProjectSize")).to_have_value("100")
            expect(page.locator("#marketAnnualPrice")).to_have_value("40000")
            expect(page.locator("#marketPenetration")).to_have_value("2")
            market_values("$519.6M", "$3.32M", "83", "4,150")
            calculator["valid_scenarios"].append({"case": "default", "sam": 519600000, "arr": 3320000,
                                                  "winning_sites": 83, "reachable_sites": 4150})
            page.locator("#marketAnnualPrice").fill("60000")
            market_values("$779.4M", "$4.98M", "83", "4,150")
            calculator["valid_scenarios"].append({"case": "annual_price_60000", "sam": 779400000,
                                                  "arr": 4980000, "winning_sites": 83})
            page.locator("#marketProjectSize").fill("200")
            # 2% of 2,825 sites is 56.5, which rounds to 57 whole customers.
            market_values("$389.7M", "$3.42M", "57", "2,825")
            calculator["valid_scenarios"].append({"case": "project_size_200", "sam": 389700000,
                                                  "arr": 3420000, "winning_sites": 57, "reachable_sites": 2825})
            page.locator("#marketPenetration").fill("5")
            market_values("$389.7M", "$8.46M", "141", "2,825")
            calculator["valid_scenarios"].append({"case": "adoption_5_percent", "sam": 389700000,
                                                  "arr": 8460000, "winning_sites": 141})
            page.locator("#marketPenetration").fill("0")
            market_values("$389.7M", "$0M", "0", "2,825")
            calculator["valid_scenarios"].append({"case": "zero_adoption", "sam": 389700000,
                                                  "arr": 0, "winning_sites": 0})
            for identifier, value, reason in (("marketAnnualPrice", "", "empty_price"),
                                               ("marketProjectSize", "0", "nonpositive_project_size"),
                                               ("marketPenetration", "101", "adoption_above_range"),
                                               ("marketPenetration", "2.1", "adoption_step_mismatch")):
                page.locator("#resetMarketModel").click()
                page.locator("#" + identifier).fill(value)
                for output_id in ("marketModelSam", "marketModelArr", "marketWinningSites", "marketReachableSites"):
                    expect(page.locator("#" + output_id)).to_have_text("—")
                expect(page.locator("#marketModelError")).not_to_have_text("")
                calculator["invalid_inputs_checked"].append(reason)
            page.locator("#resetMarketModel").click()
            expect(page.locator("#marketProjectSize")).to_have_value("100")
            expect(page.locator("#marketAnnualPrice")).to_have_value("40000")
            expect(page.locator("#marketPenetration")).to_have_value("2")
            market_values("$519.6M", "$3.32M", "83", "4,150")
            calculator["reset_restores_defaults"] = True

            click_tab("architecture")
            nodes = page.locator('#panel-architecture button[data-arch-node]')
            assert nodes.count() >= 2
            for node in nodes.all():
                node.click()
            report["architecture_nodes_checked"] = nodes.count()

            # Tab controls must consume Space; the global microphone shortcut
            # must not run even while the console remains mounted in the DOM.
            for name in TABS:
                tab = page.locator("#tab-" + name)
                tab.focus()
                tab.press("Space")
                selected(name)
            page.locator("#tab-overview").focus()
            page.keyboard.press("ArrowRight")
            expect(page.locator("#tab-console")).to_be_focused()
            selected("console")
            page.keyboard.press("ArrowLeft")
            expect(page.locator("#tab-overview")).to_be_focused()
            selected("overview")
            page.keyboard.press("End")
            expect(page.locator("#tab-proof")).to_be_focused()
            selected("proof")
            page.keyboard.press("Home")
            expect(page.locator("#tab-overview")).to_be_focused()
            selected("overview")
            page.keyboard.press("ArrowLeft")
            selected("proof")
            page.keyboard.press("ArrowRight")
            selected("overview")
            report["keyboard_tabs"] = True

            click_tab("console")
            click_tab("learning")
            page.evaluate("history.back()")
            selected("console")
            page.evaluate("history.forward()")
            selected("learning")
            page.evaluate("location.hash='architecture'")
            selected("architecture")
            report["hash_back_forward"] = True
            click_tab("console")
            expect(page.locator("#teachText")).to_have_value("Unsaved browser navigation check — do not submit")
            assert page.evaluate("document.getElementById('alarmFeed')===window.__tabChecks.feed")
            assert page.evaluate("window.__tabChecks.sockets.length===window.__tabChecks.socketCount")
            assert page.evaluate("window.__tabChecks.sockets.at(-1).readyState===1")
            current_ids = page.locator("#alarmFeed .event").evaluate_all("rows=>rows.map(row=>row.dataset.eventId)")
            after_stream = read("/stats")["replay"]
            assert current_ids, "Tab navigation emptied the recorded alarm feed"
            if (before_stream["generation"], before_stream["sequence"]) == (after_stream["generation"], after_stream["sequence"]):
                assert current_ids == initial_ids, "Unchanged replay lost its existing alarm rows"
            report["console_retained"] = {"same_feed_node": True, "same_socket": True,
                "draft_retained": True, "alarm_rows": len(current_ids),
                "original_ids_still_present": len(set(initial_ids) & set(current_ids))}

            for size, width, height in (("projector", 1366, 768), ("mobile", 390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                for name in TABS:
                    click_tab(name)
                    page.evaluate("window.scrollTo(0,0)")
                    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth+1"), (name, size)
                    path = output / f"demo_tab_{name}_{size}.png"
                    page.screenshot(path=str(path), full_page=True)
                    report["screenshots"].append(path.name)
            report["capture_attempts"] = page.evaluate("({microphone:window.__tabChecks.microphoneStarts,recognition:window.__tabChecks.recognitionStarts})")
            assert report["capture_attempts"] == {"microphone": 0, "recognition": 0}
            # Test a direct bookmarked hash as well as in-document switching.
            page.goto(origin + "/#proof", wait_until="networkidle")
            selected("proof")
            report["direct_hash"] = True
            assert page.evaluate("window.__tabChecks.microphoneStarts===0 && window.__tabChecks.recognitionStarts===0")
            assert not blocked, blocked
            assert not page_errors, page_errors
            report["after"] = read("/health")
            report.update(success=True, browser=browser.version, request_count=len(requests))
        except Exception as error:
            report["failure"] = type(error).__name__ + ": " + str(error)
            raise
        finally:
            report["blocked_requests"] = blocked
            report["page_errors"] = page_errors
            report["external_page_requests"] = [request for request in requests
                if urlparse(request["url"]).scheme in ("http", "https", "ws", "wss")
                and urlparse(request["url"]).hostname not in ("127.0.0.1", "localhost")]
            browser.close()
            (output / "demo_tabs_browser_validation.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
