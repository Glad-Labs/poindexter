"""Unit tests for brain/brain_daemon.py :func:`docker_restart_container`.

The firefighter's ``restart_container`` action and console restart requests
(``service_restart``) both call it. It is a thin wrapper over the shared
``docker_utils.restart_container`` (tested in ``test_docker_utils_restart.py``);
these tests pin what the wrapper adds and what it must not: the
``ContainerRestart`` outcome its two callers read (they need the status, not a
bare ok, to tell a restart the recently-started guard declined from one that
failed), the ``force`` kwarg that lets an operator's restart past that guard,
the not-in-docker refusal, the DB-tunable timeout (it used a hardcoded 30 s
until the shared helper, under the worker's 75 s stop grace), and silence --
its callers audit the outcome and the firefighter engine decides whether it
pages.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.brain import brain_daemon as bd
from poindexter.brain import docker_utils

pytestmark = pytest.mark.asyncio

#: A container that has been up for months, however the test clock reads.
_OLD = "running 2026-01-01T00:00:00.000000000Z\n"


def _up_for(seconds: float) -> str:
    """``docker inspect``'s answer for a container that started ``seconds`` ago."""
    then = datetime.now(UTC) - timedelta(seconds=seconds)
    return "running " + then.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z\n"


class _CP:
    def __init__(self, returncode, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


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
        # inspect ok (up for months), then docker restart ok
        return _CP(0, stdout=_OLD)

    monkeypatch.setattr(docker_utils.subprocess, "run", fake_run)
    outcome = await bd.docker_restart_container("poindexter-worker")
    assert outcome.ok is True
    assert outcome.status == docker_utils.RESTART_OK
    assert outcome.detail == "restarted poindexter-worker"
    assert calls[0][:2] == ["docker", "inspect"]
    assert calls[1] == ["docker", "restart", "poindexter-worker"]


async def test_restart_missing_container_is_not_ok(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return _CP(1, stderr="Error: No such object: ghost")  # inspect fails

    monkeypatch.setattr(docker_utils.subprocess, "run", fake_run)
    outcome = await bd.docker_restart_container("ghost")
    assert outcome.ok is False
    assert outcome.status == docker_utils.RESTART_MISSING
    assert outcome.detail == "container ghost not found (likely mid-recreate)"
    assert len(calls) == 1, "a missing container must not be restarted"


async def test_not_docker_returns_a_failed_outcome(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", False, raising=False)
    run = MagicMock()
    monkeypatch.setattr(docker_utils.subprocess, "run", run)
    outcome = await bd.docker_restart_container("poindexter-worker")
    assert outcome.ok is False
    assert outcome.container == "poindexter-worker"
    assert outcome.status == docker_utils.RESTART_ERROR
    assert "docker" in outcome.detail.lower()
    run.assert_not_called()


async def test_restart_timeout_is_the_db_knob_not_a_hardcoded_30s(monkeypatch):
    """The firefighter and console paths waited 30 s while restart_service
    waited ``brain_docker_restart_timeout_seconds``. A worker that took its
    full 75 s stop grace was reported as a failed restart the firefighter
    then paged about, while dockerd finished it."""
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    run = MagicMock(side_effect=[_CP(0, stdout=_OLD), _CP(0)])
    monkeypatch.setattr(docker_utils.subprocess, "run", run)
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value="150")

    outcome = await bd.docker_restart_container("poindexter-worker", pool=pool)

    assert outcome.ok is True
    assert run.call_args_list[1].kwargs["timeout"] == 150


async def test_default_timeout_without_a_pool_outlasts_the_worker_grace(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    run = MagicMock(side_effect=[_CP(0, stdout=_OLD), _CP(0)])
    monkeypatch.setattr(docker_utils.subprocess, "run", run)

    await bd.docker_restart_container("poindexter-worker")

    assert run.call_args_list[1].kwargs["timeout"] == docker_utils.DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS
    assert run.call_args_list[1].kwargs["timeout"] > 75


async def test_timed_out_restart_is_not_ok_and_says_dockerd_may_finish(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    monkeypatch.setattr(
        docker_utils.subprocess, "run",
        MagicMock(side_effect=[
            _CP(0, stdout=_OLD), subprocess.TimeoutExpired(["docker", "restart"], 90),
        ]),
    )

    outcome = await bd.docker_restart_container("poindexter-worker")

    assert outcome.ok is False
    assert outcome.status == docker_utils.RESTART_TIMED_OUT
    assert "did not return within 90s" in outcome.detail
    assert "may still complete it" in outcome.detail


# ---------------------------------------------------------------------------
# The recently-started guard, and the operator's way past it
# ---------------------------------------------------------------------------


async def test_a_container_that_just_started_is_not_restarted(monkeypatch):
    """The firefighter and the console queue's automated rows meet the guard:
    a second restart would land while the container is still starting up."""
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    run = MagicMock(side_effect=[_CP(0, stdout=_up_for(30))])
    monkeypatch.setattr(docker_utils.subprocess, "run", run)

    outcome = await bd.docker_restart_container("poindexter-worker")

    assert outcome.ok is False
    assert outcome.status == docker_utils.RESTART_RECENTLY_STARTED
    assert 25 < outcome.uptime_seconds < 40
    assert "started" in outcome.detail and "not restarted" in outcome.detail
    assert run.call_count == 1, "the guard must stop before `docker restart`"


async def test_force_restarts_a_container_that_just_started(monkeypatch):
    """An operator's explicit restart (a console click) is not second-guessed."""
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    run = MagicMock(side_effect=[_CP(0, stdout=_up_for(5)), _CP(0)])
    monkeypatch.setattr(docker_utils.subprocess, "run", run)

    outcome = await bd.docker_restart_container("poindexter-worker", force=True)

    assert outcome.ok is True
    assert outcome.status == docker_utils.RESTART_OK
    assert run.call_args_list[1].args[0] == ["docker", "restart", "poindexter-worker"]


async def test_force_is_passed_through_to_the_shared_helper(monkeypatch):
    monkeypatch.setattr(bd, "IS_DOCKER", True, raising=False)
    helper = AsyncMock(return_value=docker_utils.ContainerRestart(
        "c", docker_utils.RESTART_OK, "restarted c", 90,
    ))
    monkeypatch.setattr(docker_utils, "restart_container", helper)
    pool = object()

    await bd.docker_restart_container("c", pool=pool, force=True)
    await bd.docker_restart_container("c", pool=pool)

    assert [c.kwargs for c in helper.await_args_list] == [
        {"pool": pool, "force": True},
        {"pool": pool, "force": False},
    ]


async def test_the_recently_started_window_fits_inside_a_brain_cycle():
    """It has to outlast a worker restart (40-90 s to come back), but a window
    at or over the brain cycle would also hold off the next cycle's heal of a
    container the brain itself just restarted."""
    assert 90 < docker_utils.DOCKER_RESTART_MIN_UPTIME_DEFAULT_SECONDS < bd.CYCLE_SECONDS


@pytest.mark.parametrize(
    "script",
    [
        [_CP(0, stdout=_OLD), _CP(0)],  # restarted
        [_CP(0, stdout=_OLD), _CP(1, stderr="permission denied")],  # docker restart failed
        [_CP(1, stderr="Error: No such object: c")],  # missing
        [_CP(0, stdout=_up_for(30))],  # started moments ago
        [FileNotFoundError("docker")],  # no CLI
        [_CP(0, stdout=_OLD), RuntimeError("socket gone")],  # anything else
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
