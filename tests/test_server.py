"""Regression checks for an honest, usable Phase 5 server scaffold."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from earshot.config import PROJECT_ROOT
from earshot.server import app


@pytest.fixture
def client():
    # Keep TestClient's default exception propagation: unexpected route errors
    # must fail these tests rather than merely becoming an HTTP 500 response.
    with TestClient(app) as test_client:
        yield test_client


def test_root_serves_local_ui_independently_of_launch_directory(
    client, tmp_path, monkeypatch
):
    alternate_ui = tmp_path / "ui"
    alternate_ui.mkdir()
    (alternate_ui / "index.html").write_text("Incorrect launch-directory UI")
    monkeypatch.chdir(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == (PROJECT_ROOT / "ui" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "EARSHOT" in response.text
    assert "Phase 5 local teaching engine" in response.text
    assert "tests/test_policy.py" in response.text
    assert "docs/PHASE_5_REPORT.md" in response.text
    assert "scripts/compute_baseline.py" in response.text
    assert "data/processed/baseline_report.md" in response.text
    assert "demo/baseline_stats.json" in response.text


def test_health_reports_library_capabilities_without_claiming_live_apis_are_ready(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "scaffold",
        "phase": 5,
        "features": {
            "ingestion": True,
            "replay": False,
            "detection": True,
            "teaching": True,
            "voice": False,
        },
    }


@pytest.mark.parametrize(
    ("method", "path", "payload", "feature"),
    [
        ("GET", "/stats", None, "statistics"),
        ("GET", "/rules", None, "rules"),
        ("POST", "/teach", {"text": "That alarm is a morning startup."}, "teaching"),
        ("POST", "/undo/example-rule", None, "undo"),
        ("POST", "/killswitch", {"on": True}, "offline_controls"),
    ],
)
def test_unimplemented_features_return_structured_501(
    client, method, path, payload, feature
):
    response = client.request(method, path, json=payload)

    assert response.status_code == 501
    detail = response.json()["detail"]
    assert detail["code"] == "not_implemented"
    assert detail["available_in_phase"] == 7
    assert detail["feature"] == feature
    assert isinstance(detail["message"], str) and detail["message"].strip()
    assert "Phase 5 scaffold" in detail["message"]


def test_replay_socket_explains_unavailability_and_closes_cleanly(client):
    with client.websocket_connect("/stream") as websocket:
        message = websocket.receive_json()

        assert message["type"] == "error"
        assert message["code"] == "not_implemented"
        assert message["feature"] == "replay"
        assert message["available_in_phase"] == 7
        assert isinstance(message["message"], str) and message["message"].strip()
        assert "Phase 5 scaffold" in message["message"]
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 1000


def test_openapi_contract_remains_available_locally(client):
    response = client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    assert document["info"]["title"] == "EARSHOT"
    assert document["openapi"].startswith("3.")
    assert {"/", "/health", "/stats", "/rules", "/teach", "/undo/{rule_id}", "/killswitch"}.issubset(
        document["paths"]
    )


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_cdn_backed_documentation_pages_remain_disabled(client, path):
    assert client.get(path).status_code == 404
