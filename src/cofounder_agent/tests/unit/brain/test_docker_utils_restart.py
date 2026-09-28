"""Unit tests for :func:`poindexter.brain.docker_utils.restart_container`.

The shared ``docker restart`` implementation behind ``brain_daemon.restart_service``,
``brain_daemon.docker_restart_container`` (firefighter + console restarts) and
``health_probes``' self-heal. Before it, those three disagreed: a 90 s timeout
read from ``app_settings`` in one, a hardcoded 30 s in the firefighter's, a
hardcoded 60 s and no inspect pre-check in health_probes'. The worker's
``stop_grace_period`` is 75 s, so the two hardcoded timeouts could report a
restart dockerd went on to finish as a failure.

What must hold:

* inspect, then restart, both off the event loop;
* the restart timeout is ``app_settings.brain_docker_restart_timeout_seconds``
  for every caller, with a loud fallback for an unusable value;
* a missing container is not restarted, and only "no such object/container"
  counts as missing (a dead docker socket is an error, not a recreate window);
* nothing raises;
* no brain module grows its own ``docker restart`` again (the ratchet at the
  bottom).
"""

from __future__ import annotations

import ast
import logging
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import docker_utils as du


class _CP:
    """``subprocess.CompletedProcess`` stand-in."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Docker:
    """Records every ``subprocess.run`` call and answers from a script.

    Each script entry is either a ``_CP`` to return or an exception to raise,
    consumed in call order (inspect first, then restart).
    """

    def __init__(self, *script):
        self.script = list(script)
        self.calls: list[tuple[list[str], dict]] = []
        self.threads: list[int] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        self.threads.append(threading.get_ident())
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    @property
    def argvs(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls]

    def timeout_of(self, i: int):
        return self.calls[i][1].get("timeout")


def _pool(value):
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=value)
    return pool


@pytest.fixture
def docker(monkeypatch):
    """Install a scripted ``subprocess.run``; call it with the script."""

    def _install(*script) -> _Docker:
        fake = _Docker(*script)
        monkeypatch.setattr(du.subprocess, "run", fake)
        return fake

    return _install


# ---------------------------------------------------------------------------
# The happy path, and what it runs
# ---------------------------------------------------------------------------


async def test_inspects_then_restarts(docker):
    fake = docker(_CP(0, "running\n"), _CP(0))

    outcome = await du.restart_container("poindexter-worker")

    assert outcome.ok is True
    assert outcome.status == du.RESTART_OK
    assert outcome.detail == "restarted poindexter-worker"
    assert fake.argvs == [
        ["docker", "inspect", "--format", "{{.State.Status}}", "poindexter-worker"],
        ["docker", "restart", "poindexter-worker"],
    ]
    for _argv, kwargs in fake.calls:
        assert kwargs["capture_output"] is True and kwargs["text"] is True


async def test_both_docker_calls_run_off_the_event_loop(docker):
    """The brain is one event loop; a docker call made on it would freeze
    every probe for up to the restart timeout."""
    fake = docker(_CP(0, "running\n"), _CP(0))

    await du.restart_container("poindexter-worker")

    loop_thread = threading.get_ident()
    assert len(fake.threads) == 2
    assert all(t != loop_thread for t in fake.threads)


# ---------------------------------------------------------------------------
# The timeout: one knob, every caller
# ---------------------------------------------------------------------------


async def test_default_timeout_without_a_pool(docker):
    fake = docker(_CP(0), _CP(0))

    outcome = await du.restart_container("c")

    assert fake.timeout_of(0) == du.DOCKER_INSPECT_TIMEOUT_SECONDS == 10
    assert fake.timeout_of(1) == du.DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS == 90
    assert outcome.timeout_seconds == 90


def test_default_outlasts_the_workers_stop_grace_period():
    """``docker restart`` waits the container's own stop grace period before it
    kills it. The worker's is 75 s; a default at or under that would report a
    slow-but-successful worker restart as a failure again."""
    assert du.DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS > 75


async def test_restart_timeout_comes_from_app_settings(docker):
    pool = _pool("120")
    fake = docker(_CP(0), _CP(0))

    outcome = await du.restart_container("c", pool=pool)

    assert fake.timeout_of(1) == 120
    assert outcome.timeout_seconds == 120
    # The inspect is a metadata read; the knob does not stretch it.
    assert fake.timeout_of(0) == 10
    pool.fetchval.assert_awaited_once()
    assert pool.fetchval.await_args.args[1] == "brain_docker_restart_timeout_seconds"


@pytest.mark.parametrize(("raw", "expected"), [("120", 120), (" 45 ", 45), ("7.5", 7.5), (60, 60)])
async def test_timeout_setting_parses(raw, expected):
    assert await du.docker_restart_timeout_seconds(_pool(raw)) == expected


@pytest.mark.parametrize("raw", [None, "", "   "])
async def test_absent_timeout_setting_uses_the_default_quietly(raw, caplog):
    caplog.set_level(logging.WARNING, logger=du.logger.name)

    assert await du.docker_restart_timeout_seconds(_pool(raw)) == 90
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "nan", "inf"])
async def test_unusable_timeout_setting_falls_back_loudly(raw, caplog):
    caplog.set_level(logging.WARNING, logger=du.logger.name)

    assert await du.docker_restart_timeout_seconds(_pool(raw)) == 90
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "brain_docker_restart_timeout_seconds" in warnings[0].getMessage()


async def test_unreadable_timeout_setting_falls_back_loudly(caplog):
    caplog.set_level(logging.WARNING, logger=du.logger.name)
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=OSError("pool closed"))

    assert await du.docker_restart_timeout_seconds(pool) == 90
    assert any(
        "brain_docker_restart_timeout_seconds" in r.getMessage() and "pool closed" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    )


# ---------------------------------------------------------------------------
# The inspect pre-check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stderr",
    [
        "Error: No such object: poindexter-worker\n",  # docker CLI 27 (the brain image)
        "Error: no such object: poindexter-worker\n",  # docker CLI 29
    ],
)
async def test_missing_container_is_not_restarted(docker, stderr):
    fake = docker(_CP(1, stderr=stderr))

    outcome = await du.restart_container("poindexter-worker")

    assert outcome.status == du.RESTART_MISSING
    assert outcome.ok is False
    assert outcome.detail == "container poindexter-worker not found (likely mid-recreate)"
    assert fake.argvs == [["docker", "inspect", "--format", "{{.State.Status}}", "poindexter-worker"]]


async def test_unreachable_docker_daemon_is_an_error_not_a_missing_container(docker):
    """A dead or unreadable socket makes ``docker inspect`` exit 1 too. That
    is a failure to report, not a recreate window to wait out quietly."""
    fake = docker(_CP(1, stderr=(
        "failed to connect to the docker API at unix:///var/run/docker.sock; "
        "check if the path is correct and if the daemon is running\n"
    )))

    outcome = await du.restart_container("poindexter-worker")

    assert outcome.status == du.RESTART_ERROR
    assert "docker inspect poindexter-worker exited 1" in outcome.detail
    assert "failed to connect to the docker API" in outcome.detail
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# Failures: returned, never raised
# ---------------------------------------------------------------------------


async def test_non_zero_restart_is_failed_with_docker_stderr(docker):
    docker(_CP(0, "running\n"), _CP(1, stderr="permission denied\n"))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_FAILED
    assert outcome.stderr == "permission denied"
    assert outcome.detail == "docker restart failed for c: permission denied"


async def test_restart_outliving_the_timeout_is_timed_out(docker):
    docker(_CP(0, "running\n"), subprocess.TimeoutExpired(["docker", "restart", "c"], 90))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_TIMED_OUT
    assert outcome.ok is False
    assert "did not return within 90s" in outcome.detail
    assert "brain_docker_restart_timeout_seconds" in outcome.detail
    assert "may still complete it" in outcome.detail


async def test_hung_inspect_is_an_error_and_restarts_nothing(docker):
    fake = docker(subprocess.TimeoutExpired(["docker", "inspect"], 10))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_ERROR
    assert outcome.detail.startswith("docker inspect error for c:")
    assert len(fake.calls) == 1


@pytest.mark.parametrize("fail_on", ["inspect", "restart"])
async def test_missing_docker_cli(docker, fail_on):
    boom = FileNotFoundError("docker")
    script = (boom,) if fail_on == "inspect" else (_CP(0), boom)
    docker(*script)

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_NO_DOCKER_CLI
    assert outcome.detail == "docker CLI not available in brain container"


async def test_any_other_exception_is_returned_not_raised(docker):
    docker(_CP(0), RuntimeError("socket gone"))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_ERROR
    assert outcome.error == "socket gone"
    assert outcome.detail == "docker restart error for c: socket gone"


@pytest.mark.parametrize(("os_name", "flags"), [("nt", 0x08000000), ("posix", 0)])
def test_no_console_window_on_a_windows_host(docker, monkeypatch, os_name, flags):
    """CREATE_NO_WINDOW on Windows (no console flash); POSIX Popen refuses
    any non-zero creationflags."""
    fake = docker(_CP(0))
    monkeypatch.setattr(du, "os", SimpleNamespace(name=os_name))

    du._run_docker(["docker", "inspect", "c"], 10)

    assert fake.calls[0][1]["creationflags"] == flags


# ---------------------------------------------------------------------------
# Ratchet: no new docker-restart copies in the brain; the old ones only leave
# ---------------------------------------------------------------------------

_BRAIN_DIR = Path(du.__file__).resolve().parent

# Brain modules that still run their own ``docker restart``. Each is a sync
# ``restart_fn`` seam its probe offloads with ``asyncio.to_thread``, so none
# blocks the loop, but none reads brain_docker_restart_timeout_seconds or
# inspects first either. Move one onto ``docker_utils.restart_container`` and
# delete it here; nothing may be added. (migration_drift_probe restarts the
# worker on a hardcoded 30 s, under the worker's 75 s stop grace.)
_LEGACY_DOCKER_RESTARTS = {
    "auto_embed_watch.py",
    "backup_watcher.py",
    "docker_port_forward_probe.py",
    "migration_drift_probe.py",
    "offsite_backup_watch.py",
    "postiz_queue_watch.py",
    "ram_recycle_common.py",
}


def _modules_running_docker_restart() -> set[str]:
    """Brain modules containing a ``["docker", "restart", ...]`` argv literal."""
    found: set[str] = set()
    for path in sorted(_BRAIN_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.List)
                and len(node.elts) >= 2
                and all(isinstance(e, ast.Constant) for e in node.elts[:2])
                and [e.value for e in node.elts[:2]] == ["docker", "restart"]
            ):
                found.add(path.relative_to(_BRAIN_DIR).as_posix())
                break
    return found


def test_the_shared_helper_is_seen_by_the_scan():
    """Positive control: a scan that found nothing would pass the ratchet."""
    assert "docker_utils.py" in _modules_running_docker_restart()


def test_no_new_brain_module_runs_its_own_docker_restart():
    extra = _modules_running_docker_restart() - {"docker_utils.py"} - _LEGACY_DOCKER_RESTARTS
    assert not extra, (
        f"{sorted(extra)} run their own `docker restart`. Call "
        "poindexter.brain.docker_utils.restart_container instead: it inspects "
        "first, reads app_settings.brain_docker_restart_timeout_seconds and "
        "stays off the event loop."
    )


def test_the_legacy_list_only_shrinks():
    """A module that moved onto the shared helper must leave the list, or the
    list stops meaning anything."""
    migrated = _LEGACY_DOCKER_RESTARTS - _modules_running_docker_restart()
    assert not migrated, f"{sorted(migrated)} no longer restart on their own; remove them from _LEGACY_DOCKER_RESTARTS"


def test_the_three_callers_share_the_helper():
    """brain_daemon (restart_service + the firefighter's docker_restart_container)
    and health_probes (REMEDIATIONS self-heal) keep no restart of their own."""
    found = _modules_running_docker_restart()
    assert "brain_daemon.py" not in found
    assert "health_probes.py" not in found
