"""Action registry — the single seam through which every remediation action
runs. Executors wrap primitives the brain already owns. They MUST be
idempotent, reversible, and blast-radius-bounded, and MUST NOT raise into the
caller — return an ActionResult(status="failed", ...) instead.

Brain-image isolation: this module resolves brain_daemon lazily (flat OR
package path) exactly like alert_dispatcher, and imports nothing from services/.
``host_services`` (the host-service allowlist behind ``restart_host_service``)
imports ``health_probes`` and ``docker_utils``, both brain modules with no
import back into the remediation package.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from poindexter.brain import docker_utils
from poindexter.brain.remediation import host_services


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
    """Return the ``poindexter.brain.brain_daemon`` module, or None if it won't import.

    Mirrors alert_dispatcher._resolve_brain_daemon_module so the registry never
    hard-imports the daemon at module load (avoids import cycles + keeps the
    module importable in unit tests that don't load the daemon).
    """
    mod = sys.modules.get("poindexter.brain.brain_daemon")
    if mod is not None:
        return mod
    try:
        from poindexter.brain import brain_daemon as mod  # type: ignore
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
        from poindexter.brain.remediation import rules as _rules
        raw = await _rules._read_str(pool, _DENYLIST_SETTING, "")
    except Exception:  # noqa: BLE001 — guard must never fail open
        return _NEVER_RESTART
    extra = {part.strip() for part in str(raw or "").split(",") if part.strip()}
    return _NEVER_RESTART | extra


async def _restart_container_refusal(params: dict[str, Any], ctx: RemediationContext) -> str | None:
    """Why ``restart_container`` would refuse these params, or None if it would run."""
    container = str(params.get("container") or "").strip()
    if not container:
        return "restart_container: no 'container' param"
    if container in await _restart_denylist(ctx.pool):
        return (
            f"restart_container: {container} is on the firefighter restart "
            "denylist (restarting it would destroy the record of this very "
            "action, and for the database also risks data loss). Paging "
            "instead; restart it by hand if that is genuinely what is needed."
        )
    return None


async def _restart_container(params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Docker-restart a named container via brain_daemon.docker_restart_container."""
    refused = await _restart_container_refusal(params, ctx)
    if refused is not None:
        # `skipped` (not `failed`) is still a non-ok status, and the engine pages
        # on anything that is not "ok" — which is the point: refuse the action
        # AND surface the alert to a human, rather than silently doing nothing.
        return ActionResult(status="skipped", detail=refused)
    container = str(params.get("container") or "").strip()
    mod = _resolve_brain_daemon()
    if mod is None or not hasattr(mod, "docker_restart_container"):
        return ActionResult(status="failed", detail="brain_daemon.docker_restart_container unavailable")
    started = time.monotonic()
    outcome = await mod.docker_restart_container(container, pool=ctx.pool)
    latency = int((time.monotonic() - started) * 1000)
    if outcome.ok:
        status = "ok"
    elif outcome.status == docker_utils.RESTART_RECENTLY_STARTED:
        # Not restarted because something restarted it moments ago
        # (docker_utils' recently-started guard): a refusal, like the denylist
        # above, not an attempt that failed. Still non-ok, so the engine pages
        # the alert now with this detail rather than holding it for a verify of
        # a restart that never ran, and the audit row says `skipped`. The
        # breaker and the rate cap count the row either way.
        status = "skipped"
    else:
        status = "failed"
    return ActionResult(status=status, detail=outcome.detail, latency_ms=latency)


async def _restart_host_service_check(
    params: dict[str, Any], ctx: RemediationContext,
) -> tuple[str | None, str]:
    """``(refusal, evidence)`` for ``restart_host_service``.

    Refuses a missing ``service``, one that isn't in
    ``host_services.HOST_SERVICES``, and a service its own check finds
    answering (``HostService.confirm_down``). Otherwise the refusal is None and
    the evidence says how the brain saw it down. The executor and ``refusal``
    both call this, so they cannot disagree.
    """
    service = str(params.get("service") or "").strip()
    if not service:
        return "restart_host_service: no 'service' param", ""
    spec = host_services.HOST_SERVICES.get(service)
    if spec is None:
        allowed = ", ".join(sorted(host_services.HOST_SERVICES)) or "none"
        return (
            f"restart_host_service: {service!r} is not a host service the "
            f"firefighter may restart (allowed: {allowed})"
        ), ""
    confirmation = await spec.confirm_down(ctx.pool)
    if not confirmation.down:
        return f"restart_host_service: {confirmation.detail}", ""
    return None, confirmation.detail


async def _restart_host_service_refusal(params: dict[str, Any], ctx: RemediationContext) -> str | None:
    """Why ``restart_host_service`` would refuse these params, or None if it would run."""
    refused, _evidence = await _restart_host_service_check(params, ctx)
    return refused


async def _restart_host_service(params: dict[str, Any], ctx: RemediationContext) -> ActionResult:
    """Restart an allowlisted host service through the host Recovery Agent.

    Only after the brain has seen the service down from its own side (see
    ``host_services``). A refusal is ``skipped``, which pages like any non-ok
    status; so does an agent that can't be reached or reports failure.
    """
    started = time.monotonic()
    refused, evidence = await _restart_host_service_check(params, ctx)
    if refused is not None:
        return ActionResult(
            status="skipped", detail=refused,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    service = str(params.get("service") or "").strip()
    ok, agent_detail = await host_services.restart_via_agent(ctx.pool, service)
    # The agent's answer first: a failure's page carries the start of this
    # detail (the dispatcher cuts its note at 200 characters), and the agent's
    # reason is what the operator needs there.
    return ActionResult(
        status="ok" if ok else "failed",
        detail=f"recovery agent: {agent_detail}; confirmed down first: {evidence}",
        latency_ms=int((time.monotonic() - started) * 1000),
    )


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
    "restart_host_service": _restart_host_service,
    "run_auto_remediate": _run_auto_remediate,
}

# The side-effect-free checks an executor makes before it acts (bad params, the
# restart denylist, the host-service allowlist and its is-it-down check), for
# callers that must know the answer without acting: a dry run records "would be
# refused" instead of "would have run it". The executor runs the same function,
# so the two cannot disagree.
_ACTION_REFUSALS: dict[str, Callable[[dict[str, Any], RemediationContext], Awaitable[str | None]]] = {
    "restart_container": _restart_container_refusal,
    "restart_host_service": _restart_host_service_refusal,
}


# Human/LLM-facing metadata, kept parallel to ACTION_REGISTRY (not merged into
# it, so the executor stays a plain callable). ``describe_catalog()`` is the
# ONLY description of the actions the brain sends the LLM selector; the model
# picks a name from here and the engine re-validates that pick against
# ACTION_REGISTRY before executing — the model's output is never trusted.
#
# ``rules_only: True`` keeps an action out of that catalog entirely, so only an
# operator-written remediation_rules row can run it. ``restart_host_service``
# is one: its only allowlisted target is Ollama, which the selector itself runs
# on (``ops_firefighter_model``). The exclusion regex keeps Ollama's alerts
# away from the model; this keeps "restart Ollama" out of the model's answers
# to every other alert, such as the pipeline findings that make up nearly all
# of what reaches it.
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
    "restart_host_service": {
        "rules_only": True,
        "description": (
            "Restart a host systemd service through the host Recovery Agent, "
            "for a service that runs on the host rather than in a container. "
            "Allowlisted services only, and only after the brain has asked the "
            "service itself and got no answer: a service that answers is up, "
            "and restarting it would only kill its in-flight work."
        ),
        "params_schema": {
            "service": (
                "str (required) — the Recovery Agent's service name; one of: "
                + ", ".join(
                    f"{name} ({spec.restarts})"
                    for name, spec in sorted(host_services.HOST_SERVICES.items())
                )
            ),
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


def is_rules_only(action_name: str) -> bool:
    """True for an action only a remediation_rules row may run (see _ACTION_META)."""
    return bool(_ACTION_META.get(action_name, {}).get("rules_only"))


def describe_catalog(allowlist: list[str] | None = None) -> list[dict[str, Any]]:
    """The action catalog the brain hands the LLM selector.

    Returns ``[{name, description, params_schema}]`` for every registered
    action the selector may pick, in registry order. A rules-only action
    (``is_rules_only``) is never offered, so the engine refuses it as
    off-catalog if a model names it anyway. A non-empty ``allowlist`` restricts
    the catalog to those names — mirroring ``ops_firefighter_action_allowlist``
    semantics where an empty/absent list means "all registered actions". A name
    in the allowlist that isn't registered is ignored: you can only ever offer
    an action that actually executes.
    """
    allowed = set(allowlist) if allowlist else None
    catalog: list[dict[str, Any]] = []
    for name in ACTION_REGISTRY:
        if allowed is not None and name not in allowed:
            continue
        if is_rules_only(name):
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


async def refusal(action_name: str, params: dict[str, Any], ctx: RemediationContext) -> str | None:
    """Why ``execute`` would refuse this action without running it, or None.

    Never raises: a check that blows up counts as a refusal, the answer that
    keeps a dry run from promising an action the executor might not take.
    """
    if action_name not in ACTION_REGISTRY:
        return f"unknown action: {action_name}"
    check = _ACTION_REFUSALS.get(action_name)
    if check is None:
        return None
    try:
        return await check(params or {}, ctx)
    except Exception as e:  # noqa: BLE001
        return f"refusal check raised: {e}"[:400]


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
