"""Importable FastAPI application with Phase 7 route contracts only.

Start the process to inspect /openapi.json. Business routes deliberately raise
NotImplementedError until their implementation phase; no replay or model starts
at import time. Interactive API docs are disabled to avoid external CDN assets.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(
    title="EARSHOT",
    description="Phase 2 scaffold: route contracts only; business routes are not implemented.",
    version="0.0.0",
    docs_url=None,
    redoc_url=None,
)


class TeachRequest(BaseModel):
    """Operator transcript supplied to the future teaching endpoint."""

    text: str


class KillswitchRequest(BaseModel):
    """Requested offline mode for the future outbound-call boundary."""

    on: bool


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Return the local operator-console HTML; later reads ui/index.html."""
    raise NotImplementedError("Console serving is implemented in Phase 7")


@app.get("/stats")
async def stats() -> dict[str, Any]:
    """Return replay/policy and baseline statistics without changing state."""
    raise NotImplementedError("Statistics serving is implemented in Phase 7")


@app.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    """Accept a local socket and send replay events/stats; later tracks clients."""
    raise NotImplementedError("WebSocket replay is implemented in Phase 7")


@app.post("/teach")
async def teach(request: TeachRequest) -> dict[str, Any]:
    """Parse text, learn and rescore; later persist corrections and broadcast changes."""
    raise NotImplementedError("Operator teaching is implemented in Phase 7")


@app.post("/undo/{rule_id}")
async def undo(rule_id: str) -> dict[str, Any]:
    """Revoke an identified correction; later persist and broadcast revised verdicts."""
    raise NotImplementedError("Correction undo is implemented in Phase 7")


@app.post("/killswitch")
async def killswitch(request: KillswitchRequest) -> dict[str, Any]:
    """Set offline mode and return its status; later block outbound integrations."""
    raise NotImplementedError("Runtime offline enforcement is implemented in Phase 7")


@app.get("/rules")
async def rules() -> list[dict[str, Any]]:
    """Return the active local correction ledger without changing its contents."""
    raise NotImplementedError("Rule serving is implemented in Phase 7")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Return readiness, replay position and model version without side effects."""
    raise NotImplementedError("Application health is implemented in Phase 7")
