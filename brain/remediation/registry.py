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


# Containers the firefighter must NEVER auto-restart, whoever picked the action
# — a deterministic rule or the LLM long-tail. Deliberately NOT operator-
# configurable, and deliberately a union floor under the tunable denylist below:
# these are correctness invariants, not policy, and the same two names the
# console path refuses at enqueue (`services/service_restart_requests.py`).
#
# Each destroys the machinery recording its own outcome:
#   - poindexter-brain-daemon RUNS this executor. Restarting it kills the
#     process between the `remediation_action` row and the verify row, so the
#     run can never reach a terminal state.
#   - poindexter-postgres-local HOLDS audit_log. The outcome write races the
#     database's own shutdown — and a restart mid-transaction risks data loss,
#     which is why `brain/docker_port_forward_probe.py` already refuses it under
#     `db_recovery_policy`.
#
# Measured need (poindexter#1026): replaying the real
# `docker_port_forward_restart_skipped` alert — whose own annotation says the
# restart was skipped BECAUSE it is a database container — both the outgoing
# llama3.2:3b default (4/5 runs) and granite4.2:3b (1/5) picked
# restart_container on poindexter-postgres-local at confidence ABOVE
# ops_firefighter_min_confidence, so the gate would not have stopped them.
# Model quality moves that rate; it cannot make it zero. This does.
_NEVER_RESTART: frozenset[str] = frozenset({
    "poindexter-postgres-local",
    "poindexter-brain-daemon",
})

# Operator ADDITIONS to the floor above (CSV). Union, never replacement: an
# empty or malformed value must never be able to re-open the two invariants.
_DENYLIST_SETTING = "ops_firefighter_restart_denylist"


async def _restart_denylist(pool: Any) -> frozenset[str]:
    """``_NEVER_RESTART`` plus any operator additions.

    Fails CLOSED by construction: ``rules._read_str`` already swallows read
    errors and returns the default, and the result is unioned onto the hardcoded
    floor — so a missing key, an unreachable DB, or a garbage value all leave
    the two invariants denied.
    """
    try:
        from brain.remediation import rules as _rules
        raw = await _rules._read_str(pool, _DENYLIST_SETTING, "")
    except Exception:  # noqa: BLE001 — guard must never fail open
        return _NEVER_RESTART
    extra = {part.strip() for part in str(raw or "").split(",") if part.strip()}
    return _NEVER_RESTART | extra


async def _restart_container(params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Docker-restart a named container via brain_daemon.docker_restart_container."""
    container = str(params.get("container") or "").strip()
    if not container:
        return ActionResult(status="skipped", detail="restart_container: no 'container' param")
    denied = await _restart_denylist(ctx.pool)
    if container in denied:
        # `skipped` (not `failed`) is still a non-ok status, and the engine pages
        # on anything that is not "ok" — which is the point: refuse the action
        # AND surface the alert to a human, rather than silently doing nothing.
        return ActionResult(
            status="skipped",
            detail=(
                f"restart_container: {container} is on the firefighter restart "
                "denylist (restarting it would destroy the record of this very "
                "action, and for the database also risks data loss). Paging "
                "instead; restart it by hand if that is genuinely what is needed."
            ),
        )
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


# Human/LLM-facing metadata, kept parallel to ACTION_REGISTRY (not merged into
# it, so the executor stays a plain callable). ``describe_catalog()`` is the
# ONLY description of the actions the brain sends the LLM selector; the model
# picks a name from here and the engine re-validates that pick against
# ACTION_REGISTRY before executing — the model's output is never trusted.
_ACTION_META: dict[str, dict[str, Any]] = {
    "restart_container": {
        "description": (
            "Restart a single docker container by name. Idempotent and "
            "blast-radius-bounded — for a wedged or unresponsive service. "
            "NEVER choose this for the database (poindexter-postgres-local) or "
            "the brain daemon (poindexter-brain-daemon), and never when the "
            "alert says a restart was already capped, skipped by policy, or "
            "should be investigated by hand — abstain in those cases."
        ),
        "params_schema": {
            "container": "str (required) — container name, e.g. 'poindexter-pyroscope'",
        },
    },
    "run_auto_remediate": {
        "description": (
            "Run the brain's stuck-task / stale-approval cleanup sweep "
            "(resets orphaned pipeline rows, clears poisoned checkpoints). No params."
        ),
        "params_schema": {},
    },
}


def describe_catalog(allowlist: list[str] | None = None) -> list[dict[str, Any]]:
    """The action catalog the brain hands the LLM selector.

    Returns ``[{name, description, params_schema}]`` for every registered
    action, in registry order. A non-empty ``allowlist`` restricts the catalog
    to those names — mirroring ``ops_firefighter_action_allowlist`` semantics
    where an empty/absent list means "all registered actions". A name in the
    allowlist that isn't registered is ignored: you can only ever offer an
    action that actually executes.
    """
    allowed = set(allowlist) if allowlist else None
    catalog: list[dict[str, Any]] = []
    for name in ACTION_REGISTRY:
        if allowed is not None and name not in allowed:
            continue
        meta = _ACTION_META.get(name, {})
        catalog.append(
            {
                "name": name,
                "description": str(meta.get("description", "")),
                "params_schema": dict(meta.get("params_schema", {})),
            }
        )
    return catalog


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
