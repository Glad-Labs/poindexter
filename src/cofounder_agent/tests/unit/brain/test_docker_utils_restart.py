"""Unit tests for :func:`poindexter.brain.docker_utils.restart_container`.

The shared ``docker restart`` implementation behind ``brain_daemon.restart_service``,
``brain_daemon.docker_restart_container`` (firefighter + console restarts),
``health_probes``' self-heal and every probe that restarts what it watches
(migration_drift, backup/offsite/auto-embed/postiz watches, port-forward, the
two RAM recycles). Before it, those paths disagreed: a 90 s timeout read from
``app_settings`` in one, hardcoded 30 s or 60 s everywhere else, and an
inspect pre-check in only two. The worker's ``stop_grace_period`` is 75 s, so
the hardcoded timeouts could report a restart dockerd went on to finish as a
failure.

What must hold:

* inspect, then restart, both off the event loop;
* the restart timeout is ``app_settings.brain_docker_restart_timeout_seconds``
  for every caller, with a loud fallback for an unusable value;
* a missing container is not restarted, and only "no such object/container"
  counts as missing (a dead docker socket is an error, not a recreate window);
* a container that started less than
  ``app_settings.brain_docker_restart_min_uptime_seconds`` ago is not restarted
  either, unless the caller forces it, and a guard that cannot decide restarts
  rather than blocks;
* nothing raises;
* no brain module grows its own ``docker restart`` again, and every caller of
  the helper handles the recently-started outcome (the ratchets at the bottom).
"""

from __future__ import annotations

import ast
import logging
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.brain import docker_utils as du

#: The instant every test in this module believes it is.
_NOW = datetime(2026, 9, 28, 19, 30, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch):
    """The guard compares docker's StartedAt with the clock; pin the clock."""
    monkeypatch.setattr(du, "_utcnow", lambda: _NOW)


def _started(seconds_ago: float) -> str:
    """``State.StartedAt`` as docker prints it: RFC 3339, nanoseconds, ``Z``."""
    then = _NOW - timedelta(seconds=seconds_ago)
    return then.strftime("%Y-%m-%dT%H:%M:%S.%f") + "000Z"


def _inspect(status: str = "running", seconds_ago: float = 3600) -> str:
    """What the pre-check's ``--format`` prints for a container."""
    return f"{status} {_started(seconds_ago)}\n"


_INSPECT_ARGV_FORMAT = "{{.State.Status}} {{.State.StartedAt}}"


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
    fake = docker(_CP(0, _inspect()), _CP(0))

    outcome = await du.restart_container("poindexter-worker")

    assert outcome.ok is True
    assert outcome.status == du.RESTART_OK
    assert outcome.detail == "restarted poindexter-worker"
    assert outcome.uptime_seconds is None
    assert fake.argvs == [
        ["docker", "inspect", "--format", _INSPECT_ARGV_FORMAT, "poindexter-worker"],
        ["docker", "restart", "poindexter-worker"],
    ]
    for _argv, kwargs in fake.calls:
        assert kwargs["capture_output"] is True and kwargs["text"] is True


async def test_both_docker_calls_run_off_the_event_loop(docker):
    """The brain is one event loop; a docker call made on it would freeze
    every probe for up to the restart timeout."""
    fake = docker(_CP(0, _inspect()), _CP(0))

    await du.restart_container("poindexter-worker")

    loop_thread = threading.get_ident()
    assert len(fake.threads) == 2
    assert all(t != loop_thread for t in fake.threads)


# ---------------------------------------------------------------------------
# The timeout: one knob, every caller
# ---------------------------------------------------------------------------


async def test_default_timeout_without_a_pool(docker):
    fake = docker(_CP(0, _inspect()), _CP(0))

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
    fake = docker(_CP(0, _inspect()), _CP(0))

    outcome = await du.restart_container("c", pool=pool)

    assert fake.timeout_of(1) == 120
    assert outcome.timeout_seconds == 120
    # The inspect is a metadata read; the knob does not stretch it.
    assert fake.timeout_of(0) == 10
    # The timeout is read first; the recently-started guard reads its own knob
    # through the same pool once the inspect has shown a running container.
    assert [c.args[1] for c in pool.fetchval.await_args_list] == [
        "brain_docker_restart_timeout_seconds",
        "brain_docker_restart_min_uptime_seconds",
    ]


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
# The min-uptime knob: how the recently-started window is read
# ---------------------------------------------------------------------------


def _settings_pool(**settings):
    """A pool whose app_settings read answers per key (an absent key is None)."""

    async def _fetchval(_sql, key):
        return settings.get(key)

    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=_fetchval)
    return pool


async def test_default_min_uptime_without_a_pool():
    assert await du.docker_restart_min_uptime_seconds() == 120
    assert await du.docker_restart_min_uptime_seconds(None) == (
        du.DOCKER_RESTART_MIN_UPTIME_DEFAULT_SECONDS
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("120", 120), (" 45 ", 45), ("7.5", 7.5), (60, 60), ("0", 0), (0, 0)],
)
async def test_min_uptime_setting_parses(raw, expected):
    """Zero is a real value here (it turns the guard off), unlike the timeout."""
    assert await du.docker_restart_min_uptime_seconds(_pool(raw)) == expected


@pytest.mark.parametrize("raw", [None, "", "   "])
async def test_absent_min_uptime_setting_uses_the_default_quietly(raw, caplog):
    caplog.set_level(logging.WARNING, logger=du.logger.name)

    assert await du.docker_restart_min_uptime_seconds(_pool(raw)) == 120
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("raw", ["abc", "-5", "nan", "inf"])
async def test_unusable_min_uptime_setting_falls_back_loudly(raw, caplog):
    caplog.set_level(logging.WARNING, logger=du.logger.name)

    assert await du.docker_restart_min_uptime_seconds(_pool(raw)) == 120
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "brain_docker_restart_min_uptime_seconds" in warnings[0].getMessage()
    assert "non-negative" in warnings[0].getMessage()


async def test_unreadable_min_uptime_setting_falls_back_loudly(caplog):
    caplog.set_level(logging.WARNING, logger=du.logger.name)
    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=OSError("pool closed"))

    assert await du.docker_restart_min_uptime_seconds(pool) == 120
    assert any(
        "brain_docker_restart_min_uptime_seconds" in r.getMessage()
        and "pool closed" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    )


def test_the_min_uptime_default_is_seeded_in_settings_defaults():
    """The code default and the seeded row must agree, and the key needs
    its registry entry, or an operator sees a different window than the one
    the brain uses."""
    from poindexter.services import settings_defaults

    key = du.DOCKER_RESTART_MIN_UPTIME_KEY
    assert settings_defaults.DEFAULTS[key] == str(du.DOCKER_RESTART_MIN_UPTIME_DEFAULT_SECONDS)
    assert settings_defaults.METADATA[key] == {"owner": "brain_daemon", "value_type": "integer"}


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
    assert fake.argvs == [["docker", "inspect", "--format", _INSPECT_ARGV_FORMAT, "poindexter-worker"]]


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
    docker(_CP(0, _inspect()), _CP(1, stderr="permission denied\n"))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_FAILED
    assert outcome.stderr == "permission denied"
    assert outcome.detail == "docker restart failed for c: permission denied"


async def test_restart_outliving_the_timeout_is_timed_out(docker):
    docker(_CP(0, _inspect()), subprocess.TimeoutExpired(["docker", "restart", "c"], 90))

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
    script = (boom,) if fail_on == "inspect" else (_CP(0, _inspect()), boom)
    docker(*script)

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_NO_DOCKER_CLI
    assert outcome.detail == "docker CLI not available in brain container"


async def test_any_other_exception_is_returned_not_raised(docker):
    docker(_CP(0, _inspect()), RuntimeError("socket gone"))

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
# The recently-started guard
# ---------------------------------------------------------------------------
#
# Several brain paths can restart the same container (usually the worker) in
# one cycle, and deploy-sync and compose restart it too. The worker takes
# 40-90 s to come back, so a second restart kills it mid-startup. The age is
# docker's own State.StartedAt, read by the inspect the helper already runs.


async def test_a_container_started_moments_ago_is_not_restarted(docker):
    fake = docker(_CP(0, _inspect("running", 30)))

    outcome = await du.restart_container("poindexter-worker")

    assert outcome.status == du.RESTART_RECENTLY_STARTED
    assert outcome.ok is False
    assert outcome.uptime_seconds == 30
    assert outcome.detail == (
        "poindexter-worker started 30s ago, inside the 120s "
        "brain_docker_restart_min_uptime_seconds guard; not restarted"
    )
    assert [argv[1] for argv in fake.argvs] == ["inspect"], "must not reach `docker restart`"


async def test_a_guarded_outcome_still_carries_the_restart_timeout(docker):
    """Every outcome says what timeout the attempt would have used."""
    docker(_CP(0, _inspect("running", 30)))

    outcome = await du.restart_container("c", pool=_settings_pool(
        brain_docker_restart_timeout_seconds="150",
    ))

    assert outcome.status == du.RESTART_RECENTLY_STARTED
    assert outcome.timeout_seconds == 150


@pytest.mark.parametrize(
    ("seconds_ago", "restarted"),
    [(0, False), (30, False), (119.5, False), (120, True), (120.5, True), (3600, True)],
)
async def test_the_window_edge(docker, seconds_ago, restarted):
    """Younger than the window is guarded; at the window and older restarts."""
    fake = docker(_CP(0, _inspect("running", seconds_ago)), _CP(0))

    outcome = await du.restart_container("c")

    assert outcome.ok is restarted
    assert (outcome.status == du.RESTART_OK) is restarted
    assert len(fake.calls) == (2 if restarted else 1)


@pytest.mark.parametrize(
    ("window", "seconds_ago", "restarted"),
    [("300", 200, False), ("30", 60, True), ("90", 89, False), ("90", 91, True)],
)
async def test_the_window_comes_from_app_settings(docker, window, seconds_ago, restarted):
    pool = _settings_pool(brain_docker_restart_min_uptime_seconds=window)
    docker(_CP(0, _inspect("running", seconds_ago)), _CP(0))

    outcome = await du.restart_container("c", pool=pool)

    assert outcome.ok is restarted
    assert pool.fetchval.await_args_list[1].args[1] == "brain_docker_restart_min_uptime_seconds"


async def test_zero_turns_the_guard_off(docker, caplog):
    caplog.set_level(logging.WARNING, logger=du.logger.name)
    pool = _settings_pool(brain_docker_restart_min_uptime_seconds="0")
    docker(_CP(0, _inspect("running", 1)), _CP(0))

    outcome = await du.restart_container("c", pool=pool)

    assert outcome.ok is True
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_an_unusable_window_falls_back_to_the_default_guard(docker):
    """A typo in the knob must not switch the guard off."""
    docker(_CP(0, _inspect("running", 30)))

    outcome = await du.restart_container(
        "c", pool=_settings_pool(brain_docker_restart_min_uptime_seconds="two minutes"),
    )

    assert outcome.status == du.RESTART_RECENTLY_STARTED
    assert "120s" in outcome.detail


async def test_force_restarts_a_container_that_just_started(docker):
    """An operator's explicit restart is not the brain second-guessing itself."""
    pool = _settings_pool()
    fake = docker(_CP(0, _inspect("running", 2)), _CP(0))

    outcome = await du.restart_container("c", pool=pool, force=True)

    assert outcome.status == du.RESTART_OK
    assert [argv[1] for argv in fake.argvs] == ["inspect", "restart"]
    # The guard is skipped outright: its knob is not even read.
    assert [c.args[1] for c in pool.fetchval.await_args_list] == [
        "brain_docker_restart_timeout_seconds",
    ]


async def test_force_defaults_to_off(docker):
    """No caller is forced by omission: the automated paths never pass it."""
    docker(_CP(0, _inspect("running", 2)))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_RECENTLY_STARTED


@pytest.mark.parametrize("status", ["exited", "dead", "created", "restarting", "paused"])
async def test_only_a_running_container_is_guarded(docker, status):
    """A container that is not running is not mid-startup, and it is the one
    that needs the restart, however recently it last started."""
    fake = docker(_CP(0, _inspect(status, 5)), _CP(0))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_OK
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    "stdout",
    ["running\n", "running not-a-timestamp\n", "running \n", "running 0000-00-00T00:00:00Z\n"],
)
async def test_an_unreadable_started_at_restarts_with_a_warning(docker, caplog, stdout):
    """The guard is a safety net. One that cannot decide must not stand
    between the brain and a container that needs restarting, and it must not
    be blind quietly either."""
    caplog.set_level(logging.WARNING, logger=du.logger.name)
    fake = docker(_CP(0, stdout), _CP(0))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_OK
    assert len(fake.calls) == 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "State.StartedAt" in warnings[0]
    assert "recently-started guard" in warnings[0]


async def test_a_started_at_in_the_future_restarts_with_a_warning(docker, caplog):
    """The clock stepped back. Holding restarts until it catches up could
    block them for hours."""
    caplog.set_level(logging.WARNING, logger=du.logger.name)
    fake = docker(_CP(0, _inspect("running", -3600)), _CP(0))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_OK
    assert len(fake.calls) == 2
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "in the future" in warnings[0]


async def test_a_timezone_naive_started_at_is_read_as_utc(docker):
    """docker prints ``Z``. Anything without an offset is taken as UTC, the
    clock docker uses, rather than the brain container's local zone."""
    naive = (_NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S")
    docker(_CP(0, f"running {naive}\n"))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_RECENTLY_STARTED
    assert outcome.uptime_seconds == 30


async def test_a_broken_guard_restarts_instead_of_raising(docker, monkeypatch, caplog):
    """Never raises, and a guard bug must not turn into a wedged container
    nobody can restart."""
    caplog.set_level(logging.WARNING, logger=du.logger.name)

    async def boom(_pool=None):
        raise RuntimeError("settings exploded")

    monkeypatch.setattr(du, "docker_restart_min_uptime_seconds", boom)
    fake = docker(_CP(0, _inspect("running", 30)), _CP(0))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_OK
    assert len(fake.calls) == 2
    assert any(
        "guard failed" in r.getMessage() and "settings exploded" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    )


async def test_the_guard_reports_its_outcome_and_does_not_log_it(docker, caplog):
    """Callers differ on what a guarded restart means (a skip, a quiet log, an
    audit row), so the helper says nothing about it."""
    caplog.set_level(logging.DEBUG, logger=du.logger.name)
    docker(_CP(0, _inspect("running", 30)))

    outcome = await du.restart_container("c", pool=_settings_pool())

    assert outcome.status == du.RESTART_RECENTLY_STARTED
    assert not [r for r in caplog.records if r.name == du.logger.name]


async def test_a_missing_container_is_still_missing_not_recently_started(docker):
    """The two skips are different: nothing to restart vs. too soon to."""
    docker(_CP(1, stderr="Error: No such object: c\n"))

    outcome = await du.restart_container("c")

    assert outcome.status == du.RESTART_MISSING
    assert outcome.uptime_seconds is None


# ---------------------------------------------------------------------------
# Ratchet: the shared helper is the brain's only ``docker restart``
# ---------------------------------------------------------------------------

_BRAIN_DIR = Path(du.__file__).resolve().parent

# Until 2026-09-28 seven probes each ran their own ``docker restart`` behind a
# sync ``restart_fn`` seam, on hardcoded 30 s / 60 s timeouts and with no
# inspect first; this ratchet listed them until they moved over. None is left:
# every brain restart goes through ``docker_utils.restart_container``, and the
# probes' ``restart_fn`` seams are ``docker_utils.RestartFn``.


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


def test_no_other_brain_module_runs_its_own_docker_restart():
    extra = _modules_running_docker_restart() - {"docker_utils.py"}
    assert not extra, (
        f"{sorted(extra)} run their own `docker restart`. Call "
        "poindexter.brain.docker_utils.restart_container instead (a probe "
        "takes it as a docker_utils.RestartFn `restart_fn` seam): it inspects "
        "first, reads app_settings.brain_docker_restart_timeout_seconds and "
        "stays off the event loop."
    )


# ---------------------------------------------------------------------------
# Ratchet: every caller of the helper handles the recently-started outcome
# ---------------------------------------------------------------------------
#
# The guard adds a status no caller was written for. A caller's catch-all
# failure branch would page (``restart_service``, the ``health_probes`` self-heal,
# ``migration_drift_probe``), or record a failed action (the firefighter), for a
# restart the guard declined on purpose. So each one names the status and says
# what it means there, as each already does for ``RESTART_MISSING``.

# Names a brain module calls the helper by: the helper itself, the name
# ``health_probes`` binds it to, ``brain_daemon``'s wrapper for the firefighter
# and the console queue, and the ``restart_fn`` seam the probes take.
_RESTART_CALL_NAMES = {
    "restart_container", "_restart_container", "docker_restart_container", "restart_fn",
}


def _modules_calling_the_restart_helper() -> set[str]:
    """Brain modules, other than the helper, that call it or one of its seams."""
    found: set[str] = set()
    for path in sorted(_BRAIN_DIR.rglob("*.py")):
        if path.name == "docker_utils.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in _RESTART_CALL_NAMES:
                found.add(path.relative_to(_BRAIN_DIR).as_posix())
                break
    return found


def _modules_naming(status_constant: str) -> set[str]:
    """Brain modules that reference ``status_constant`` (not just in a comment)."""
    found: set[str] = set()
    for path in sorted(_BRAIN_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                (isinstance(node, ast.Attribute) and node.attr == status_constant)
                or (isinstance(node, ast.Name) and node.id == status_constant)
            ):
                found.add(path.relative_to(_BRAIN_DIR).as_posix())
                break
    return found


def test_the_caller_scan_sees_the_callers():
    """Positive control: a scan that found nothing would pass the ratchet."""
    found = _modules_calling_the_restart_helper()
    assert "brain_daemon.py" in found
    assert "health_probes.py" in found


def test_every_caller_of_the_helper_handles_the_recently_started_outcome():
    unhandled = _modules_calling_the_restart_helper() - _modules_naming(
        "RESTART_RECENTLY_STARTED"
    )
    assert not unhandled, (
        f"{sorted(unhandled)} restart a container through the shared helper but "
        "never name RESTART_RECENTLY_STARTED. That outcome falls into their "
        "catch-all failure branch: a page, or a failed action, for a restart the "
        "guard declined on purpose. Handle it the way the module handles "
        "RESTART_MISSING, and say what it means there in the "
        "'What a restart that was not attempted means' table in "
        "docs/operations/self-healing.md."
    )
