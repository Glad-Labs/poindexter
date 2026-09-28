"""Unit tests for brain/brain_daemon.py :func:`docker_restart_container`.

The firefighter's ``restart_container`` action and console restart requests
(``service_restart``) both call it. It is a thin wrapper over the shared
``docker_utils.restart_container`` (tested in ``test_docker_utils_restart.py``);
these tests pin what the wrapper adds and what it must not: the
``(ok, detail)`` contract its two callers read, the not-in-docker refusal,
the DB-tunable timeout (it used a hardcoded 30 s until the shared helper,
under the worker's 75 s stop grace), and silence -- its callers audit the
outcome and the firefighter engine decides whether it pages.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# brain/ lives outside the poindexter distro; mirror the path-prelude the
# other brain_daemon tests use so its flat sibling imports resolve.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "src" / "cofounder_agent" / "poindexter" / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from poindexter.brain import brain_daemon as bd  # noqa: E402
from poindexter.brain import docker_utils  # noqa: E402

pytestmark = pytest.mark.asyncio


class _CP:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


@pytest.fixture
def silent_notifiers():
    """The page and the notice, both recorded. Neither may fire here."""
    with patch.object(bd, "notify", new=AsyncMock()) as page, \
            patch.object(bd, "notify_discord_ops", new=AsyncMock()) as notice:
        yield page, notice


async def test_restart_ok(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return _CP(0)  # inspect ok, then docker restart ok

    monkeypatch.setattr(docker_utils.subprocess, "run", fake_run)
    ok, detail = await bd.docker_restart_container("poindexter-worker")
    assert ok is True
    assert detail == "restarted poindexter-worker"
    assert calls[0][:2] == ["docker", "inspect"]
    assert calls[1] == ["docker", "restart", "poindexter-worker"]


async def test_restart_missing_container_is_not_ok(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return _CP(1, stderr="Error: No such object: ghost")  # inspect fails

    monkeypatch.setattr(docker_utils.subprocess, "run", fake_run)
    ok, detail = await bd.docker_restart_container("ghost")
    assert ok is False
    assert detail == "container ghost not found (likely mid-recreate)"
    assert len(calls) == 1, "a missing container must not be restarted"


async def test_not_docker_returns_false(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", False, raising=False)
    run = MagicMock()
    monkeypatch.setattr(docker_utils.subprocess, "run", run)
    ok, detail = await bd.docker_restart_container("poindexter-worker")
    assert ok is False
    assert "docker" in detail.lower()
    run.assert_not_called()


async def test_restart_timeout_is_the_db_knob_not_a_hardcoded_30s(monkeypatch):
    """The firefighter and console paths waited 30 s while restart_service
    waited ``brain_docker_restart_timeout_seconds``. A worker that took its
    full 75 s stop grace was reported as a failed restart the firefighter
    then paged about, while dockerd finished it."""
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    run = MagicMock(side_effect=[_CP(0), _CP(0)])
    monkeypatch.setattr(docker_utils.subprocess, "run", run)
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value="150")

    ok, _detail = await bd.docker_restart_container("poindexter-worker", pool=pool)

    assert ok is True
    assert run.call_args_list[1].kwargs["timeout"] == 150


async def test_default_timeout_without_a_pool_outlasts_the_worker_grace(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    run = MagicMock(side_effect=[_CP(0), _CP(0)])
    monkeypatch.setattr(docker_utils.subprocess, "run", run)

    await bd.docker_restart_container("poindexter-worker")

    assert run.call_args_list[1].kwargs["timeout"] == docker_utils.DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS
    assert run.call_args_list[1].kwargs["timeout"] > 75


async def test_timed_out_restart_is_not_ok_and_says_dockerd_may_finish(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    monkeypatch.setattr(
        docker_utils.subprocess, "run",
        MagicMock(side_effect=[_CP(0), subprocess.TimeoutExpired(["docker", "restart"], 90)]),
    )

    ok, detail = await bd.docker_restart_container("poindexter-worker")

    assert ok is False
    assert "did not return within 90s" in detail
    assert "may still complete it" in detail


@pytest.mark.parametrize(
    "script",
    [
        [_CP(0), _CP(0)],  # restarted
        [_CP(0), _CP(1, stderr="permission denied")],  # docker restart failed
        [_CP(1, stderr="Error: No such object: c")],  # missing
        [FileNotFoundError("docker")],  # no CLI
        [_CP(0), RuntimeError("socket gone")],  # anything else
    ],
)
async def test_never_notifies(monkeypatch, silent_notifiers, script):
    """The firefighter records the outcome to audit_log and pages (or not)
    by the alert's own routing; console restarts report through their
    request row. A notice or page from here would duplicate both."""
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    monkeypatch.setattr(docker_utils.subprocess, "run", MagicMock(side_effect=script))

    await bd.docker_restart_container("c", pool=None)

    page, notice = silent_notifiers
    page.assert_not_called()
    notice.assert_not_called()
