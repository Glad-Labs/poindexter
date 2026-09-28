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

#: ``docker inspect`` reads metadata; it only ever waits on dockerd itself.
DOCKER_INSPECT_TIMEOUT_SECONDS = 10

# ``ContainerRestart.status`` values.
#: ``docker restart`` exited 0.
RESTART_OK = "restarted"
#: ``docker inspect`` found no such container, so nothing was restarted.
RESTART_MISSING = "missing"
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

    @property
    def ok(self) -> bool:
        return self.status == RESTART_OK


class RestartFn(Protocol):
    """The shape of :func:`restart_container`.

    A probe that restarts a container takes a ``restart_fn`` of this shape so
    its tests can inject a stub, defaults it to :func:`restart_container`, and
    calls it as ``await restart_fn(container, pool=pool)``. The pool is what
    the timeout is read through.
    """

    def __call__(self, container: str, *, pool: Any = None) -> Awaitable[ContainerRestart]: ...


async def docker_restart_timeout_seconds(pool: Any = None) -> float:
    """Seconds a ``docker restart`` may run before the brain reports it failed.

    ``app_settings.brain_docker_restart_timeout_seconds`` when ``pool`` is
    given and the row holds a positive number, the default otherwise. A value
    that is set but unusable is a misconfiguration, so it is logged at WARNING
    rather than quietly replaced.
    """
    if pool is None:
        return DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS
    try:
        raw = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", DOCKER_RESTART_TIMEOUT_KEY,
        )
    except Exception as exc:
        logger.warning(
            "[docker_utils] could not read app_settings.%s (%s: %s) — using %ss",
            DOCKER_RESTART_TIMEOUT_KEY, type(exc).__name__, exc,
            DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS,
        )
        return DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS
    if raw is None or not str(raw).strip():
        return DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds <= 0:
        logger.warning(
            "[docker_utils] app_settings.%s=%r is not a positive number of "
            "seconds — using %ss",
            DOCKER_RESTART_TIMEOUT_KEY, raw, DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS,
        )
        return DOCKER_RESTART_TIMEOUT_DEFAULT_SECONDS
    return int(seconds) if seconds.is_integer() else seconds


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


async def restart_container(container: str, *, pool: Any = None) -> ContainerRestart:
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
    * **One timeout, from the database.** ``docker restart`` honours the
      container's own stop grace period (the worker's is 75 s) before it
      kills and starts it, so every caller waits
      ``app_settings.brain_docker_restart_timeout_seconds`` for it.
    * **Off the loop.** Both docker calls run in a worker thread. The brain
      is one event loop, and a 90 s wait on it would freeze every probe.

    Never raises: every failure comes back as a ``ContainerRestart`` that is
    not ``ok``. Logs and notifies nothing either: the callers differ on
    that (a notice, a page, or silence plus an audit row), so it stays theirs.
    """
    timeout = await docker_restart_timeout_seconds(pool)
    try:
        inspect = await asyncio.to_thread(
            _run_docker,
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
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
