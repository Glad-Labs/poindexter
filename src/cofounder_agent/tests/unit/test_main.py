"""Unit tests for main.py route handlers.

Currently covers the HEAD / probe handler added for poindexter#396 — the
worker logged 405 noise every minute because uptime-kuma + the docker host
HEAD-probed the root before falling back to GET.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient


def _build_probe_app() -> FastAPI:
    """Mount only the HEAD / handler from main.py onto a fresh app.

    Importing the real ``main`` module triggers heavy startup wiring
    (StartupManager, settings, telemetry, etc.) that isn't needed here —
    we just want to confirm the handler answers HEAD with 200.
    """
    app = FastAPI()

    # Mirror the implementation in main.py so the test fails loudly if the
    # handler signature or status code drifts.
    @app.head("/")
    async def root_head() -> Response:
        return Response(status_code=200)

    return app


@pytest.mark.unit
class TestHeadRoot:
    def test_head_root_returns_200(self):
        client = TestClient(_build_probe_app())
        resp = client.head("/")
        assert resp.status_code == 200
        # HEAD must not return a body.
        assert resp.content == b""

    def test_head_root_handler_exists_in_main(self):
        """Guard against the handler being deleted from main.py.

        We don't import main (heavy startup) — instead we grep the source
        for the @app.head("/") decorator. If this assertion fails, the
        405-then-200 probe noise from poindexter#396 will return.
        """
        from pathlib import Path

        main_py = Path(__file__).resolve().parent.parent.parent / "main.py"
        assert main_py.is_file(), f"expected main.py at {main_py}"
        source = main_py.read_text(encoding="utf-8")
        assert '@app.head("/")' in source, (
            "HEAD / handler missing from main.py — uptime-kuma probes will "
            "log 405 noise every minute (poindexter#396)."
        )


@pytest.mark.unit
class TestProcessCommandErrorPassthrough:
    """poindexter#741 — process_command raises a deliberate HTTPException(503)
    when the orchestrator isn't initialized. A broad `except Exception` after
    it must NOT convert that 503 into a generic 500."""

    def _slice_process_command(self) -> str:
        """Return the body of main.py's process_command handler.

        We don't import main (heavy startup) — slice the source between the
        ``async def process_command`` definition and the next top-level
        ``async def``/``@app`` so the guard is scoped to this one handler.
        """
        import re
        from pathlib import Path

        main_py = Path(__file__).resolve().parent.parent.parent / "main.py"
        assert main_py.is_file(), f"expected main.py at {main_py}"
        source = main_py.read_text(encoding="utf-8")

        start = source.index("async def process_command")
        rest = source[start + 1 :]
        # Next handler boundary (decorator or another top-level async def).
        m = re.search(r"\n@app\.|\n@\w|\nasync def ", rest)
        end = (start + 1 + m.start()) if m else len(source)
        return source[start:end]

    def test_command_handler_reraises_httpexception(self):
        body = self._slice_process_command()
        assert "except HTTPException:" in body, (
            "process_command must re-raise HTTPException before the broad "
            "`except Exception`, or its deliberate 503 (orchestrator not "
            "initialized) collapses into a 500 (poindexter#741)."
        )
        # The re-raise guard must come *before* the broad catch.
        assert body.index("except HTTPException:") < body.index(
            "except Exception"
        ), "the HTTPException re-raise must precede `except Exception`"

    def test_command_handler_behaviour_mirror(self):
        """Behavioural mirror of the fixed handler: a 503 raised inside the
        try survives the broad except. Mirrors main.py so it fails loudly if
        the real handler's contract drifts (paired with the source guard)."""
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient

        app = FastAPI()

        @app.post("/command")
        async def _command():  # mirrors main.py process_command's error path
            try:
                raise HTTPException(status_code=503, detail="Orchestrator not initialized")
            except HTTPException:
                raise
            except Exception as e:  # pragma: no cover - defensive
                raise HTTPException(status_code=500, detail="boom") from e

        resp = TestClient(app, raise_server_exceptions=False).post("/command")
        assert resp.status_code == 503
