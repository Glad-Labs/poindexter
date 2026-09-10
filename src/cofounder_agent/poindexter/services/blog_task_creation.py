"""Blog-post task creation — the service behind POST /api/tasks (blog_post).

Extracted verbatim-as-possible from ``routes/task_routes.py``'s
``_handle_blog_post_creation`` (poindexter#947): the Cofounder chat agent's
``create_post`` tool needs the SAME creation path the HTTP route uses —
topic resolution, semantic dedup guard, length picker, throttle flag — and
the transport-adapter contract (ADR 2026-06-10) says that logic belongs in a
service both adapters call, not in a route handler one of them would have to
duplicate.

Transport-agnostic error contract: failures raise
:class:`BlogTaskCreationError` carrying an HTTP-ish ``status_code`` +
operator-readable ``detail``. The HTTP route maps it onto ``HTTPException``;
the chat tool surfaces ``detail`` to the model as the tool error (the 409
duplicate message names the colliding post and the force=true override —
written for LLM consumption per feedback_design_for_llm_consumers).
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any

from schemas.task_schemas import UnifiedTaskRequest
from services.logger_config import get_logger
from services.topic_length import pick_target_length

logger = get_logger(__name__)


class BlogTaskCreationError(Exception):
    """Transport-agnostic creation failure (maps to an HTTP status)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def resolve_niche_for_topics(pool: Any, niche_slug: str | None) -> Any:
    """Resolve which niche a topic operation targets — explicit slug wins,
    a single active niche is unambiguous, anything else fails loud
    (``feedback_no_silent_defaults``: never guess between niches)."""
    from services.niche_service import NicheService

    nsvc = NicheService(pool)
    if niche_slug:
        niche = await nsvc.get_by_slug(niche_slug)
        if niche is None:
            raise BlogTaskCreationError(
                status_code=404, detail=f"unknown niche_slug: {niche_slug!r}",
            )
        return niche
    active = await nsvc.list_active()
    if len(active) == 1:
        return active[0]
    if not active:
        raise BlogTaskCreationError(
            status_code=422,
            detail="no active niches configured — create one first "
                   "(poindexter niches create)",
        )
    raise BlogTaskCreationError(
        status_code=422,
        detail="multiple active niches — pass niche_slug (one of: "
               + ", ".join(sorted(n.slug for n in active)) + ")",
    )


async def create_blog_post_task(
    request: UnifiedTaskRequest,
    *,
    db_service: Any,
    site_config: Any | None = None,
    user_id: str = "operator",
) -> dict[str, Any]:
    """Create a pending blog_post pipeline task (dedup-guarded, throttled-flagged).

    Body moved from ``routes/task_routes._handle_blog_post_creation`` —
    behaviour-preserving except that failures raise
    :class:`BlogTaskCreationError` instead of ``HTTPException``.
    """
    task_id = str(uuid_lib.uuid4())

    # Log model selections (#952) so we can confirm user choices are applied
    if request.models_by_phase:
        logger.info(
            "[create_blog_post_task] User model selections applied: %s",
            request.models_by_phase,
        )

    # Merge content_constraints into top-level fields (#1250)
    # content_constraints overrides top-level style/tone/target_length when provided
    cc = request.content_constraints or {}
    effective_style = cc.get("writing_style") or request.style or "narrative"
    effective_tone = cc.get("tone") or request.tone or "professional"
    # Length falls through to the weighted picker (#542) rather than a flat
    # literal: a hardcoded 1500 here pinned 91.5% of all tasks to one length
    # and left topic_discovery_length_distribution unreachable in prod. An
    # explicit caller length (top-level or content_constraints) still wins.
    effective_length = (
        cc.get("word_count")
        or request.target_length
        or pick_target_length(site_config)
    )

    # Resolve "auto" topic from the niche's topic_pool (b3 of
    # poindexter#812 — the Gen-1 TopicDiscovery inline scrape is retired).
    resolved_topic = (request.topic or "").strip()
    # Capture BEFORE resolution: pool candidates were already deduped at
    # tap ingest, so the manual-injection dedup guard must skip the auto
    # path (no human is present to pass force=true).
    is_auto_topic = resolved_topic.lower() == "auto"
    if is_auto_topic:
        from services.topic_pool import claim_best_pooled_topic

        pool = db_service.pool if db_service else None
        if pool is None:
            raise BlogTaskCreationError(
                status_code=503, detail="Database pool unavailable",
            )
        niche = await resolve_niche_for_topics(pool, request.niche_slug)
        claimed = await claim_best_pooled_topic(
            pool, niche_id=niche.id, site_config=site_config,
        )
        if claimed is None:
            raise BlogTaskCreationError(
                status_code=422,
                detail=(
                    f"Could not resolve auto topic — topic_pool holds no "
                    f"usable candidates for niche {niche.slug!r}. The topic "
                    "taps haven't deposited fresh candidates (check "
                    "external_taps rows + the Findings board)."
                ),
            )
        resolved_topic = claimed["title"]
        # Stamp the resolved niche so the task passes the #729
        # niche-allowlist publish gate even when the caller omitted it.
        if not request.niche_slug:
            request.niche_slug = niche.slug
        logger.info(
            "[create_blog_post_task] Resolved 'auto' topic -> %r (pool row %s, source %s)",
            resolved_topic, claimed["id"], claimed["source"],
        )
        # Carry the pool row's summary into the task as caller-attached
        # research. Layer 1 of writer_core._collect_research_context reads
        # metadata["research_context"], which is the same seam the seed_url
        # path uses ("that's how we get it in front of the LLM without adding
        # new pipeline wiring") — the auto path simply never used it, so every
        # source's summary was discarded at the moment of task creation. 804 of
        # 1,815 topic_pool rows carry one (internal_rag ~118 chars, rss ~150),
        # and benchmark_findings puts its whole measured fact block there, so
        # dropping it would leave that source proposing a topic with its
        # evidence stripped off.
        summary = (claimed.get("summary") or "").strip()
        if summary:
            merged = dict(request.metadata or {})
            existing = merged.get("research_context", "")
            # Prepend, matching the seed_url precedent: pool context leads,
            # anything the caller supplied is preserved after it.
            merged["research_context"] = (
                f"{summary}\n\n{existing}" if existing else summary
            )
            merged.setdefault("discovered_by", claimed["source"])
            request.metadata = merged
            logger.info(
                "[create_blog_post_task] Attached %d chars of pool summary as "
                "research_context (source %s)", len(summary), claimed["source"],
            )

    # Pre-enqueue semantic dedup guard — closes the create_post / POST
    # /api/tasks near-duplicate gap. AUTO topics were already deduped by
    # TopicDiscovery above; an explicitly-provided or seed_url-derived topic is
    # checked here against already-published posts and refused (409) when too
    # similar, unless the caller passes force=true. See topic_dedup_guard.py.
    if not is_auto_topic:
        from services.topic_dedup_guard import (
            DuplicateTopicError,
            assert_topic_not_duplicate,
        )

        try:
            await assert_topic_not_duplicate(
                resolved_topic,
                site_config=site_config,
                force=bool(getattr(request, "force", False)),
            )
        except DuplicateTopicError as dup:
            # 409 Conflict — the topic collides with an existing post. The
            # message names the post, the score, and the force=true override.
            raise BlogTaskCreationError(status_code=409, detail=str(dup)) from dup

    task_data = {
        "id": task_id,
        "task_name": f"Blog Post: {resolved_topic}",
        "task_type": "blog_post",
        "topic": resolved_topic,
        "niche_slug": request.niche_slug,
        "category": request.category or "general",
        "target_audience": request.target_audience or "General",
        "primary_keyword": request.primary_keyword,
        "style": effective_style,
        "tone": effective_tone,
        "target_length": effective_length,
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": user_id,
        "metadata": {
            **(request.metadata or {}),
            "generate_featured_image": request.generate_featured_image,
            "tags": request.tags,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Check approval-queue throttle BEFORE insert so the response body
    # can tell the caller whether this task is going to sit behind a
    # wall of unreviewed work. The task is still queued (201) — we do
    # not reject it — but the caller sees ``queue_full: true`` plus
    # ``queue_position`` so MCP / dashboards can surface "this won't
    # run until you approve something" instead of silently stalling.
    # See GH-89 AC#1. Chose 201+flag over 429 because the caller
    # explicitly asked for this topic; refusing outright would drop
    # the request on the floor, and the whole point of the approval
    # queue is an asynchronous hand-off.
    queue_full = False
    queue_position = 0
    queue_limit = 0
    try:
        from services.pipeline_throttle import is_queue_full

        queue_full, queue_position, queue_limit = await is_queue_full(
            db_service.pool if db_service else None,
            site_config=site_config,
        )
    except Exception as e:
        # Best-effort display flag — creation must not fail because the
        # throttle *readout* couldn't be computed — but a broken check is
        # worth a visible warning (silent-excepts burn-down bar).
        logger.warning(
            "[create_blog_post_task] Throttle state check failed "
            "(queue_full flag omitted from response): %s", e,
        )

    if queue_full:
        logger.warning(
            "[create_blog_post_task] Approval queue full (%d/%d) — task %s accepted "
            "but will block until a slot opens. Free one via /approve-post or raise "
            "max_approval_queue.",
            queue_position, queue_limit, task_id[:8],
        )

    # Store in database as pending — task executor will pick it up
    returned_task_id = await db_service.add_task(task_data)
    logger.info("Blog task created: %s", returned_task_id)

    response: dict[str, Any] = {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "task_type": "blog_post",
        "topic": resolved_topic,
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Blog post task created and queued",
    }
    if queue_full:
        response["queue_full"] = True
        # queue_position = current awaiting_approval count. The new task
        # sits behind roughly ``(queue_position - queue_limit + 1)`` human
        # approvals before the executor will pick it up.
        response["queue_position"] = queue_position
        response["queue_limit"] = queue_limit
        response["message"] = (
            f"Blog post task created but pipeline is throttled: "
            f"{queue_position} tasks awaiting approval (limit {queue_limit}). "
            f"Task stays pending until approvals free a slot."
        )
    return response


__all__ = [
    "BlogTaskCreationError",
    "create_blog_post_task",
    "resolve_niche_for_topics",
]
