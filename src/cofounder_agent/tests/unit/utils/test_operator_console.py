"""Tests for utils/operator_console.py

Covers ``mount_operator_console`` — the presence-based mount for the
operator-console SPA (a Pro-tier overlay stripped from the public mirror):

- absent console directory  -> returns False, mounts nothing, does NOT raise
  (this is the OSS-mirror case: the console/ dir is stripped, and a bare
  ``StaticFiles(directory=missing)`` would otherwise raise RuntimeError at boot)
- present console directory  -> returns True, mounts /console/, serves index.html
- default console_dir resolves to src/cofounder_agent/console (the pre-extraction
  ``Path(__file__).parent / "console"`` location from main.py)
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from utils.operator_console import _default_console_dir, mount_operator_console


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
