"""content.persist_draft_images — write rebuilt content + featured back to the draft.

Terminal atom of the image_rebuild graph and its ONLY writer against the
target draft. Ports the persist tail of ``ImageRebuildService``:

1. UPDATE the target's ``pipeline_versions`` row (same version the entry atom
   loaded — the draft surface the console preview + approval flow read).
2. Bump ``pipeline_tasks.regen_images_attempts`` on the TARGET task.
3. Best-effort ``post_images_rebuild`` audit row via the capability handle
   (powers the same audit trail the synchronous path wrote).

The target's status is not touched — the draft stays ``awaiting_approval``
with fresh images, ready for the operator's normal approve/reject decision.
"""
from __future__ import annotations

import logging
from typing import Any

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.persist_draft_images",
    type="atom",
    version="1.0.0",
    description=(
        "Persist rebuilt draft content + featured image to the target task's "
        "pipeline_versions row, bump regen_images_attempts, audit."
    ),
    inputs=(
        FieldSpec(name="content", type="str", description="rebuilt draft body (images injected)"),
        FieldSpec(name="target_task_id", type="str", description="the draft task being rebuilt"),
        FieldSpec(name="target_version", type="int", description="pipeline_versions.version to write"),
        FieldSpec(name="featured_image_url", type="str", description="rebuilt featured URL"),
        FieldSpec(name="image_results", type="list", description="for the audit breakdown", required=False),
        FieldSpec(name="featured_source", type="str", description="for the audit breakdown", required=False),
        FieldSpec(name="allow_stock", type="bool", description="for the audit breakdown", required=False),
        FieldSpec(name="database_service", type="object", description="DB service"),
        FieldSpec(name="platform", type="object", description="capability handle (audit)", required=False),
    ),
    outputs=(),
    requires=("content", "target_task_id", "target_version"),
    produces=(),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=("db_write",),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)

_PERSIST_SQL = (
    "UPDATE pipeline_versions SET content = $1, featured_image_url = $2 "
    "WHERE task_id = $3 AND version = $4"
)
_BUMP_SQL = (
    "UPDATE pipeline_tasks SET regen_images_attempts = "
    "COALESCE(regen_images_attempts, 0) + 1 WHERE task_id = $1"
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    content = state.get("content") or ""
    target_task_id = state.get("target_task_id")
    target_version = state.get("target_version")
    pool = getattr(state.get("database_service"), "pool", None)
    if not content or not target_task_id or target_version is None or pool is None:
        raise RuntimeError(
            "content.persist_draft_images: content + target_task_id + target_version "
            f"+ database_service are required (target={target_task_id!r}, "
            f"version={target_version!r}, content={'set' if content else 'EMPTY'}, "
            f"pool={'set' if pool else 'None'})"
        )

    featured_url = state.get("featured_image_url") or None
    await pool.execute(_PERSIST_SQL, content, featured_url, str(target_task_id), int(target_version))
    await pool.execute(_BUMP_SQL, str(target_task_id))

    image_results = state.get("image_results") or []
    platform = state.get("platform")
    if platform is not None:
        try:
            await platform.audit.write(
                "post_images_rebuild",
                source="image_rebuild_graph",
                details={
                    "task_id": str(target_task_id),
                    "rebuild_task_id": str(state.get("task_id") or ""),
                    "inline_sources": [r.get("source") for r in image_results],
                    "featured_source": state.get("featured_source") or "none",
                    "allow_stock": bool(state.get("allow_stock", False)),
                },
                task_id=str(target_task_id),
                severity="info",
            )
        except Exception as exc:
            logger.warning("[content.persist_draft_images] audit write failed: %s", exc)

    inline_generated = sum(1 for r in image_results if r.get("source") == "image_gen")
    logger.info(
        "[content.persist_draft_images] draft %s updated (v%s): %d inline "
        "(%d generated) + featured (%s)",
        target_task_id, target_version, len(image_results), inline_generated,
        state.get("featured_source") or "none",
    )
    return {}


__all__ = ["ATOM_META", "run"]
