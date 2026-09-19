"""Phase 6 scaffold serving a home page, health and unavailable live APIs.

Offline ingestion, detection, local teaching and text parsing are available as
library capabilities. Replay and voice remain unimplemented; live APIs await Phase 7.
Interactive API docs are disabled to avoid external CDN assets;
/openapi.json exposes the contracts.
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from earshot.config import PROJECT_ROOT

app = FastAPI(
    title="EARSHOT",
    description=(
        "Phase 6 text teaching parser: offline ingestion, detection, teaching and "
        "text parsing are available as library capabilities. "
        "The scaffold serves home and health; live operations return 501 until implemented."
    ),
    version="0.0.1",
    docs_url=None,
    redoc_url=None,
)

UNAVAILABLE_RESPONSE = {
    501: {"description": "This feature has not been implemented in the current scaffold."}
}


def _unavailable_details(feature: str) -> dict[str, Any]:
    """Describe an unfinished feature without inventing data or changing state."""
    return {
        "code": "not_implemented",
        "feature": feature,
        "message": f"The {feature} API is not available in the Phase 6 scaffold; it is planned for Phase 7.",
        "available_in_phase": 7,
    }


def _unavailable(feature: str) -> NoReturn:
    """Return an intentional HTTP 501 response for an unfinished operation."""
    raise HTTPException(status_code=501, detail=_unavailable_details(feature))


class TeachRequest(BaseModel):
    """Operator transcript supplied to the future teaching endpoint."""

    text: str


class KillswitchRequest(BaseModel):
    """Requested offline mode for the future outbound-call boundary."""

    on: bool


@app.get("/", response_class=HTMLResponse)
async def index() -> FileResponse:
    """Serve the local scaffold page, independently of the working directory."""
    return FileResponse(PROJECT_ROOT / "ui" / "index.html", media_type="text/html")


@app.get("/stats", responses=UNAVAILABLE_RESPONSE)
async def stats() -> dict[str, Any]:
    """Return replay/policy and baseline statistics without changing state."""
    _unavailable("statistics")


@app.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    """Explain that replay is unavailable, then close the socket normally."""
    await websocket.accept()
    await websocket.send_json({"type": "error", **_unavailable_details("replay")})
    await websocket.close(code=1000, reason="Replay has not been implemented")


@app.post("/teach", responses=UNAVAILABLE_RESPONSE)
async def teach(request: TeachRequest) -> dict[str, Any]:
    """Parse text, learn and rescore; later persist corrections and broadcast changes."""
    _unavailable("teaching")


@app.post("/undo/{rule_id}", responses=UNAVAILABLE_RESPONSE)
async def undo(rule_id: str) -> dict[str, Any]:
    """Revoke an identified correction; later persist and broadcast revised verdicts."""
    _unavailable("undo")


@app.post("/killswitch", responses=UNAVAILABLE_RESPONSE)
async def killswitch(request: KillswitchRequest) -> dict[str, Any]:
    """Set offline mode and return its status; later block outbound integrations."""
    _unavailable("offline_controls")


@app.get("/rules", responses=UNAVAILABLE_RESPONSE)
async def rules() -> list[dict[str, Any]]:
    """Return the active local correction ledger without changing its contents."""
    _unavailable("rules")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Report liveness and library capabilities, independently of live API readiness."""
    return {
        "ok": True,
        "status": "scaffold",
        "phase": 6,
        "features": {
            "ingestion": True,
            "replay": False,
            "detection": True,
            "teaching": True,
            "parsing": True,
            "voice": False,
        },
    }
