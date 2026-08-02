"""Tests for utils/operator_console.py

Covers ``mount_operator_console`` — the presence-based mount for the
operator-console SPA (a Pro-tier overlay stripped from the public mirror):

- absent console directory  -> returns False, mounts nothing, does NOT raise
  (this is the OSS-mirror case: the console/ dir is stripped, and a bare
  ``StaticFiles(directory=missing)`` would otherwise raise RuntimeError at boot)
- present console directory  -> returns True, mounts /console/, serves index.html
- default console_dir resolves to src/cofounder_agent/console (the pre-extraction
  ``Path(__file__).parent / "console"`` location from main.py)
- served assets carry ``Cache-Control: no-cache`` so a deploy invalidates the
  console's unhashed, globally-scoped files atomically instead of leaving the
  browser running a mixed version
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.cache_control import NO_STORE, CacheControlMiddleware
from utils.operator_console import (
    CONSOLE_CACHE_CONTROL,
    _default_console_dir,
    mount_operator_console,
)


def _has_console_mount(app: FastAPI) -> bool:
    """True if ``app`` carries a route/mount named ``console``."""
    return any(getattr(route, "name", None) == "console" for route in app.routes)


class TestMountOperatorConsoleAbsent:
    """The OSS-mirror case: the console directory has been stripped."""

    def test_absent_directory_returns_false(self, tmp_path):
        app = FastAPI()
        missing = tmp_path / "does-not-exist"
        assert mount_operator_console(app, console_dir=missing) is False

    def test_absent_directory_mounts_nothing(self, tmp_path):
        app = FastAPI()
        missing = tmp_path / "does-not-exist"
        mount_operator_console(app, console_dir=missing)
        assert _has_console_mount(app) is False

    def test_absent_directory_does_not_raise(self, tmp_path):
        # The core guarantee: on the public mirror the console is stripped, and
        # a bare StaticFiles(directory=missing) raises RuntimeError at startup.
        # The guard must swallow that so the OSS backend boots cleanly.
        app = FastAPI()
        missing = tmp_path / "does-not-exist"
        mount_operator_console(app, console_dir=missing)  # must not raise


class TestMountOperatorConsolePresent:
    """The operator case: the console directory is present in the tree."""

    def test_present_directory_returns_true(self, tmp_path):
        (tmp_path / "index.html").write_text("<h1>console</h1>", encoding="utf-8")
        app = FastAPI()
        assert mount_operator_console(app, console_dir=tmp_path) is True

    def test_present_directory_serves_index_html(self, tmp_path):
        (tmp_path / "index.html").write_text(
            "<h1>operator console</h1>", encoding="utf-8"
        )
        app = FastAPI()
        mount_operator_console(app, console_dir=tmp_path)

        client = TestClient(app)
        resp = client.get("/console/")

        assert resp.status_code == 200
        assert "operator console" in resp.text


class TestDefaultConsoleDir:
    """The default location must match the pre-extraction main.py path."""

    def test_default_resolves_to_cofounder_agent_console(self):
        console_dir = _default_console_dir()
        assert console_dir.name == "console"
        # ``.../src/cofounder_agent/console`` — parent is the package root.
        assert console_dir.parent.name == "cofounder_agent"


def _console_app(tmp_path, *, with_middleware: bool = False) -> FastAPI:
    """A FastAPI app serving a two-asset console out of ``tmp_path``."""
    (tmp_path / "index.html").write_text(
        '<script src="js/app.js"></script>', encoding="utf-8"
    )
    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "app.js").write_text("window.PX = {};", encoding="utf-8")

    app = FastAPI()
    if with_middleware:
        app.add_middleware(CacheControlMiddleware)
    mount_operator_console(app, console_dir=tmp_path)
    return app


class TestConsoleCacheHeaders:
    """Deploys must invalidate the console atomically.

    The console has no build step and no hashed filenames — ~33 plain script/
    link tags sharing one global lexical scope. Serving any subset from cache
    while fetching the rest fresh runs a mixed version of the app, which is what
    bit on 2026-07-31 when four console PRs landed the same day. ``no-cache``
    (revalidate every use, cheap 304s off the ETag ``StaticFiles`` already
    emits) is what makes "some files stale, some fresh" unrepresentable.
    """

    def test_asset_is_no_cache(self, tmp_path):
        client = TestClient(_console_app(tmp_path))
        resp = client.get("/console/js/app.js")

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == CONSOLE_CACHE_CONTROL

    def test_index_html_is_no_cache(self, tmp_path):
        # index.html is the one that names every other file, so a stale copy
        # can't even request the assets a new deploy added.
        client = TestClient(_console_app(tmp_path))
        resp = client.get("/console/")

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == CONSOLE_CACHE_CONTROL

    def test_no_cache_survives_the_304_revalidation(self, tmp_path):
        # no-cache is only atomic if revalidation stays cheap AND the policy
        # rides along on the 304 — otherwise the re-cached entry loses it and
        # the next load is free to skip revalidating.
        client = TestClient(_console_app(tmp_path))
        first = client.get("/console/js/app.js")
        etag = first.headers["etag"]

        second = client.get("/console/js/app.js", headers={"If-None-Match": etag})

        assert second.status_code == 304
        assert not second.content
        assert second.headers["cache-control"] == CONSOLE_CACHE_CONTROL

    def test_middleware_defers_to_the_mount(self, tmp_path):
        # The regression guard that matters. CacheControlMiddleware stamps a
        # catch-all `private, max-age=60` on anything that arrives without a
        # Cache-Control — which is precisely how the console got a 60s
        # no-revalidation window. It only defers because the mount now sets its
        # own header first; if that ordering ever inverts, this goes red.
        client = TestClient(_console_app(tmp_path, with_middleware=True))
        resp = client.get("/console/js/app.js")

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == CONSOLE_CACHE_CONTROL
        assert "max-age" not in resp.headers["cache-control"]

    def test_api_routes_keep_the_middleware_default(self, tmp_path):
        # The mount must not leak its policy onto the rest of the app — the
        # console's needs are specific to unhashed static assets. Bound to the
        # middleware's own constant rather than a literal so a future policy
        # change can't leave this asserting a directive that no longer exists.
        app = _console_app(tmp_path, with_middleware=True)

        @app.get("/api/tasks")
        async def _tasks():
            return {"ok": True}

        resp = TestClient(app).get("/api/tasks")

        assert resp.headers["cache-control"] == NO_STORE
        assert resp.headers["cache-control"] != CONSOLE_CACHE_CONTROL
