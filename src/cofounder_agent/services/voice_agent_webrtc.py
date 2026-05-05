"""voice_agent_webrtc.py — WebRTC surface for the Poindexter voice agent.

Same Emma pipeline as ``services.voice_agent`` (Whisper → Ollama → Kokoro),
but the mic/speaker pair is replaced with a browser-side WebRTC peer so
the agent is reachable from a phone or laptop over Tailscale.

## Architecture

::

    Browser (mic) ──WebRTC──▶  SmallWebRTCInputTransport ─┐
                                                          ├─▶ Pipeline
    Browser (audio) ◀─WebRTC── SmallWebRTCOutputTransport ┘

Pipecat's ``SmallWebRTCRequestHandler`` handles SDP exchange + ICE.
Each connecting client spawns its own pipeline task — connections are
isolated, but they share the process-wide DB pool + SiteConfig.

## Run

    python -m services.voice_agent_webrtc

Then on the same Tailscale tailnet open ``http://<host>:8003`` in any
browser. Click "Connect" — the prebuilt UI handles mic permission +
SDP negotiation.

Bind host/port live in ``app_settings`` (``voice_agent_webrtc_host``,
``voice_agent_webrtc_port``). Defaults: 0.0.0.0:8003.

## Auth

For now the only protection is Tailscale (only your tailnet devices
can reach the bind port). If you ever expose this on the open internet,
add a token gate to the ``/api/offer`` handler.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import RedirectResponse

from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat_ai_small_webrtc_prebuilt.frontend import SmallWebRTCPrebuiltUI

from services.voice_agent import (
    _ensure_brain_on_path,
    build_voice_pipeline_task,
)


_LOG_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


# ---------------------------------------------------------------------------
# Per-connection runner
# ---------------------------------------------------------------------------


async def _run_pipeline_for_connection(
    connection: SmallWebRTCConnection,
    site_config: Any,
    log: logging.Logger,
) -> None:
    """Build a Pipecat pipeline bound to ``connection`` and run it until
    the client disconnects. One of these runs per active client.
    """
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def _on_connected(_t: Any, _c: Any) -> None:
        log.info("WebRTC client connected: pc_id=%s", connection.pc_id)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_t: Any, _c: Any) -> None:
        log.info("WebRTC client disconnected: pc_id=%s", connection.pc_id)

    task = build_voice_pipeline_task(transport, site_config, log=log)
    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    except Exception:  # noqa: BLE001 — log + swallow so one bad connection doesn't kill the server
        log.exception("Pipeline crashed for pc_id=%s", connection.pc_id)


# ---------------------------------------------------------------------------
# FastAPI app + lifespan
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    log = logging.getLogger("voice_agent_webrtc")

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN001 — FastAPI signature
        import asyncpg

        _ensure_brain_on_path()
        from brain.bootstrap import require_database_url
        from services.site_config import SiteConfig

        dsn = require_database_url(source="voice_agent_webrtc")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        site_config = SiteConfig()
        await site_config.load(pool)

        handler = SmallWebRTCRequestHandler()

        app.state.pool = pool
        app.state.site_config = site_config
        app.state.handler = handler
        app.state.log = log
        # In-memory session store for the RTVI handshake. Pipecat's
        # prebuilt UI hits POST /start to get a session id, then routes
        # all subsequent traffic through /sessions/{id}/... — that's
        # the Pipecat-Cloud convention this server has to mimic.
        app.state.sessions = {}

        log.info(
            "Voice WebRTC service ready — open http://%s:%s in a browser "
            "on your tailnet.",
            site_config.get("voice_agent_webrtc_host", "0.0.0.0"),
            site_config.get("voice_agent_webrtc_port", "8003"),
        )

        try:
            yield
        finally:
            await handler.close()
            await pool.close()
            log.info("Voice WebRTC service shut down.")

    app = FastAPI(lifespan=lifespan, title="Poindexter Voice (WebRTC)")

    # Pipecat ships a prebuilt mic-button UI. Mount it at /client and
    # redirect / → /client/ so users can just hit the bare host:port.
    app.mount("/client", SmallWebRTCPrebuiltUI)

    @app.get("/", include_in_schema=False)
    async def _root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/client/")

    @app.get("/healthz")
    async def _healthz() -> dict[str, str]:
        return {"status": "ok", "service": "voice_agent_webrtc"}

    @app.post("/api/offer")
    async def _offer(
        request: SmallWebRTCRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str]:
        site_config = app.state.site_config
        handler: SmallWebRTCRequestHandler = app.state.handler

        async def _on_new_connection(connection: SmallWebRTCConnection) -> None:
            background_tasks.add_task(
                _run_pipeline_for_connection, connection, site_config, log,
            )

        answer = await handler.handle_web_request(
            request=request,
            webrtc_connection_callback=_on_new_connection,
        )
        return answer or {}

    @app.patch("/api/offer")
    async def _ice_candidate(request: SmallWebRTCPatchRequest) -> dict[str, str]:
        handler: SmallWebRTCRequestHandler = app.state.handler
        await handler.handle_patch_request(request)
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # RTVI / Pipecat-Cloud-compatible handshake
    #
    # The prebuilt UI is an RTVI client — it expects a Pipecat-Cloud-shaped
    # API: POST /start returns a sessionId, then everything else is routed
    # under /sessions/{sessionId}/... and proxied back to the real handlers.
    # Without these two routes the UI fails at "authenticating" with a 404.
    # ------------------------------------------------------------------

    @app.post("/start")
    async def _rtvi_start(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — prebuilt UI sometimes sends an empty body
            body = {}

        session_id = str(uuid.uuid4())
        app.state.sessions[session_id] = body.get("body", {})

        result: dict[str, Any] = {"sessionId": session_id}
        if body.get("enableDefaultIceServers"):
            # Tailscale links are direct so a public STUN is rarely needed,
            # but offer one if the client asks. Google's is fine for dev.
            result["iceConfig"] = {
                "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
            }
        return result

    @app.api_route(
        "/sessions/{session_id}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def _session_proxy(
        session_id: str,
        path: str,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> Any:
        active_session = app.state.sessions.get(session_id)
        if active_session is None:
            return Response(
                content="Invalid or not-yet-ready session_id",
                status_code=404,
            )

        if not path.endswith("api/offer"):
            log.info("Unhandled session-scoped path: %s", path)
            return Response(status_code=200)

        try:
            data = await request.json()
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to parse RTVI request body: %s", exc)
            return Response(content="Invalid WebRTC request", status_code=400)

        if request.method == "POST":
            webrtc_request = SmallWebRTCRequest(
                sdp=data["sdp"],
                type=data["type"],
                pc_id=data.get("pc_id"),
                restart_pc=data.get("restart_pc"),
                request_data=(
                    data.get("request_data")
                    or data.get("requestData")
                    or active_session
                ),
            )
            return await _offer(webrtc_request, background_tasks)

        if request.method == "PATCH":
            patch = SmallWebRTCPatchRequest(
                pc_id=data["pc_id"],
                candidates=[IceCandidate(**c) for c in data.get("candidates", [])],
            )
            return await _ice_candidate(patch)

        return Response(status_code=200)

    return app


app = _build_app()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _serve() -> int:
    """Read host/port from app_settings then start uvicorn.

    Uvicorn is normally driven by a CLI invocation, but reading host/port
    from the DB requires loading SiteConfig first — so we do that here
    and then hand a configured ``uvicorn.Config`` to ``uvicorn.Server``.

    Honors ``voice_agent_webrtc_enabled`` (#383) — when false the process
    exits 0 immediately so docker's ``unless-stopped`` policy leaves the
    container stopped without crash-looping.

    Returns the desired process exit code.
    """
    import asyncpg
    import uvicorn

    logging.basicConfig(level=logging.INFO, format=_LOG_FMT)
    log = logging.getLogger("voice_agent_webrtc")

    _ensure_brain_on_path()
    from brain.bootstrap import require_database_url
    from services.site_config import SiteConfig

    dsn = require_database_url(source="voice_agent_webrtc")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        site_config = SiteConfig()
        await site_config.load(pool)

        enabled = str(
            site_config.get("voice_agent_webrtc_enabled", "true"),
        ).strip().lower()
        if enabled in {"false", "0", "no", "off"}:
            log.info(
                "voice_agent_webrtc_enabled=%s — surface disabled, "
                "exiting 0 so docker leaves us stopped under unless-stopped.",
                enabled,
            )
            return 0

        host = site_config.get("voice_agent_webrtc_host", "0.0.0.0")
        port = int(site_config.get("voice_agent_webrtc_port", "8003"))
    finally:
        # We close the bootstrap pool — the app's lifespan will create
        # its own pool. Keeps the two lifecycles uncoupled.
        await pool.close()

    log.info("Starting voice WebRTC server on %s:%d", host, port)
    config = uvicorn.Config(
        app="services.voice_agent_webrtc:app",
        host=host,
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(_serve())
    except KeyboardInterrupt:
        rc = 0
    sys.exit(rc or 0)
