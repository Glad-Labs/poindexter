"""content.load_draft_for_image_rebuild — hydrate state from an awaiting_approval draft.

The image_rebuild graph's entry atom. Mirrors ``content.load_existing_post``
(the seo_refresh entry) but reads the DRAFT surface — the latest
``pipeline_versions`` row of a target task — instead of a published ``posts``
row. Strips the draft's existing ``<img>`` blocks (and their trailing Pexels
``<figcaption>``) so the downstream ``content.plan_image_markers`` atom
re-plans every slot from clean article text.

``target_task_id`` + ``allow_stock`` arrive via the rebuild task's
``task_metadata`` (flattened onto the initial state by
``content_router_service``, the same seam seo_refresh's ``post_id`` uses).

Fail-loud: the target must still be ``awaiting_approval`` when the worker
claims the rebuild task — an operator may have approved/rejected the draft in
the queue gap, and rebuilding images on a task in any other status is always
wrong. Read-only; mutates nothing.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from modules.content.atoms._pool import resolve_pool
from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)

ATOM_META = AtomMeta(
    name="content.load_draft_for_image_rebuild",
    type="atom",
    version="1.0.0",
    description=(
        "Hydrate pipeline state from a target awaiting_approval draft's latest "
        "pipeline_versions row, with existing <img> blocks stripped, for the "
        "image_rebuild graph. No LLM, no writes."
    ),
    inputs=(
        FieldSpec(name="target_task_id", type="str", description="pipeline_tasks.task_id of the draft to rebuild"),
        FieldSpec(name="allow_stock", type="bool", description="accept Pexels stock fallback slots", required=False),
        FieldSpec(name="database_service", type="object", description="DB service"),
    ),
    outputs=(
        FieldSpec(name="content", type="str", description="draft body with existing <img> blocks stripped"),
        FieldSpec(name="topic", type="str", description="the draft task's topic"),
        FieldSpec(name="target_version", type="int", description="pipeline_versions.version the rebuild writes back to"),
        FieldSpec(name="allow_stock", type="bool", description="normalized stock-fallback flag for the gate atom"),
    ),
    requires=("target_task_id",),
    produces=("content", "topic", "target_version", "allow_stock"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(),
    retry=RetryPolicy(max_attempts=1),
    parallelizable=False,
)

_STATUS_SQL = "SELECT status FROM pipeline_tasks WHERE task_id = $1"
_TOPIC_SQL = "SELECT topic FROM pipeline_tasks WHERE task_id = $1"
_LATEST_SQL = (
    "SELECT content, version FROM pipeline_versions "
    "WHERE task_id = $1 ORDER BY version DESC LIMIT 1"
)
# Statuses meaning "an operator (or a downstream system) already acted on
# this draft" — the benign, expected race this module's docstring describes.
# Anything else that isn't awaiting_approval (pending/in_progress/on_hold/
# None/a task that doesn't exist) is a genuinely unexpected state, not this
# race — those stay a plain fail-loud RuntimeError. The marker phrase below
# ("target draft moved on") is matched by content_router_service to record
# status='cancelled' + severity='info' instead of 'failed'/severity='error'
# (poindexter#846) — mirrors the pipeline_dry_run_mode halt-demotion.
_TARGET_MOVED_ON_STATUSES = frozenset({"approved", "rejected", "published"})
# An <img ...> tag plus an optional trailing <figcaption>…</figcaption> (Pexels).
_IMG_BLOCK_RE = re.compile(
    r"<img\b[^>]*>\s*(?:<figcaption>.*?</figcaption>)?",
    re.IGNORECASE | re.DOTALL,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    target_task_id = state.get("target_task_id")
    pool = resolve_pool(state, atom="content.load_draft_for_image_rebuild")
    if not target_task_id or pool is None:
        raise RuntimeError(
            "content.load_draft_for_image_rebuild: target_task_id + database_service "
            f"are required (target_task_id={target_task_id!r}, pool={'set' if pool else 'None'})"
        )

    status = await pool.fetchval(_STATUS_SQL, str(target_task_id))
    if status != "awaiting_approval":
        if status in _TARGET_MOVED_ON_STATUSES:
            raise RuntimeError(
                "content.load_draft_for_image_rebuild: target draft moved on "
                f"before rebuild claimed it (target_task_id={target_task_id!r} "
                f"is now {status!r}) — not a bug, the rebuild request is moot"
            )
        raise RuntimeError(
            "content.load_draft_for_image_rebuild: image rebuild requires an "
            f"awaiting_approval draft; task {target_task_id} is {status!r}"
        )
    row = await pool.fetchrow(_LATEST_SQL, str(target_task_id))
    if not row:
        raise RuntimeError(
            f"content.load_draft_for_image_rebuild: no pipeline_versions row for task {target_task_id!r}"
        )
    topic = await pool.fetchval(_TOPIC_SQL, str(target_task_id)) or ""

    stripped = _IMG_BLOCK_RE.sub("", row["content"] or "")
    out = {
        "content": stripped,
        "topic": topic,
        "target_version": int(row["version"]),
        "allow_stock": bool(state.get("allow_stock", False)),
    }
    logger.info(
        "[content.load_draft_for_image_rebuild] hydrated draft task=%s (v%d, allow_stock=%s)",
        target_task_id, out["target_version"], out["allow_stock"],
    )
    return out


__all__ = ["ATOM_META", "run"]
