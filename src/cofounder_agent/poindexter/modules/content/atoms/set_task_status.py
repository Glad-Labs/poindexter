"""atoms.set_task_status — general-purpose task-status mutation node.

A config-driven atom that transitions the RUNNING task's own
``pipeline_tasks.status`` to a status declared in its graph_def node ``config``
(``target_status``), via a guarded write. It is NOT a terminal-only finalizer —
it just sets a status, so it can sit anywhere in a graph; ``image_rebuild`` uses
it last (``target_status='completed'``) to give its otherwise-orphaned job row a
terminal state (mirrors how ``content.republish_post`` finalizes ``seo_refresh``).

Reads (from state; config values are seeded onto state by
``pipeline_architect.build_graph_from_spec``):
  - ``task_id``          (required) — the running task's own id.
  - ``target_status``    (required, from config) — the status to set.
  - ``allowed_from``     (optional, default ('in_progress',)) — guard whitelist.
  - ``percentage``       (optional, from config) — set atomically with the status.

Writes only the running task's own row; never touches any target draft.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

# Mirror of the pipeline_tasks_status_check CHECK constraint
# (services/migrations/0000_baseline.schema.sql, as amended by the
# 20260816_021929 migration that added 'expired' + 'dismissed'). Kept
# honest by test_set_task_status.py::test_valid_statuses_match_db_constraint.
_VALID_STATUSES: frozenset[str] = frozenset(
    {
        "pending",
        "in_progress",
        "approved",
        "awaiting_approval",
        "awaiting_gate",
        "rejected",
        "rejected_retry",
        "rejected_final",
        "failed",
        "completed",
        "published",
        "cancelled",
        "dry_run",
        "superseded",
        "archived",
        "expired",
        "dismissed",
    }
)

_DEFAULT_ALLOWED_FROM = ("in_progress",)

ATOM_META = AtomMeta(
    name="atoms.set_task_status",
    type="atom",
    version="1.0.0",
    description=(
        "Transition the running task's pipeline_tasks.status to a config-declared "
        "target_status via a guarded write (allowed_from defaults to in_progress). "
        "General-purpose status-mutation node; image_rebuild uses it terminally to "
        "mark the rebuild job 'completed'."
    ),
    inputs=(
        FieldSpec(name="task_id", type="str", description="the running task's id"),
        FieldSpec(
            name="target_status",
            type="str",
            description="status to set (from node config)",
        ),
        FieldSpec(
            name="allowed_from",
            type="list",
            description="guard whitelist (default ('in_progress',))",
            required=False,
        ),
        FieldSpec(
            name="percentage",
            type="int",
            description="progress %, set atomically with status",
            required=False,
        ),
        FieldSpec(
            name="database_service",
            type="object",
            description="DB service",
            required=False,
        ),
    ),
    outputs=(),
    requires=("task_id", "target_status"),
    produces=(),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("db_write",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    task_id = state.get("task_id")
    target_status = state.get("target_status")
    if not task_id or not target_status:
        raise RuntimeError(
            "atoms.set_task_status: task_id + target_status are required "
            f"(task_id={task_id!r}, target_status={target_status!r}). "
            "Declare target_status in the node config."
        )
    if target_status not in _VALID_STATUSES:
        raise RuntimeError(
            f"atoms.set_task_status: target_status={target_status!r} is not a "
            f"valid pipeline_tasks status; must be one of {sorted(_VALID_STATUSES)}"
        )

    allowed_from = state.get("allowed_from") or _DEFAULT_ALLOWED_FROM
    if isinstance(allowed_from, str):
        allowed_from = (allowed_from,)
    allowed_from = tuple(allowed_from)

    database_service = state.get("database_service")
    guarded = getattr(database_service, "update_task_status_guarded", None)
    if guarded is None:
        logger.warning(
            "atoms.set_task_status: database_service has no "
            "update_task_status_guarded — cannot set status %r for task %s",
            target_status,
            task_id,
        )
        return {}

    fields: dict[str, Any] = {}
    percentage = state.get("percentage")
    if percentage is not None:
        fields["percentage"] = int(percentage)

    prev = await guarded(
        task_id=str(task_id),
        new_status=target_status,
        allowed_from=allowed_from,
        **fields,
    )
    if prev is None:
        logger.debug(
            "atoms.set_task_status: guarded no-op for task %s (current status "
            "not in allowed_from=%s; already terminal?)",
            task_id,
            allowed_from,
        )
    elif prev != target_status:
        logger.info(
            "atoms.set_task_status: task %s status %s -> %s",
            task_id,
            prev,
            target_status,
        )
    return {}


__all__ = ["ATOM_META", "run", "_VALID_STATUSES"]
