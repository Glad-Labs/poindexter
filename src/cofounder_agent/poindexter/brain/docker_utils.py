"""docker_utils — tiny helpers for brain code running inside a container.

Keeping these out of health_probes / brain_daemon so they can be reused
by other brain modules (seed_loader, future phase-2 orchestration work)
without creating circular imports. That matters for :func:`restart_container`
in particular: ``brain_daemon`` imports ``health_probes``, so a restart
helper living in ``brain_daemon`` could never be shared with it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import subprocess
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


def _detect_docker() -> bool:
    """True when running inside a Docker container.

    Uses two signals: the IN_DOCKER env var (set in docker-compose.local.yml)
    and /.dockerenv, which Docker creates on every container. Either alone
    is sufficient; checking both makes host-side pytest and tools that
    don't set the env var still behave correctly.
    """
    if os.getenv("IN_DOCKER", "").lower() in ("1", "true", "yes"):
        return True
    return Path("/.dockerenv").exists()


IN_DOCKER = _detect_docker()


def localize_url(url: str) -> str:
    """Rewrite host-side URLs so they work from inside a container.

    `app_settings` holds canonical URLs that work from the host (e.g.
    `http://localhost:3000` for Grafana). Inside a container, `localhost`
    loops back to the container itself, which isn't what's wanted —
    every port the host exposes is reachable at `host.docker.internal`
    instead, on the same port number.

    This function rewrites only the hostname; the port is preserved so
    the translation stays in sync with whatever docker-compose decides
    to expose.

    No-op when not running inside Docker.
    """
    if not url or not IN_DOCKER:
        return url
    return (
        url.replace("://localhost:", "://host.docker.internal:")
           .replace("://127.0.0.1:", "://host.docker.internal:")
    )


async def resolve_url(
    pool_or_conn,
    *app_setting_keys: str,
    default: str = "",
    env_var: str | None = None,
) -> str:
    """Resolve a service URL with DB-first config + in-container translation.

    Precedence:
      1. If ``env_var`` is passed and that env var is non-empty, its value
         wins verbatim (no localize — env is assumed container-aware already).
      2. First non-empty value from the given ``app_setting_keys`` (tried in
         order), with ``localize_url()`` applied.
      3. ``default``, with ``localize_url()`` applied.

    Accepts either an ``asyncpg.Pool`` or an ``asyncpg.Connection``; both
    expose ``fetchval``.

    This is the single pattern that was duplicated across
    ``scripts/auto-embed.py``, ``brain/health_probes.py``, and
    ``brain/business_probes.py`` before 2026-04-18. Concentrating it here
    makes the "brain reports everything DOWN because localhost resolves
    to itself" class of bug impossible in new code — every caller just
    asks for the resolved URL and trusts it.
    """
    if env_var:
        env_val = os.getenv(env_var)
        if env_val:
            return env_val
    try:
        for key in app_setting_keys:
            val = await pool_or_conn.fetchval(
                "SELECT value FROM app_settings WHERE key = $1", key
            )
            if val:
                return localize_url(val)
    except Exception as e:
        logger.warning(
            "resolve_url: app_settings lookup failed for keys %s: %s — using default",
            app_setting_keys, e,
        )
    return localize_url(default)


# ---------------------------------------------------------------------------
# docker restart — the implementation the brain's restart paths share
# ---------------------------------------------------------------------------

#: ``app_settings`` key bounding the ``docker restart`` subprocess.
DOCKER_RESTART_TIMEOUT_KEY = "brain_docker_restart_timeout_seconds"

#: Used when the key is absent, unreadable, or not a positive number. It must
#: stay above the longest stop grace period of any container the brain
#: restarts: ``docker restart`` waits that long for a graceful stop before it
#: kills the container and starts it again, and the worker's
#: ``stop_grace_period`` is 75 s (docker-compose.local.yml). A shorter timeout
#: reports a failure, and pages, while dockerd goes on to finish the restart.
DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS = 90

#: ``app_settings`` key for how long a container must have been running before
#: the brain restarts it (again). ``0`` turns the recently-started guard off.
DOCKER_RESTART_MIN_UPTIME_KEY = "brain_docker_restart_min_uptime_seconds"

#: Used when the key is absent, unreadable, or not a non-negative number. It
#: has to outlast a worker restart, which takes 40-90 s to come back, and it is
#: the same 120 s as the brain's own boot allowance, ``brain_boot_grace_seconds``.
#: Keep it under the 300 s brain cycle, or a restart the brain made itself would
#: also hold off the next cycle's heal.
DOCKER_RESTART_MIN_UPTIME_DEFAULT_SECONDS = 120

#: ``docker inspect`` reads metadata; it only ever waits on dockerd itself.
DOCKER_INSPECT_TIMEOUT_SECONDS = 10

#: What the pre-check asks ``docker inspect`` for: the run state and when the
#: container last started, e.g. ``running 2026-09-28T19:12:24.123456789Z``. One
#: short line, so there is no JSON to parse and nothing to bloat the brain's logs.
_INSPECT_FORMAT = "{{.State.Status}} {{.State.StartedAt}}"

# ``ContainerRestart.status`` values.
#: ``docker restart`` exited 0.
RESTART_OK = "restarted"
#: ``docker inspect`` found no such container, so nothing was restarted.
RESTART_MISSING = "missing"
#: The container has been running for less than
#: ``app_settings.brain_docker_restart_min_uptime_seconds``, so it was not
#: restarted: a second restart would land while it is still starting up.
RESTART_RECENTLY_STARTED = "recently_started"
#: ``docker restart`` ran and exited non-zero.
RESTART_FAILED = "failed"
#: ``docker restart`` outlived the timeout. dockerd may still finish the job.
RESTART_TIMED_OUT = "timed_out"
#: No ``docker`` binary on PATH.
RESTART_NO_DOCKER_CLI = "no_docker_cli"
#: Anything else: dockerd unreachable, ``docker inspect`` hung or failed, ...
RESTART_ERROR = "error"

# How docker words an unknown name: "Error: No such object: x" (CLI 27, which
# the brain image ships), "Error: no such object: x" (CLI 29), "No such
# container: x" (``docker restart``). Only that counts as absent. A dead or
# unreadable socket makes ``docker inspect`` exit 1 too, and that is a failure
# to report, not a recreate window to wait out.
_NO_SUCH_CONTAINER_RE = re.compile(r"no such (object|container)", re.IGNORECASE)


@dataclass(frozen=True)
class ContainerRestart:
    """What one :func:`restart_container` call did."""

    container: str
    #: One of the ``RESTART_*`` values above.
    status: str
    #: One line naming the container and the outcome, safe to log, store or
    #: page with. The firefighter records it verbatim in ``audit_log``.
    detail: str
    #: The ``docker restart`` timeout this attempt used, in seconds.
    timeout_seconds: float
    #: docker's stderr, for a non-zero exit (``RESTART_FAILED``, and a failed
    #: inspect's ``RESTART_MISSING`` / ``RESTART_ERROR``).
    stderr: str = ""
    #: The exception text when docker could not be run at all
    #: (``RESTART_NO_DOCKER_CLI``, or a ``RESTART_ERROR`` that raised).
    error: str = ""
    #: How long the container had been running, in seconds, for
    #: ``RESTART_RECENTLY_STARTED``. None for every other status.
    uptime_seconds: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == RESTART_OK


class RestartFn(Protocol):
    """The shape of :func:`restart_container`.

    A probe that restarts a container takes a ``restart_fn`` of this shape so
    its tests can inject a stub, defaults it to :func:`restart_container`, and
    calls it as ``await restart_fn(container, pool=pool)``. The pool is what
    the timeout and the recently-started window are read through. There is no
    ``force`` here: a probe is automated, so it always gets the guard.
    """

    def __call__(self, container: str, *, pool: Any = None) -> Awaitable[ContainerRestart]: ...


async def _seconds_setting(pool: Any, key: str, default: int, *, allow_zero: bool) -> float:
    """``app_settings.<key>`` as a number of seconds, or ``default``.

    ``default`` when ``pool`` is None or the row is absent or blank. A value
    that is set but unusable (not a number, negative, or zero where zero is not
    allowed) is a misconfiguration, so it is logged at WARNING rather than
    quietly replaced.
    """
    if pool is None:
        return default
    try:
        raw = await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
    except Exception as exc:
        logger.warning(
            "[docker_utils] could not read app_settings.%s (%s: %s) — using %ss",
            key, type(exc).__name__, exc, default,
        )
        return default
    if raw is None or not str(raw).strip():
        return default
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0 or (seconds == 0 and not allow_zero):
        logger.warning(
            "[docker_utils] app_settings.%s=%r is not %s number of seconds — using %ss",
            key, raw, "a non-negative" if allow_zero else "a positive", default,
        )
        return default
    return int(seconds) if seconds.is_integer() else seconds


async def docker_restart_timeout_seconds(pool: Any = None) -> float:
    """Seconds a ``docker restart`` may run before the brain reports it failed.

    ``app_settings.brain_docker_restart_timeout_seconds`` when ``pool`` is
    given and the row holds a positive number, the default otherwise. A value
    that is set but unusable is a misconfiguration, so it is logged at WARNING
    rather than quietly replaced.
    """
    return await _seconds_setting(
        pool, DOCKER_RESTART_TIMEOUT_KEY, DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS,
        allow_zero=False,
    )


async def docker_restart_min_uptime_seconds(pool: Any = None) -> float:
    """Seconds a container must have been running before the brain restarts it.

    ``app_settings.brain_docker_restart_min_uptime_seconds`` when ``pool`` is
    given and the row holds a number of zero or more, the default otherwise.
    ``0`` turns the recently-started guard off. A value that is set but
    unusable is logged at WARNING rather than quietly replaced.
    """
    return await _seconds_setting(
        pool, DOCKER_RESTART_MIN_UPTIME_KEY, DOCKER_RESTART_MIN_UPTIME_DEFAULT_SECONDS,
        allow_zero=True,
    )


def _run_docker(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """One docker CLI call with its output captured as text.

    Blocking, so it only ever runs through ``asyncio.to_thread``.
    """
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout,
        # CREATE_NO_WINDOW: no console flashing up on a Windows host. Must be
        # 0 anywhere else.
        creationflags=0x08000000 if os.name == "nt" else 0,
    )


def _could_not_run(container: str, step: str, timeout: float, exc: Exception) -> ContainerRestart:
    """The outcome when ``docker <step>`` raised instead of exiting."""
    if isinstance(exc, FileNotFoundError):
        return ContainerRestart(
            container, RESTART_NO_DOCKER_CLI,
            "docker CLI not available in brain container", timeout, error=str(exc),
        )
    return ContainerRestart(
        container, RESTART_ERROR,
        f"docker {step} error for {container}: {exc}"[:200], timeout, error=str(exc),
    )


def _utcnow() -> datetime:
    """The clock the recently-started guard reads. A function so tests can pin it."""
    return datetime.now(UTC)


def _parse_docker_timestamp(raw: str) -> datetime | None:
    """docker's RFC 3339 timestamp (``2026-09-28T19:12:24.123456789Z``), or None."""
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def _recently_started(
    container: str, inspect_stdout: str, pool: Any, timeout: float,
) -> ContainerRestart | None:
    """``RESTART_RECENTLY_STARTED`` when the pre-check finds ``container`` inside the window.

    None means go ahead: the container is not running (a created, exited or
    restarting container needs the restart), the guard is off, or it has been
    up long enough. A ``StartedAt`` that cannot be read, or that is in the
    future because the clock stepped, also means go ahead, with a WARNING. The
    guard is a safety net, so a guard that cannot decide must not stand between
    the brain and a container that needs restarting.
    """
    parts = (inspect_stdout or "").split()
    if not parts or parts[0] != "running":
        return None
    started = _parse_docker_timestamp(parts[1]) if len(parts) > 1 else None
    if started is None:
        logger.warning(
            "[docker_utils] could not read State.StartedAt for %s from docker "
            "inspect output %r — restarting without the recently-started guard",
            container, (inspect_stdout or "").strip()[:80],
        )
        return None
    min_uptime = await docker_restart_min_uptime_seconds(pool)
    if min_uptime <= 0:
        return None
    uptime = (_utcnow() - started).total_seconds()
    if uptime < 0:
        logger.warning(
            "[docker_utils] %s's State.StartedAt (%s) is in the future, so the "
            "clock has stepped — restarting without the recently-started guard",
            container, parts[1],
        )
        return None
    if uptime >= min_uptime:
        return None
    return ContainerRestart(
        container, RESTART_RECENTLY_STARTED,
        f"{container} started {uptime:.0f}s ago, inside the {min_uptime:g}s "
        f"{DOCKER_RESTART_MIN_UPTIME_KEY} guard; not restarted",
        timeout, uptime_seconds=uptime,
    )


async def restart_container(
    container: str, *, pool: Any = None, force: bool = False,
) -> ContainerRestart:
    """``docker inspect`` then ``docker restart`` one container, off the event loop.

    Every ``docker restart`` the brain runs goes through this:
    ``brain_daemon.restart_service`` (``monitor_services``' heal),
    ``brain_daemon.docker_restart_container`` (the firefighter's
    ``restart_container`` action and console restart requests),
    ``health_probes``' ``REMEDIATIONS`` self-heal, and the probes that restart
    what they watch (``migration_drift_probe``, ``backup_watcher``,
    ``offsite_backup_watch``, ``auto_embed_watch``, ``postiz_queue_watch``,
    ``docker_port_forward_probe``, ``sidecar_ram_watch``,
    ``comfyui_ram_watch``), each through a :class:`RestartFn` seam. A ratchet
    in ``tests/unit/brain/test_docker_utils_restart.py`` fails if any other
    brain module shells out ``docker restart`` itself.

    * **Inspect first.** ``docker compose up --force-recreate`` leaves the
      name unbound for a second or two between removing the old container
      and creating the new one. A restart aimed into that window fails with
      "No such container" about a container that is being replaced anyway, so
      absence returns ``RESTART_MISSING`` without restarting and each caller
      decides what that means for it.
    * **Not straight after a start.** Several of those paths can restart the
      same container, usually the worker, within one brain cycle, and
      deploy-sync and compose restart it too. The worker takes 40-90 s to come
      back, so a second restart lands mid-startup and kills it. The same
      inspect reads docker's own ``State.StartedAt``, and a container that has
      been ``running`` for less than
      ``app_settings.brain_docker_restart_min_uptime_seconds`` (120; ``0`` turns
      this off) returns ``RESTART_RECENTLY_STARTED`` without being restarted,
      with its ``uptime_seconds``. Docker's timestamp counts every restart,
      whoever made it, which a record of the brain's own restarts could not.
      ``force=True`` skips the guard, for an explicit operator restart; an
      automated caller never sets it, and the ``RestartFn`` seam does not carry
      it. Each caller decides what a guarded restart means for it.
    * **One timeout, from the database.** ``docker restart`` honours the
      container's own stop grace period (the worker's is 75 s) before it
      kills and starts it, so every caller waits
      ``app_settings.brain_docker_restart_timeout_seconds`` for it.
    * **Off the loop.** Both docker calls run in a worker thread. The brain
      is one event loop, and a 90 s wait on it would freeze every probe.

    Never raises: every failure comes back as a ``ContainerRestart`` that is
    not ``ok``. Reports no outcome either, by log or notice: the callers differ
    on that (a notice, a page, or silence plus an audit row), so it stays
    theirs. It logs only what it could not read or run: a setting, a timestamp,
    or the guard itself.
    """
    timeout = await docker_restart_timeout_seconds(pool)
    try:
        inspect = await asyncio.to_thread(
            _run_docker,
            ["docker", "inspect", "--format", _INSPECT_FORMAT, container],
            DOCKER_INSPECT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — never raises; the outcome carries it
        return _could_not_run(container, "inspect", timeout, exc)
    if inspect.returncode != 0:
        stderr = (inspect.stderr or "").strip()
        if _NO_SUCH_CONTAINER_RE.search(stderr):
            return ContainerRestart(
                container, RESTART_MISSING,
                f"container {container} not found (likely mid-recreate)", timeout,
                stderr=stderr,
            )
        return ContainerRestart(
            container, RESTART_ERROR,
            f"docker inspect {container} exited {inspect.returncode}: {stderr[:160]}",
            timeout, stderr=stderr,
        )
    if not force:
        try:
            recent = await _recently_started(container, inspect.stdout, pool, timeout)
        except Exception as exc:  # noqa: BLE001 — never raises; a broken guard must not block the restart
            logger.warning(
                "[docker_utils] recently-started guard failed for %s (%s: %s) — "
                "restarting without it", container, type(exc).__name__, exc,
            )
            recent = None
        if recent is not None:
            return recent
    try:
        result = await asyncio.to_thread(_run_docker, ["docker", "restart", container], timeout)
    except subprocess.TimeoutExpired:
        return ContainerRestart(
            container, RESTART_TIMED_OUT,
            f"docker restart {container} did not return within {timeout}s "
            f"(app_settings.{DOCKER_RESTART_TIMEOUT_KEY}); dockerd may still complete it",
            timeout,
        )
    except Exception as exc:  # noqa: BLE001 — never raises; the outcome carries it
        return _could_not_run(container, "restart", timeout, exc)
    if result.returncode == 0:
        return ContainerRestart(container, RESTART_OK, f"restarted {container}", timeout)
    stderr = (result.stderr or "").strip()
    return ContainerRestart(
        container, RESTART_FAILED,
        f"docker restart failed for {container}: {stderr[:200]}", timeout, stderr=stderr,
    )
