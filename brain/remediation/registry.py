"""Action registry — the single seam through which every remediation action
runs. Executors wrap primitives the brain already owns. They MUST be
idempotent, reversible, and blast-radius-bounded, and MUST NOT raise into the
caller — return an ActionResult(status="failed", ...) instead.

Brain-image isolation: this module resolves brain_daemon lazily (flat OR
package path) exactly like alert_dispatcher, and imports nothing from services/.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ActionResult:
    status: str  # "ok" | "failed" | "skipped"
    detail: str = ""
    latency_ms: int = 0


@dataclass
class RemediationContext:
    pool: Any
    alert: dict[str, Any]
    logger: Any


Executor = Callable[[dict[str, Any], RemediationContext], Awaitable[ActionResult]]


def _resolve_brain_daemon() -> Any | None:
    """Find brain.brain_daemon across the flat / package import paths.

    Mirrors alert_dispatcher._resolve_brain_daemon_module so the registry never
    hard-imports the daemon at module load (avoids import cycles + keeps the
    module importable in unit tests that don't load the daemon).
    """
    mod = sys.modules.get("brain_daemon") or sys.modules.get("brain.brain_daemon")
    if mod is not None:
        return mod
    try:
        import brain_daemon as mod  # type: ignore
        return mod
    except ImportError:
        try:
            from brain import brain_daemon as mod  # type: ignore
            return mod
        except ImportError:
            return None


async def _restart_container(params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Docker-restart a named container via brain_daemon.docker_restart_container."""
    container = str(params.get("container") or "").strip()
    if not container:
        return ActionResult(status="skipped", detail="restart_container: no 'container' param")
    mod = _resolve_brain_daemon()
    if mod is None or not hasattr(mod, "docker_restart_container"):
        return ActionResult(status="failed", detail="brain_daemon.docker_restart_container unavailable")
    started = time.monotonic()
    ok, detail = await mod.docker_restart_container(container, pool=ctx.pool)
    latency = int((time.monotonic() - started) * 1000)
    return ActionResult(status="ok" if ok else "failed", detail=detail, latency_ms=latency)


async def _run_auto_remediate(params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Run the brain's stuck-task / stale-approval cleanup sweep."""
    mod = _resolve_brain_daemon()
    if mod is None or not hasattr(mod, "auto_remediate"):
        return ActionResult(status="failed", detail="brain_daemon.auto_remediate unavailable")
    started = time.monotonic()
    try:
        await mod.auto_remediate(ctx.pool)
    except Exception as e:  # noqa: BLE001 — executors never raise into the loop
        return ActionResult(
            status="failed",
            detail=f"auto_remediate raised: {e}"[:400],
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    return ActionResult(
        status="ok", detail="auto_remediate completed",
        latency_ms=int((time.monotonic() - started) * 1000),
    )


ACTION_REGISTRY: dict[str, Executor] = {
    "restart_container": _restart_container,
    "run_auto_remediate": _run_auto_remediate,
}


async def execute(action_name: str, params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Run a registered action. Unknown name -> skipped. Executor blow-up -> failed.

    Never raises: the poll loop is best-effort.
    """
    executor = ACTION_REGISTRY.get(action_name)
    if executor is None:
        return ActionResult(status="skipped", detail=f"unknown action: {action_name}")
    try:
        return await executor(params or {}, ctx)
    except Exception as e:  # noqa: BLE001
        return ActionResult(status="failed", detail=f"executor raised: {e}"[:400])
