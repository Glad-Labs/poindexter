"""Unit tests for brain/brain_daemon.py :func:`docker_restart_container`."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# brain/ lives outside the poindexter distro; mirror the path-prelude the
# other brain_daemon tests use so its flat sibling imports resolve.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402

pytestmark = pytest.mark.asyncio


class _CP:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


async def test_restart_ok(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return _CP(0)  # inspect ok, then docker restart ok

    monkeypatch.setattr(bd.subprocess, "run", fake_run)
    ok, detail = await bd.docker_restart_container("poindexter-worker")
    assert ok is True
    assert "poindexter-worker" in detail
    assert ["docker", "restart", "poindexter-worker"] in calls


async def test_restart_missing_container_is_not_ok(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)

    def fake_run(args, **kw):
        return _CP(1, stderr="No such object")  # inspect fails

    monkeypatch.setattr(bd.subprocess, "run", fake_run)
    ok, detail = await bd.docker_restart_container("ghost")
    assert ok is False
    assert "not found" in detail


async def test_not_docker_returns_false(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", False, raising=False)
    ok, detail = await bd.docker_restart_container("poindexter-worker")
    assert ok is False
    assert "docker" in detail.lower()
