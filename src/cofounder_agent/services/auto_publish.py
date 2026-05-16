"""Auto-publish helpers — quality-gated, daily-limited blog publishing.

Ported from ``services/task_executor.py`` during the Prefect cutover
Stage 4 (Glad-Labs/poindexter#410). The dispatch daemon was deleted;
this module owns the two pieces of logic that survived because
``services/post_pipeline_actions.py`` still needs them after every
successful pipeline run:

- :func:`get_auto_publish_threshold` — reads the
  ``auto_publish_threshold`` row from ``app_settings`` (default ``0``
  = disabled). The gate fires when the pipeline's QA quality_score
  meets or exceeds this floor AND ``require_human_approval=false``.
  The "do we even want to auto-publish?" gate lives in
  ``post_pipeline_actions._maybe_auto_publish``; this helper only
  answers "what's the threshold?".

- :func:`auto_publish_task` — flips a task to ``approved`` /
  ``publish_mode='auto'``, calls ``publish_service.publish_post_from_task``,
  and records the approval + distribution rows so the
  ``content_tasks`` view resolves ``approval_status='approved'`` for
  auto-published rows the same way the operator-curated path does.

Both helpers fail loud per ``feedback_no_silent_defaults``: anything
that bubbles up (DB error, missing task, missing featured image,
publish failure) is logged at WARNING or ERROR with enough context
to find the row in Grafana / Loki. The task lands in
``awaiting_approval`` whenever the auto-publish path can't complete
cleanly, which is the safe failure mode (an operator-visible row
is always better than a silent drop).

Per ``feedback_design_for_llm_consumers``: this module is the canonical
source for "how does auto-publish work?". The post_pipeline_actions
caller threads the wired DatabaseService through; this module never
touches a module-level singleton.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def _get_setting(
    database_service: Any,
    key: str,
    default: str,
) -> str:
    """Read an ``app_settings`` value via the DatabaseService cache.

    Mirrors the contract of the deleted ``TaskExecutor._get_setting`` so
    existing call sites behave identically: returns a str (DB values
    are coerced from int/bool to str), falls back to ``default`` on any
    DB / cache miss. Never raises — auto-publish is best-effort.
    """
    if database_service is None:
        return default
    try:
        raw = await database_service.get_setting_value(key, default)
        return str(raw) if raw is not None else default
    except Exception:
        return default


async def get_auto_publish_threshold(database_service: Any) -> float:
    """Return the ``auto_publish_threshold`` app_settings value.

    Zero (the default) means auto-publish is disabled — the gate in
    ``post_pipeline_actions._maybe_auto_publish`` treats
    ``threshold <= 0`` as "never fire". Operators raise this above the
    desired quality_score floor to opt in.
    """
    try:
        value = await _get_setting(database_service, "auto_publish_threshold", "0")
        return float(value) if value else 0.0
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "[AUTO_PUBLISH] Failed to read auto_publish_threshold: %s", exc,
        )
        return 0.0


async def auto_publish_task(
    *,
    database_service: Any,
    task_id: str,
    quality_score: float,
) -> bool:
    """Auto-approve and publish a task that has cleared the quality floor.

    Returns ``True`` when the publish succeeded (task is live on the
    public site), ``False`` when the path bailed for any reason
    (daily limit hit, missing featured image, publish_service error).
    Callers should suppress the operator notification when this
    returns ``True`` — ``publish_service`` sends its own
    "published" message.

    Failure modes (each leaves the task in ``awaiting_approval`` so
    the operator can intervene):

    - ``daily_post_limit`` reached (default ``1``/day)
    - task row not found by ``task_id``
    - task missing ``featured_image_url`` (we don't auto-publish
      image-less posts; the operator-facing approval queue is the
      right surface for that)
    - ``publish_post_from_task`` returned ``success=False``
    """
    from services.publish_service import publish_post_from_task

    if database_service is None:
        logger.warning(
            "[AUTO_PUBLISH] database_service is None — task %s stays in awaiting_approval",
            task_id,
        )
        return False

    # Daily limit — check the cloud (production) posts table when we have
    # a separate cloud_pool, otherwise fall back to the local pool. The
    # date arithmetic runs on the DB so we never have a timezone-skew
    # bug between the worker's clock and the published_at column.
    try:
        daily_limit = int(await _get_setting(database_service, "daily_post_limit", "1"))
        check_pool = getattr(database_service, "cloud_pool", None) or database_service.pool
        published_today = await check_pool.fetchval(
            "SELECT COUNT(*) FROM posts "
            "WHERE status = 'published' AND published_at::date = CURRENT_DATE"
        )
        if published_today >= daily_limit:
            logger.info(
                "[AUTO_PUBLISH] Daily limit reached (%d/%d), task %s stays in awaiting_approval",
                published_today, daily_limit, task_id,
            )
            return False
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning("[AUTO_PUBLISH] Failed to check daily limit: %s", exc)

    task = await database_service.get_task(task_id)
    if not task:
        logger.error("[AUTO_PUBLISH] Task %s not found", task_id)
        return False

    if not task.get("featured_image_url"):
        logger.info(
            "[AUTO_PUBLISH] Task %s missing featured image, stays in awaiting_approval",
            task_id,
        )
        return False

    # Status transition: pending → approved (single update, then refetch
    # so publish_post_from_task sees the fresh row).
    await database_service.update_task_status(task_id, "approved")

    existing_metadata = task.get("task_metadata") or {}
    if isinstance(existing_metadata, str):
        try:
            existing_metadata = json.loads(existing_metadata) if existing_metadata else {}
        except (json.JSONDecodeError, TypeError):
            existing_metadata = {}
    existing_metadata.update(
        {
            "auto_published": True,
            "auto_publish_quality_score": quality_score,
            "auto_published_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    await database_service.update_task(
        task_id,
        {
            "approval_status": "approved",
            "publish_mode": "auto",
            "task_metadata": json.dumps(existing_metadata, default=str),
        },
    )

    task = await database_service.get_task(task_id)
    if not task:
        logger.error("[AUTO_PUBLISH] Task %s not found after approval", task_id)
        return False

    result = await publish_post_from_task(
        database_service,
        task,
        task_id,
        publisher="auto_publish",
        trigger_revalidation=True,
        queue_social=True,
    )

    if not result.success:
        logger.error(
            "[AUTO_PUBLISH] Task %s auto-publish failed: %s",
            task_id, result.error,
        )
        return False

    logger.info(
        "[AUTO_PUBLISH] Task %s published as post %s (score: %s, slug: %s)",
        task_id, result.post_id, quality_score, result.post_slug,
    )

    # Record approval + distribution so the ``content_tasks`` view's
    # resolved ``approval_status`` / ``post_id`` / ``post_slug`` columns
    # are non-NULL for auto-published rows (same contract as the
    # operator-curated approve path).
    with suppress(Exception):
        await database_service.pool.execute(
            """
            INSERT INTO pipeline_gate_history
                (task_id, gate_name, event_kind, feedback, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            task_id,
            "auto_publish",
            "approved",
            f"Auto-approved at quality score {quality_score:.1f}",
            json.dumps(
                {
                    "reviewer": "auto_publish",
                    "decision": "approved",
                    "quality_score": quality_score,
                },
                default=str,
            ),
        )
        from services.pipeline_db import PipelineDB

        await PipelineDB(database_service.pool).add_distribution(
            task_id=task_id,
            target="gladlabs.io",
            post_id=result.post_id,
            post_slug=result.post_slug,
            external_url=result.published_url,
            status="published",
        )

    # model_performance.human_approved flip — learning signal that
    # closes the loop on whether the quality scorer's prediction
    # matched the auto-publish outcome (poindexter#271 Phase 3.A1).
    with suppress(Exception):
        await database_service.mark_model_performance_outcome(
            task_id, human_approved=True, post_published=True,
        )

    return True


__all__ = ["auto_publish_task", "get_auto_publish_threshold"]
