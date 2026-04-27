"""Topic proposal service — manual topic injection + queue-cap helpers (#146).

Supports two callers:

- ``poindexter topics propose`` — operator hand-types a topic and the
  CLI calls :func:`propose_topic` to create a ``pipeline_tasks`` row
  that lands directly at ``awaiting_gate='topic_decision'``.
- ``services.topic_discovery.queue_topics`` — the anticipation engine's
  auto-discovered topics call :func:`pending_topic_count` /
  :func:`queue_at_capacity` to respect ``topic_discovery_max_pending``
  before they propose.

Both paths share the same gate machinery from #145
(``services.approval_service.pause_at_gate``); this module is a thin
glue layer that:

1. Inserts the new ``pipeline_tasks`` row with ``status='pending'``
   and the gate columns set so the operator drains the queue uniformly
   regardless of source.
2. Handles the gate-disabled case — when
   ``pipeline_gate_topic_decision`` is off, manual proposals still
   land in ``status='pending'`` (matches existing anticipation_engine
   behaviour) and skip straight to the worker.

Design rules from #198 / #146:

- DI seam: every public coroutine takes ``pool`` + ``site_config`` —
  no module-level singletons.
- Bail loudly: empty ``topic`` raises ``ValueError`` so the CLI prints
  a helpful error rather than silently inserting an empty row.
- DB-first: gate enable check goes through
  :func:`services.approval_service.is_gate_enabled` so the same flag
  controls both auto and manual paths.
- No silent fallback: when the queue cap can't be read (DB outage,
  malformed app_settings value), we log a WARNING + use the default
  rather than letting the queue grow unbounded.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from services.approval_service import (
    is_gate_enabled,
    pause_at_gate,
)
from services.audit_log import audit_log_bg
from services.logger_config import get_logger
from services.stages.topic_decision_gate import build_topic_decision_artifact

logger = get_logger(__name__)


# Default cap on the awaiting-approval queue. Keeps a runaway
# anticipation_engine from filling Telegram with hundreds of pending
# topics. Operators tune via ``topic_discovery_max_pending`` in
# app_settings.
DEFAULT_MAX_PENDING = 50


# Gate name we use throughout — pinned here so a typo in the wrapper
# Stage's config can't accidentally route a manual proposal to a
# different gate.
TOPIC_DECISION_GATE = "topic_decision"


# ---------------------------------------------------------------------------
# Queue-cap helpers — used by anticipation_engine + the propose CLI
# ---------------------------------------------------------------------------


async def pending_topic_count(*, pool: Any) -> int:
    """Return the number of tasks currently paused at ``topic_decision``.

    Reads the ``pipeline_tasks`` base table directly (not the view) so
    the count is accurate even if the view definition is mid-migration.
    """
    if pool is None:
        return 0
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchval(
                """
                SELECT COUNT(*) FROM pipeline_tasks
                 WHERE awaiting_gate = $1
                """,
                TOPIC_DECISION_GATE,
            )
        return int(row or 0)
    except Exception as exc:
        # Per the no-silent-fallback rule: we log loudly so the
        # operator can see why the queue hasn't grown. We return -1 to
        # let the caller decide how to react (anticipation_engine
        # treats -1 as "DB outage, skip propose").
        logger.warning(
            "[topic_proposal] pending_topic_count failed; returning -1: %s",
            exc,
        )
        return -1


def resolve_max_pending(site_config: Any) -> int:
    """Return the configured queue cap, falling back to the default."""
    if site_config is None:
        return DEFAULT_MAX_PENDING
    # SiteConfig.get_int handles the int-coercion + default. When the
    # caller passes a fake / dict-only stub we still tolerate it.
    if hasattr(site_config, "get_int"):
        try:
            return int(
                site_config.get_int(
                    "topic_discovery_max_pending", DEFAULT_MAX_PENDING
                )
            )
        except Exception:
            logger.warning(
                "[topic_proposal] topic_discovery_max_pending coerce failed; "
                "falling back to default %d",
                DEFAULT_MAX_PENDING,
            )
            return DEFAULT_MAX_PENDING
    raw = site_config.get("topic_discovery_max_pending", DEFAULT_MAX_PENDING)
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning(
            "[topic_proposal] topic_discovery_max_pending=%r not int; "
            "falling back to default %d",
            raw, DEFAULT_MAX_PENDING,
        )
        return DEFAULT_MAX_PENDING


async def queue_at_capacity(*, pool: Any, site_config: Any) -> bool:
    """Return True iff the topic-decision queue is at or above the cap.

    Anticipation engine consults this before proposing — if the queue
    is full, it skips (and the operator drains via the CLI). Returns
    False when the gate is OFF so the cap doesn't accidentally throttle
    the existing auto-queue flow.
    """
    if not is_gate_enabled(TOPIC_DECISION_GATE, site_config):
        return False
    cap = resolve_max_pending(site_config)
    pending = await pending_topic_count(pool=pool)
    if pending < 0:
        # DB outage path — refuse to propose (treat as "at capacity")
        # so a transient outage doesn't generate a flood once it
        # recovers. Log already emitted in pending_topic_count.
        return True
    return pending >= cap


# ---------------------------------------------------------------------------
# Manual propose
# ---------------------------------------------------------------------------


async def propose_topic(
    *,
    topic: str,
    primary_keyword: Optional[str] = None,
    tags: Optional[list[str]] = None,
    category: Optional[str] = None,
    source: str = "manual",
    target_length: int = 1500,
    style: str = "technical",
    tone: str = "professional",
    site_config: Any,
    pool: Any,
    notify: bool = True,
) -> dict[str, Any]:
    """Inject a topic into the topic-decision queue.

    Creates a ``pipeline_tasks`` row, then routes it through the
    gate machinery so the row lands at ``awaiting_gate='topic_decision'``
    when the gate is enabled. When the gate is OFF, the row is left
    in ``status='pending'`` exactly like an anticipation_engine
    auto-proposal — keeps the manual path additive, not a special case.

    Args:
        topic: Operator-supplied topic string. Required, non-empty.
        primary_keyword: Optional SEO target. Falls back to the first
            tag, then to the empty string.
        tags: Optional list of tag strings.
        category: Optional category slug. Defaults to "" (resolver
            picks one downstream).
        source: Where the topic came from. Defaults to "manual" so
            operators can tell hand-typed proposals apart from
            anticipation_engine output in the queue.
        target_length, style, tone: Pipeline parameters consumed by
            ``content_router_service`` once the gate is approved.
        site_config: SiteConfig instance (DI seam).
        pool: asyncpg pool (DI seam).
        notify: Forwarded to ``pause_at_gate`` — set False in tests so
            no Telegram / Discord call fires.

    Returns:
        Dict ``{"ok": True, "task_id": ..., "topic": ...,
        "awaiting_gate": "topic_decision" | None,
        "status": "pending" | "in_progress", "gate_enabled": bool,
        "queue_full": bool}``.

    Raises:
        ValueError: ``topic`` is empty / whitespace.
        RuntimeError: pool unavailable or DB write fails.
    """
    if not topic or not topic.strip():
        raise ValueError("propose_topic: topic must be a non-empty string")

    if pool is None:
        raise RuntimeError("propose_topic: asyncpg pool is required")

    topic_clean = topic.strip()
    tags_clean = [str(t).strip() for t in (tags or []) if str(t).strip()]
    primary_keyword_resolved = (
        (primary_keyword or "").strip()
        or (tags_clean[0] if tags_clean else "")
    )
    category_clean = (category or "").strip()

    # Cap check — refuse to propose past the queue cap so the operator
    # drains rather than letting the queue grow unbounded. Only enforced
    # when the gate is ON; with the gate off, manual proposals act like
    # anticipation_engine auto-queue (uncapped, the operator never sees
    # them).
    gate_on = is_gate_enabled(TOPIC_DECISION_GATE, site_config)
    queue_full = False
    if gate_on:
        queue_full = await queue_at_capacity(pool=pool, site_config=site_config)
        if queue_full:
            cap = resolve_max_pending(site_config)
            audit_log_bg(
                event_type="topic_proposal_queue_full",
                source="topic_proposal_service",
                details={
                    "topic": topic_clean[:120],
                    "source": source,
                    "cap": cap,
                },
                severity="warning",
            )
            return {
                "ok": False,
                "task_id": None,
                "topic": topic_clean,
                "awaiting_gate": None,
                "status": None,
                "gate_enabled": True,
                "queue_full": True,
                "detail": (
                    f"Topic queue is full (cap={cap}). "
                    "Drain pending topics before proposing more."
                ),
            }

    task_id = str(uuid4())
    now = datetime.now(timezone.utc)

    # Stash the manual-injection metadata on pipeline_versions.stage_data
    # so the artifact_fn / downstream stages can read source + tags.
    metadata = {
        "category": category_clean or None,
        "source": source,
        "discovered_by": "topic_propose",
        "target_length": int(target_length),
        "style": style,
        "tone": tone,
        "tags": tags_clean,
        "primary_keyword": primary_keyword_resolved,
    }

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pipeline_tasks (
                    task_id, task_type, topic, status, stage, site_id,
                    style, tone, target_length, category, primary_keyword,
                    target_audience, percentage, message, model_used,
                    error_message, created_at, updated_at
                ) VALUES (
                    $1, 'blog_post', $2, 'pending', 'pending', NULL,
                    $3, $4, $5, $6, $7,
                    NULL, 0, $8, NULL,
                    NULL, $9, $9
                )
                """,
                task_id,
                topic_clean,
                style,
                tone,
                int(target_length),
                category_clean or None,
                primary_keyword_resolved or None,
                f"manual proposal (source={source})",
                now,
            )
            # Mirror the topic-source metadata into pipeline_versions so
            # downstream stages can read tags / source from the existing
            # context-builder, matching how topic_discovery already routes
            # via content_tasks.metadata.
            await conn.execute(
                """
                INSERT INTO pipeline_versions (
                    task_id, version, stage_data, created_at, updated_at
                ) VALUES ($1, 1, $2::jsonb, $3, $3)
                ON CONFLICT (task_id, version) DO UPDATE
                   SET stage_data = EXCLUDED.stage_data,
                       updated_at = EXCLUDED.updated_at
                """,
                task_id,
                json.dumps({"metadata": metadata}, default=str),
                now,
            )
    except Exception as exc:
        logger.exception(
            "[topic_proposal] insert failed for topic %r: %s",
            topic_clean[:80], exc,
        )
        audit_log_bg(
            event_type="topic_proposal_insert_failed",
            source="topic_proposal_service",
            details={
                "topic": topic_clean[:120],
                "source": source,
                "error": f"{type(exc).__name__}: {exc}",
            },
            severity="error",
        )
        raise RuntimeError(
            f"propose_topic: pipeline_tasks insert failed: {exc}"
        ) from exc

    audit_log_bg(
        event_type="topic_proposed",
        source="topic_proposal_service",
        details={
            "topic": topic_clean[:120],
            "source": source,
            "primary_keyword": primary_keyword_resolved or None,
            "tags": tags_clean,
            "category": category_clean or None,
            "gate_enabled": gate_on,
        },
        task_id=task_id,
        severity="info",
    )

    if not gate_on:
        # Gate disabled — leave the row at status=pending so the
        # worker picks it up exactly like an anticipation_engine
        # proposal. No artifact, no notify; the operator opted out
        # of HITL on topic decisions.
        return {
            "ok": True,
            "task_id": task_id,
            "topic": topic_clean,
            "awaiting_gate": None,
            "status": "pending",
            "gate_enabled": False,
            "queue_full": False,
            "detail": (
                "Topic queued at status=pending — "
                "pipeline_gate_topic_decision is off."
            ),
        }

    # Gate enabled — pause the row immediately so the operator drains
    # the queue. Build the artifact through the same helper the Stage
    # uses so manual + auto proposals look identical to the operator.
    artifact = build_topic_decision_artifact(
        {
            "topic": topic_clean,
            "primary_keyword": primary_keyword_resolved,
            "tags": tags_clean,
            "category": category_clean,
            "topic_source": source,
            # No research yet — artifact_fn omits research_summary.
        }
    )

    try:
        await pause_at_gate(
            task_id=task_id,
            gate_name=TOPIC_DECISION_GATE,
            artifact=artifact,
            site_config=site_config,
            pool=pool,
            notify=notify,
        )
    except Exception as exc:
        # The row is in pipeline_tasks but the gate columns aren't set.
        # Bail loudly — the operator has a malformed row to clean up
        # but no silent loss of work.
        logger.exception(
            "[topic_proposal] pause_at_gate failed for task %s: %s",
            task_id, exc,
        )
        raise RuntimeError(
            f"propose_topic: pause_at_gate failed: {exc}"
        ) from exc

    return {
        "ok": True,
        "task_id": task_id,
        "topic": topic_clean,
        "awaiting_gate": TOPIC_DECISION_GATE,
        "status": "pending",
        "gate_enabled": True,
        "queue_full": False,
        "artifact": artifact,
    }


__all__ = [
    "DEFAULT_MAX_PENDING",
    "TOPIC_DECISION_GATE",
    "pending_topic_count",
    "resolve_max_pending",
    "queue_at_capacity",
    "propose_topic",
]
