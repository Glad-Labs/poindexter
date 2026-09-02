"""Retention backlog registry — how a policy states whether it is keeping up.

poindexter#933, from the Glad-Labs/poindexter#2871 bug where
``retention.checkpoint_prune`` failed to prune ~20k rows **for months** while
reporting success on every run.

Every retention health signal we had was a *liveness* signal:

===================== ==========================================
signal                what it showed during the bug
===================== ==========================================
``last_error``        ``NULL`` — the handler never errored
``last_run_at``       current — ran every 6h on schedule
``total_deleted``     15,807 — non-zero, looked healthy
``last_run_deleted``  ``0`` — indistinguishable from "nothing to do"
Grafana panel         all green
===================== ==========================================

The policy did exactly what it was told, and what it was told was wrong. **A
misconfigured policy and an idle one produce byte-identical telemetry**, and
since ~20 of the enabled policies legitimately sit at ``deleted=0`` most runs,
``deleted=0`` can never itself be the alarm.

The missing signal is *correctness*: how many rows should this policy have
removed and hasn't? A correct policy drains to ~0 backlog; a broken one
accumulates.

Measure the invariant, not the policy's own predicate
-----------------------------------------------------

This is the part worth getting right, and it is why the backlog expression
lives with the **handler** rather than being reverse-engineered per handler
type by the probe.

For :mod:`~services.integrations.handlers.retention_ttl_prune` the policy row
*is* the invariant — "rows older than ``ttl_days`` should be gone" — so
counting what its own WHERE clause matches is exactly right.

For ``checkpoint_prune`` it is not. That handler's bug lived in its
``terminal_statuses`` config, so a backlog computed from the policy's own
predicate would have returned **0 during the outage** and caught nothing. Its
backlog expression therefore encodes what *should* be true independent of the
policy's configuration, and the two disagreeing is the signal.

So: the handler enforces the policy; the backlog expression states the
invariant. Where a handler cannot state one, it is reported **unmonitored** —
never as zero. A policy silently exempt from the correctness check would
recreate the exact blind spot this module exists to close.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacklogQuery:
    """A single-value ``SELECT`` returning the overdue row count."""

    sql: str
    params: tuple[Any, ...] = ()


@dataclass(frozen=True)
class BacklogResult:
    """One policy's backlog reading.

    ``count`` is ``None`` whenever the number is not known — either the
    handler declares no backlog expression (``status='unmonitored'``) or the
    query failed (``status='error'``). Callers must never coerce that to 0.
    """

    policy: str
    handler: str
    count: int | None
    status: str  # measured | unmonitored | error
    detail: str = ""


BacklogBuilder = Callable[[Mapping[str, Any]], BacklogQuery | None]

_BACKLOG_REGISTRY: dict[str, BacklogBuilder] = {}


class BacklogRegistrationError(RuntimeError):
    """Raised when two modules claim the same handler's backlog expression."""


def register_backlog(handler_name: str) -> Callable[[BacklogBuilder], BacklogBuilder]:
    """Decorator — register ``handler_name``'s backlog expression.

    Mirrors ``registry.register_handler``: duplicate registration is a hard
    error, because the framework imports every handler module at startup and a
    duplicate means two modules claimed the same name. Failing loudly beats a
    silent override that would make the probe measure the wrong invariant.
    """
    if not handler_name or "." in handler_name:
        raise BacklogRegistrationError(
            f"handler_name must be non-empty and dot-free; got {handler_name!r}"
        )

    def decorator(fn: BacklogBuilder) -> BacklogBuilder:
        existing = _BACKLOG_REGISTRY.get(handler_name)
        if existing is not None and existing is not fn:
            raise BacklogRegistrationError(
                f"backlog expression for {handler_name!r} already registered by "
                f"{existing.__module__}.{existing.__qualname__}"
            )
        _BACKLOG_REGISTRY[handler_name] = fn
        return fn

    return decorator


def handlers_with_backlog() -> frozenset[str]:
    """Handler names that can state whether they are keeping up."""
    return frozenset(_BACKLOG_REGISTRY)


def build_backlog_query(
    handler_name: str, row: Mapping[str, Any]
) -> BacklogQuery | None:
    """Build ``row``'s backlog query, or ``None`` if the handler declares none."""
    builder = _BACKLOG_REGISTRY.get(handler_name)
    if builder is None:
        return None
    return builder(row)


async def measure_backlog(pool: Any, row: Mapping[str, Any]) -> BacklogResult:
    """Measure one policy's overdue backlog.

    Never raises: a probe must not crash a cycle, and a failed measurement is
    reported as ``error`` rather than folded into a healthy zero.
    """
    policy = str(row.get("name") or "<unnamed>")
    handler = str(row.get("handler_name") or "<none>")

    try:
        query = build_backlog_query(handler, row)
    except Exception as exc:  # noqa: BLE001 — a bad row must not stop the sweep
        return BacklogResult(
            policy, handler, None, "error", f"query build failed: {exc}"
        )

    if query is None:
        return BacklogResult(
            policy, handler, None, "unmonitored",
            f"handler {handler!r} declares no backlog expression",
        )

    try:
        async with pool.acquire() as conn:
            value = await conn.fetchval(query.sql, *query.params)
    except Exception as exc:  # noqa: BLE001 — see docstring
        logger.warning(
            "[retention_backlog] %s (%s): measurement failed: %s",
            policy, handler, exc,
        )
        return BacklogResult(policy, handler, None, "error", str(exc))

    return BacklogResult(policy, handler, int(value or 0), "measured")


async def measure_all(pool: Any, rows: list[Mapping[str, Any]]) -> list[BacklogResult]:
    """Measure every policy in ``rows``, preserving order."""
    return [await measure_backlog(pool, row) for row in rows]
