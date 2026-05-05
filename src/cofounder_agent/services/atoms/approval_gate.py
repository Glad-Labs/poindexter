"""``atoms.approval_gate`` — composable HITL pause-and-wait atom.

Phase 3 of the dynamic-pipeline-composition spec. Wraps the existing
:class:`services.stages.approval_gate.ApprovalGateStage` logic in atom
shape so the architect-LLM can drop a gate at any point in a composed
graph without subclassing.

How "interrupt" works in v1 (without a Postgres checkpointer):

When the gate is open, this atom calls
:func:`services.approval_service.pause_at_gate` (which writes
``content_tasks.awaiting_gate`` + fires the operator notification),
then returns ``_halt=True`` to short-circuit the rest of the graph.
The operator approves via Telegram / CLI / MCP, the existing approve
flow clears the gate columns and re-queues the task, and TemplateRunner
runs the template again. On the second pass the gate atom sees the
gate is already cleared and passes through — the graph continues from
where it left off.

This matches the StageRunner behavior exactly. A "true" LangGraph
``interrupt()`` (state durably checkpointed mid-graph) is blocked on
Phase 2's Postgres checkpointer with custom serializers, because the
v1 state contains live service objects (DatabaseService etc.) that
aren't msgpack-serializable.

Spec: ``docs/superpowers/specs/2026-05-04-dynamic-pipeline-composition.md``
Issue: Glad-Labs/poindexter#363.
"""

from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)


ATOM_META = AtomMeta(
    name="atoms.approval_gate",
    type="approval_gate",
    version="1.0.0",
    description=(
        "Pause pipeline execution pending operator approval at a named gate. "
        "On open: calls pause_at_gate(), notifies operator, returns _halt=True. "
        "On cleared: passes through (operator already approved on a prior pass)."
    ),
    inputs=(
        FieldSpec(
            name="task_id", type="str",
            description="Task UUID — required to write awaiting_gate column.",
            required=True,
        ),
        FieldSpec(
            name="gate_name", type="str",
            description=(
                "Stable gate slug, e.g. 'topic_decision', 'preview_approval'. "
                "Lands in content_tasks.awaiting_gate."
            ),
            required=True,
        ),
        FieldSpec(
            name="database_service", type="DatabaseService",
            description="DB pool source — needed for pause_at_gate write.",
            required=True,
        ),
        FieldSpec(
            name="site_config", type="SiteConfig",
            description="Used to look up pipeline_gate_<gate_name> enable flag.",
            required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="_halt", type="bool",
            description="Set to True when the gate is open — TemplateRunner halts.",
        ),
        FieldSpec(
            name="awaiting_gate", type="str",
            description="Same gate_name, surfaced for operator dashboards.",
        ),
        FieldSpec(
            name="gate_artifact", type="dict",
            description="The artifact the operator reviewed.",
        ),
    ),
    requires=("task_id", "gate_name"),
    produces=("_halt", "awaiting_gate", "gate_artifact"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(
        "writes content_tasks.awaiting_gate",
        "calls notify_operator",
    ),
    retry=RetryPolicy(max_attempts=1),
    fallback=(),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Atom entry point.

    Reads ``state['gate_name']`` (mandatory) plus optional
    ``state['gate_artifact_keys']`` (list of state keys to surface in
    the operator review payload). Writes the gate row + notifies on
    open, returns ``{}`` (passthrough) when the gate is already
    cleared.
    """
    from services.approval_service import is_gate_enabled, pause_at_gate

    gate_name = state.get("gate_name") or ""
    if not gate_name:
        logger.warning("[atoms.approval_gate] missing gate_name in state — passthrough")
        return {}

    site_config = state.get("site_config")
    task_id = state.get("task_id")
    if not task_id:
        logger.warning("[atoms.approval_gate:%s] missing task_id — passthrough", gate_name)
        return {}

    # Master enable check — passthrough when the operator has the gate
    # turned off in app_settings.
    if not is_gate_enabled(gate_name, site_config):
        logger.info("[atoms.approval_gate:%s] disabled — passthrough", gate_name)
        return {}

    # If the task already cleared this gate on a previous run, we're
    # in the resume pass — don't re-pause.
    pool = _resolve_pool(state)
    if pool is not None and await _gate_already_cleared(pool, str(task_id), gate_name):
        logger.info(
            "[atoms.approval_gate:%s] already cleared on prior pass — passthrough",
            gate_name,
        )
        return {}

    # Build the operator-review artifact. Keys to surface come from
    # ``state['gate_artifact_keys']`` (list[str]) when set; otherwise
    # we surface a minimal default summary.
    artifact_keys = state.get("gate_artifact_keys") or [
        "topic", "title", "excerpt", "quality_score", "featured_image_url",
    ]
    artifact: dict[str, Any] = {"task_id": str(task_id), "gate": gate_name}
    for key in artifact_keys:
        if key in state and state[key] not in (None, "", [], {}):
            artifact[key] = state[key]

    if pool is None:
        logger.error("[atoms.approval_gate:%s] no DB pool — cannot pause", gate_name)
        return {
            "_halt": True,
            "_halt_reason": (
                f"approval_gate {gate_name!r}: no DB pool on state — pipeline "
                "cannot be paused for review"
            ),
        }

    try:
        await pause_at_gate(
            task_id=str(task_id),
            gate_name=gate_name,
            artifact=artifact,
            site_config=site_config,
            pool=pool,
            notify=True,
        )
    except Exception as exc:
        logger.exception(
            "[atoms.approval_gate:%s] pause_at_gate raised: %s",
            gate_name, exc,
        )
        return {
            "_halt": True,
            "_halt_reason": (
                f"approval_gate {gate_name!r} pause_at_gate failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    return {
        "_halt": True,
        "_halt_reason": f"awaiting operator approval at gate {gate_name!r}",
        "awaiting_gate": gate_name,
        "gate_artifact": artifact,
    }


def _resolve_pool(state: dict[str, Any]) -> Any:
    db = state.get("database_service")
    return getattr(db, "pool", None) if db else None


async def _gate_already_cleared(pool: Any, task_id: str, gate_name: str) -> bool:
    """Return True if the task previously paused at this gate and was
    approved.

    We check ``pipeline_gate_history`` rather than relying on
    ``content_tasks.awaiting_gate`` alone because the column also reads
    NULL for tasks that never hit the gate at all — the history row is
    what tells us "this gate was specifically cleared, not just absent."
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                """
                SELECT EXISTS(
                  SELECT 1 FROM pipeline_gate_history
                  WHERE task_id = $1
                    AND gate_name = $2
                    AND event_kind = 'approved'
                )
                """,
                str(task_id), gate_name,
            )
            return bool(row)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[atoms.approval_gate] cleared-check failed: %s", exc)
        return False


__all__ = ["ATOM_META", "run"]
