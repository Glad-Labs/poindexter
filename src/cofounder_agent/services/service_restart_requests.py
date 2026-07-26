"""Operator-triggered container restart — the intent queue's SQL-owning seam.

The worker has no docker.sock (only ``poindexter-brain-daemon`` does — see
``brain/brain_daemon.py::docker_restart_container``), so a restart click can't
act directly. This module writes/reads ``service_restart_requests`` rows;
``brain/service_restart.py`` claims and executes them on its own poll loop.
Route: ``routes/service_restart_routes.py`` (poindexter#909).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

# Every poindexter-managed container is named by the compose ``container_name:``
# convention. This is a SHAPE check only (defense against garbage/injection in
# the path param) — whether the name is a REAL, restartable container is
# brain's call at claim time (docker inspect), since the worker can't see
# docker.sock to check itself.
_CONTAINER_NAME_RE = re.compile(r"^poindexter-[a-z0-9]+(?:-[a-z0-9]+)*$")

# Two containers cannot be restarted THROUGH THIS MECHANISM, because each one
# destroys the mechanism mid-flight and so can never reach a terminal row:
#
# - `poindexter-brain-daemon` runs `poll_and_execute_restart_requests` itself.
#   It marks the row `claimed` in one transaction, then calls
#   `docker_restart_container` — which kills this very process before the
#   `status='done'` write executes.
# - `poindexter-postgres-local` HOLDS the queue. The terminal UPDATE would
#   race the database's own shutdown.
#
# Either way the row strands in `claimed` forever: the claim query only selects
# `status='pending'`, so nothing ever reclaims it, and the operator gets the
# console's honest-but-useless "still in progress". Refusing up front with a
# remediation string beats queueing an intent that is guaranteed to strand
# (`feedback_no_silent_defaults`). Both remain restartable by hand — that is
# what the error tells the operator to do. #2505 called this allowlist
# load-bearing and scoped out brain self-restart explicitly.
_SELF_DEFEATING_CONTAINERS = frozenset({
    "poindexter-brain-daemon",
    "poindexter-postgres-local",
})


class InvalidContainerName(ValueError):
    """Raised when a container name fails the shape check."""


class SelfDefeatingRestart(ValueError):
    """Raised for a container whose restart would destroy the queue recording it.

    Distinct from :class:`InvalidContainerName`: the name is well-formed and the
    container is real — the request just can't be serviced by this path.
    """


def is_valid_container_name(container: str) -> bool:
    return bool(_CONTAINER_NAME_RE.match(container or ""))


def is_self_defeating(container: str) -> bool:
    """True when restarting ``container`` would orphan its own intent row."""
    return (container or "") in _SELF_DEFEATING_CONTAINERS


async def create_restart_request(
    pool: Any, container: str, *, requested_by: str = "console"
) -> dict[str, Any]:
    """Queue a restart intent.

    Raises :class:`InvalidContainerName` on a malformed name (route → 400) and
    :class:`SelfDefeatingRestart` for a container that would orphan its own row
    (route → 409). Never a silent no-op.
    """
    if not is_valid_container_name(container):
        raise InvalidContainerName(container)
    if is_self_defeating(container):
        raise SelfDefeatingRestart(container)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO service_restart_requests (container, requested_by)
            VALUES ($1, $2)
            RETURNING id, container, status, requested_at
            """,
            container,
            requested_by,
        )
    return dict(row)


async def get_restart_request(pool: Any, request_id: str) -> dict[str, Any] | None:
    """Read one request's current status — the console polls this for
    closed-loop restart feedback instead of trusting an optimistic UI state.
    A malformed (non-UUID) id returns ``None`` — same as "not found" — rather
    than raising, so the route always maps it to a clean 404."""
    try:
        request_uuid = uuid.UUID(str(request_id))
    except (ValueError, AttributeError, TypeError):
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, container, status, requested_by, detail,
                   requested_at, claimed_at, completed_at
            FROM service_restart_requests
            WHERE id = $1
            """,
            request_uuid,
        )
    return dict(row) if row else None
