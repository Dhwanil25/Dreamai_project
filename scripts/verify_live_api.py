"""Verify a running local Phase 7 server, then undo only this script's rules.

Start uvicorn separately. This script applies two temporary demo corrections,
restores the prior offline setting, and saves full local evidence under data/.
The append-only audit history and model revision will retain these operations.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from pathlib import Path
import time

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"


async def main() -> None:
    report = {"responses": [], "five_events": [], "notifications": {"first": [], "second": []}}
    learned_ids = []
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as http:
        async def request(method, path, payload=None):
            response = await http.request(method, path, json=payload)
            body = response.json()
            report["responses"].append({"method": method, "path": path, "status": response.status_code, "body": body})
            response.raise_for_status()
            return body

        initial = await request("GET", "/health")
        assert initial["ok"], initial
        initial_offline = not initial["online"]
        report["initial_health"] = initial
        initial_rules = await request("GET", "/rules")
        baseline = await request("GET", "/stats")
        assert "baseline" in baseline and "rates" in baseline["baseline"]
        messages = []
        five_ready = asyncio.Event()
        event_count = 0
        stats_count = 0
        observations = {"alarm": 0, "sensor": 0}
        started = time.perf_counter()
        try:
            async with websockets.connect(BASE.replace("http", "ws") + "/stream", max_size=2**22) as first, websockets.connect(BASE.replace("http", "ws") + "/stream", max_size=2**22) as second:
                async def receive(socket, name):
                    nonlocal event_count, stats_count
                    async for raw in socket:
                        message = json.loads(raw)
                        kind = message.get("type")
                        if name == "first" and kind == "event":
                            event_count += 1
                            category = "alarm" if message["event"]["alarm_code"] is not None else "sensor"
                            observations[category] += 1
                            messages.append(message)
                            if len(report["five_events"]) < 5:
                                report["five_events"].append(message)
                            if len(report["five_events"]) == 5:
                                five_ready.set()
                        elif name == "first" and kind == "stats":
                            stats_count += 1
                        if kind in {"taught", "undo", "link", "error"}:
                            report["notifications"][name].append(message)

                consumers = [asyncio.create_task(receive(first, "first")), asyncio.create_task(receive(second, "second"))]
                try:
                    await asyncio.wait_for(five_ready.wait(), timeout=10)
                    await request("POST", "/killswitch", {"on": False})
                    before = await request("GET", "/health")
                    taught = await request("POST", "/teach", {"text": "ignore the cable untwist alarm on turbine four"})
                    assert taught["parsed"] and taught["model_version_before"] == before["model_version"]
                    assert taught["model_version"] == before["model_version"] + 1
                    learned_ids.append(taught["rule_id"])
                    report["cable_teach"] = taught
                    assert taught["rule"] in await request("GET", "/rules")
                    link = await request("POST", "/killswitch", {"on": True})
                    assert link == {"online": False, "offline": True}
                    offline = await request("POST", "/teach", {"text": "generator cut-in is routine, suppress it"})
                    assert offline["parsed"] and offline["rule"]["confidence"] == 0.6
                    assert offline["model_version"] == taught["model_version"] + 1
                    learned_ids.append(offline["rule_id"])
                    report["offline_teach"] = offline
                    assert await request("POST", "/teach", {"text": "what is the weather like"}) == {"parsed": False, "message": "no rule found in that"}
                    # Observe a measured ten-second window, with concurrent HTTP
                    # actions included in the actual operator-session throughput.
                    await asyncio.sleep(max(0, 10 - (time.perf_counter() - started)))
                    duration = time.perf_counter() - started
                    report["measurement"] = {"wall_seconds": duration, "events_received": event_count,
                        "events_per_second": event_count / duration, "event_kinds": observations,
                        "stats_messages_received": stats_count, "configured_replay_speed": 600}
                    for name in ("first", "second"):
                        seen = {item.get("rule_id") for item in report["notifications"][name] if item["type"] == "taught"}
                        assert set(learned_ids) <= seen, (name, seen)
                        assert any(item["type"] == "link" and item["online"] is False for item in report["notifications"][name])
                    report["both_clients_received_teaching_and_offline_updates"] = True
                    report["subsequent_cut_in_events_suppressed"] = sum(
                        item["event"]["alarm_code"] == 20 and not item["verdict"]["show"]
                        and item["model_version"] >= offline["model_version"] for item in messages)
                    report["health_after_teaching"] = await request("GET", "/health")
                    report["stats_after_teaching"] = await request("GET", "/stats")
                finally:
                    for consumer in consumers:
                        consumer.cancel()
                    await asyncio.gather(*consumers, return_exceptions=True)
        finally:
            for rule_id in reversed(learned_ids):
                await request("POST", "/undo/" + rule_id)
            await request("POST", "/killswitch", {"on": initial_offline})
            report["cleanup_health"] = await request("GET", "/health")
            assert await request("GET", "/rules") == initial_rules
            report["initial_rules_restored"] = True
            report["http_errors"] = [row for row in report["responses"] if row["status"] >= 400]
            path = ROOT / "data/processed/phase7_api_validation.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"initial_health": initial, "cable_teach": report["cable_teach"],
            "offline_teach": report["offline_teach"], "measurement": report["measurement"],
            "both_clients_received_updates": report["both_clients_received_teaching_and_offline_updates"],
            "subsequent_cut_in_events_suppressed": report["subsequent_cut_in_events_suppressed"],
            "cleanup_health": report["cleanup_health"], "http_errors": report["http_errors"]}, indent=2))
        print("Full local evidence: data/processed/phase7_api_validation.json")


if __name__ == "__main__":
    asyncio.run(main())
