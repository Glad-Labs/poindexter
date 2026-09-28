"""Stand-ins for ``docker_utils.restart_container`` in probe tests.

Every brain probe that restarts what it watches takes a ``restart_fn`` seam of
the shared helper's shape, ``async (container, *, pool) -> ContainerRestart``
(``docker_utils.RestartFn``), and awaits it. So a stub must be awaitable: a
sync ``lambda c: (True, "")`` left over from the old ``(ok, msg)`` seam fails
the moment a probe awaits it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from poindexter.brain import docker_utils as du

#: How long a stubbed ``RESTART_RECENTLY_STARTED`` container "has been running".
RECENT_UPTIME_SECONDS = 45


def outcome(
    container: str, status: str = du.RESTART_OK, detail: str | None = None,
) -> du.ContainerRestart:
    """What ``restart_container`` returns for ``container``.

    ``detail`` defaults to the helper's own wording for ``status``. A
    ``RESTART_RECENTLY_STARTED`` outcome carries the uptime the helper reports
    (``RECENT_UPTIME_SECONDS``).
    """
    if detail is None:
        detail = {
            du.RESTART_OK: f"restarted {container}",
            du.RESTART_MISSING: f"container {container} not found (likely mid-recreate)",
            du.RESTART_RECENTLY_STARTED: (
                f"{container} started {RECENT_UPTIME_SECONDS}s ago, inside the 120s "
                f"{du.DOCKER_RESTART_MIN_UPTIME_KEY} guard; not restarted"
            ),
            du.RESTART_FAILED: f"docker restart failed for {container}: permission denied",
            du.RESTART_TIMED_OUT: (
                f"docker restart {container} did not return within 90s "
                f"(app_settings.{du.DOCKER_RESTART_TIMEOUT_KEY}); dockerd may "
                f"still complete it"
            ),
            du.RESTART_NO_DOCKER_CLI: "docker CLI not available in brain container",
            du.RESTART_ERROR: (
                f"docker inspect {container} exited 1: failed to connect to the "
                f"docker API at unix:///var/run/docker.sock"
            ),
        }[status]
    return du.ContainerRestart(
        container, status, detail, 90,
        uptime_seconds=(
            float(RECENT_UPTIME_SECONDS) if status == du.RESTART_RECENTLY_STARTED else None
        ),
    )


def restart_stub(
    calls: list[str] | None = None,
    *,
    status: str = du.RESTART_OK,
    detail: str | None = None,
) -> AsyncMock:
    """An ``AsyncMock`` ``restart_fn`` answering ``status`` for any container.

    Each container it is asked to restart is appended to ``calls``. The mock
    records its own awaits too, for ``assert_awaited_once_with(container,
    pool=pool)``.
    """

    async def _restart(container: str, *, pool=None) -> du.ContainerRestart:
        if calls is not None:
            calls.append(container)
        return outcome(container, status, detail)

    return AsyncMock(side_effect=_restart)
