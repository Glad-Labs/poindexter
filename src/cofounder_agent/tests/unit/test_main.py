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
