"""
Task Management Routes

REST API endpoints for creating, retrieving, and managing tasks.
Uses asyncpg DatabaseService for async database access.
"""

import json
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from middleware.api_token_auth import verify_api_token
from schemas.task_schemas import TaskListResponse, UnifiedTaskRequest
from schemas.unified_task_response import UnifiedTaskResponse

# Import async database service
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency, get_site_config_dependency

# Configure logging
logger = get_logger(__name__)
# ============================================================================
# HELPER FUNCTIONS FOR TASK RESPONSE FORMATTING
# ============================================================================


def _parse_seo_keywords(value: str) -> list:
    """Parse seo_keywords from either JSON array string or comma-separated string."""
    if not value or not value.strip():
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return [kw.strip() for kw in value.split(",") if kw.strip()]


def _normalize_seo_keywords_in_task(task: dict[str, Any]) -> dict[str, Any]:
    """Normalize seo_keywords from strings to lists at top level, result, and task_metadata."""
    if not isinstance(task, dict):
        return task

    if "seo_keywords" in task and isinstance(task["seo_keywords"], str):
        task["seo_keywords"] = _parse_seo_keywords(task["seo_keywords"])

    if "result" in task and isinstance(task["result"], dict):
        if "seo_keywords" in task["result"] and isinstance(task["result"]["seo_keywords"], str):
            task["result"]["seo_keywords"] = _parse_seo_keywords(task["result"]["seo_keywords"])

    if "task_metadata" in task and isinstance(task["task_metadata"], dict):
        if "seo_keywords" in task["task_metadata"] and isinstance(
            task["task_metadata"]["seo_keywords"], str
        ):
            task["task_metadata"]["seo_keywords"] = _parse_seo_keywords(
                task["task_metadata"]["seo_keywords"]
            )

    return task


def _check_task_ownership(task: dict, current_user: Any) -> None:
    """
    Verify the current user owns the task.

    In solo-operator mode (Bearer token auth), ownership checks are
    bypassed since there is only one operator. When current_user is a
    str (token), all tasks are accessible. When it is a dict (legacy),
    compares user_id against the authenticated user's id.
    """
    # Solo-operator mode: token string — skip ownership check
    if isinstance(current_user, str):
        return
    task_owner = task.get("user_id")
    request_user = current_user.get("id") if isinstance(current_user, dict) else None
    # Allow access if ownership can't be determined (legacy tasks without user_id)
    if task_owner and request_user and str(task_owner) != str(request_user):
        raise HTTPException(status_code=403, detail="Access denied")


# ============================================================================
# SEED URL RESOLUTION (GH-42)
# ============================================================================
# Matt often sees a link and wants to riff on that topic. Instead of
# requiring him to extract the topic + submit via the API, we accept
# ``seed_url`` directly, fetch it, and extract a topic from the page's
# <title>/<h1>. Attribution is injected into the writer's research
# context so the resulting post cites the source — see GH-42 AC#3 for
# the Herrington-pattern reasoning behind the "Source article:" block.


async def _resolve_seed_url(task_request: UnifiedTaskRequest) -> None:
    """If ``seed_url`` is set, fetch it and populate topic + research context.

    Mutates ``task_request`` in place:
      - If ``topic`` is empty, sets it to the extracted page title.
      - If ``topic`` is also present, keeps the caller's topic but still
        stores the URL + title/excerpt in metadata so the writer can
        attribute.
      - Stores the seed URL, title, excerpt, and a pre-formatted
        "Source article:" research_context block in
        ``task_request.metadata`` so the downstream stage
        :mod:`services.stages.generate_content` picks it up via
        ``_extract_caller_research``.

    On any fetch/parse failure, raises HTTPException 400 with a clear
    reason — we do NOT create a task and fall back to autodiscovery,
    because the caller explicitly asked for this URL.
    """
    seed_url = (task_request.seed_url or "").strip()
    if not seed_url:
        return

    # Import late so tests that don't exercise seed_url don't pay the
    # httpx import cost and so the module is easy to monkeypatch in
    # route tests without the full http stack loaded.
    from services.seed_url_fetcher import (
        SeedURLError,
        build_source_attribution,
        fetch_seed_url,
    )

    try:
        result = await fetch_seed_url(seed_url)
    except SeedURLError as exc:
        logger.info(
            "[SEED_URL] Fetch rejected for %s (reason=%s): %s",
            seed_url, exc.reason, exc,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "seed_url_fetch_failed",
                "reason": exc.reason,
                "message": str(exc),
                "url": seed_url,
            },
        ) from exc
    except Exception as exc:  # pragma: no cover — defensive catch-all
        logger.error("[SEED_URL] Unexpected fetch crash for %s: %s", seed_url, exc, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail={
                "error": "seed_url_fetch_failed",
                "reason": "network",
                "message": f"Unexpected error fetching {seed_url}",
                "url": seed_url,
            },
        ) from exc

    # Promote the extracted title to ``topic`` only when the caller
    # didn't provide one. If both are present, the caller's topic wins
    # — they may want a specific angle ("rebut this post", "summarize
    # for beginners") while still pinning the source URL.
    if not task_request.topic or not str(task_request.topic).strip():
        # ``topic`` has a max_length=200 Pydantic validator; titles
        # sometimes exceed that, so we trim here to stay inside the
        # schema bound while preserving as much context as possible.
        task_request.topic = result.title[:200]

    # Build the research-context block with the "Source article:"
    # attribution header. The writer's _extract_caller_research helper
    # reads research_context from metadata JSONB — that's how we get it
    # in front of the LLM without adding new pipeline wiring.
    attribution = build_source_attribution(result)

    merged_metadata = dict(task_request.metadata or {})
    # If the caller ALREADY supplied research_context, prepend ours so
    # attribution wins but their additional context is preserved.
    existing_research = merged_metadata.get("research_context", "")
    if existing_research:
        merged_metadata["research_context"] = f"{attribution}\n\n{existing_research}"
    else:
        merged_metadata["research_context"] = attribution

    # Persist seed URL + extracted pieces as first-class metadata keys
    # so the Oversight Hub / MCP can display them without re-parsing
    # the research_context string.
    merged_metadata["seed_url"] = result.url
    merged_metadata["seed_url_title"] = result.title
    merged_metadata["seed_url_excerpt"] = result.excerpt
    merged_metadata["discovered_by"] = merged_metadata.get("discovered_by") or "seed_url"

    task_request.metadata = merged_metadata


# Configure router with prefix and tags
router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post(
    "/discover-topics",
    response_model=dict[str, Any],
    summary="Trigger topic discovery on demand and optionally queue the results",
    status_code=200,
)
@limiter.limit("10/minute")
async def discover_topics(
    request: Request,
    max_topics: int = Query(5, ge=1, le=20),
    queue: bool = Query(True, description="Queue fresh topics as content tasks"),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    """Run TopicDiscovery on demand instead of waiting for the 8-hour idle cycle.

    Returns the list of discovered topics with a duplicate flag for each.
    When `queue=true` (default), fresh topics are immediately inserted as
    pending content_tasks so the executor picks them up on its next poll.
    """
    try:
        from services.topic_discovery import TopicDiscovery

        pool = db_service.pool
        if not pool:
            raise HTTPException(status_code=503, detail="Database pool unavailable")
        # Phase H step 5 (GH#95): thread site_config explicitly.
        discovery = TopicDiscovery(pool, site_config=site_config)
        topics = await discovery.discover(max_topics=max_topics)
        fresh = [t for t in topics if not getattr(t, "is_duplicate", False)]

        queued_count = 0
        if queue and fresh:
            queued_count = await discovery.queue_topics(fresh)

        return {
            "discovered": len(topics),
            "fresh": len(fresh),
            "queued": queued_count,
            "topics": [
                {
                    "title": t.title,
                    "source": getattr(t, "source", None),
                    "score": getattr(t, "score", None),
                    "is_duplicate": getattr(t, "is_duplicate", False),
                }
                for t in topics
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Topic discovery failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Topic discovery failed: {e}") from e


@router.post(
    "",
    response_model=dict[str, Any],
    summary="Create task - unified endpoint for all task types",
    status_code=201,
)
@limiter.limit("10/minute")
async def create_task(
    request: Request,
    task_request: UnifiedTaskRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
):
    """Unified task creation endpoint - routes to appropriate handler based on task_type.

    Supported types: blog_post, social_media, email, newsletter,
    business_analytics, data_retrieval, market_research, financial_analysis.
    See UnifiedTaskRequest schema for all parameters.
    """
    try:
        # GH-42: if the caller only sent ``seed_url`` (no topic), fetch
        # the URL and extract a topic from its <title>/<h1>. Mutates the
        # request in-place so downstream handlers see a resolved topic.
        # Failures bubble up as HTTPException 400 with a clear reason —
        # we deliberately do NOT fall back to autodiscovery, because the
        # caller asked for THIS specific URL.
        await _resolve_seed_url(task_request)

        # Validate required fields (belt-and-suspenders — Pydantic also
        # enforces this, but the check keeps the 422 message specific).
        if not task_request.topic or not str(task_request.topic).strip():
            raise HTTPException(
                status_code=422,
                detail="topic is required and cannot be empty",
            )

        logger.info("Creating task: type=%s, topic=%s", task_request.task_type, task_request.topic)

        # Route based on task_type using registry dict (Open/Closed — add new
        # task types by registering a handler below, not by editing this block).
        handler = _TASK_TYPE_REGISTRY.get(task_request.task_type)
        if handler is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown task_type: '{task_request.task_type}'. Supported: {', '.join(sorted(_TASK_TYPE_REGISTRY.keys()))}",
            )
        # Solo-operator: pass a dict with "id" for backward compat with handlers
        operator_user = {"id": "operator"}
        # Blog handler needs site_config for the throttle check (Phase H).
        # Other handlers ignore extra kwargs via signature inspection below;
        # we pass it only to the blog handler to keep other signatures stable.
        if task_request.task_type == "blog_post":
            return await handler(
                task_request, operator_user, db_service, site_config=site_config,
            )
        return await handler(task_request, operator_user, db_service)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Task creation failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to create task",
        ) from e


# ============================================================================
# TASK TYPE HANDLERS
# ============================================================================


async def _handle_blog_post_creation(
    request: UnifiedTaskRequest,
    current_user: dict,
    db_service: DatabaseService,
    *,
    site_config: Any,
) -> dict[str, Any]:
    """Handle blog post task creation.

    ``site_config`` is required (kw-only). Phase H (GH#95) finished the
    migration off the module-singleton fallback — every caller threads
    site_config through explicitly.
    """
    task_id = str(uuid_lib.uuid4())

    # Log model selections (#952) so we can confirm user choices are applied
    if request.models_by_phase:
        logger.info("[create_task] User model selections applied: %s", request.models_by_phase)

    # Merge content_constraints into top-level fields (#1250)
    # content_constraints overrides top-level style/tone/target_length when provided
    cc = request.content_constraints or {}
    effective_style = cc.get("writing_style") or request.style or "narrative"
    effective_tone = cc.get("tone") or request.tone or "professional"
    effective_length = cc.get("word_count") or request.target_length or 1500

    # Resolve "auto" topic to a fresh discovered topic
    resolved_topic = request.topic.strip()
    if resolved_topic.lower() == "auto":
        try:
            from services.topic_discovery import TopicDiscovery
            pool = db_service.pool if db_service else None
            # Phase H step 5 (GH#95): thread site_config explicitly.
            discovery = TopicDiscovery(pool, site_config=site_config)
            topics = await discovery.discover(max_topics=3)
            fresh = [t for t in topics if not t.is_duplicate]
            if fresh:
                resolved_topic = fresh[0].title
                logger.info("[create_task] Resolved 'auto' topic -> '%s'", resolved_topic)
            else:
                raise ValueError("No fresh topics found — all discovered topics are duplicates of recent content")
        except Exception as e:
            logger.warning("[create_task] Auto-topic resolution failed: %s", e)
            raise HTTPException(status_code=422, detail=f"Could not resolve auto topic: {e}") from e

    task_data = {
        "id": task_id,
        "task_name": f"Blog Post: {resolved_topic}",
        "task_type": "blog_post",
        "topic": resolved_topic,
        "category": request.category or "general",
        "target_audience": request.target_audience or "General",
        "primary_keyword": request.primary_keyword,
        "style": effective_style,
        "tone": effective_tone,
        "target_length": effective_length,
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
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

        # Phase H (GH#95): is_queue_full requires site_config as an
        # explicit parameter. ``site_config`` is the kw-only arg threaded
        # through from create_task's Depends(get_site_config_dependency).
        queue_full, queue_position, queue_limit = await is_queue_full(
            db_service.pool if db_service else None,
            site_config,
        )
    except Exception as e:
        logger.debug("[create_task] Throttle state check failed: %s", e)

    if queue_full:
        logger.warning(
            "[create_task] Approval queue full (%d/%d) — task %s accepted but will "
            "block until a slot opens. Free one via /approve-post or raise "
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


async def _handle_social_media_creation(
    request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService
) -> dict[str, Any]:
    """Handle social media task creation"""
    task_id = str(uuid_lib.uuid4())

    task_data = {
        "id": task_id,
        "task_name": f"Social Media: {request.topic}",
        "task_type": "social_media",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "tone": request.tone or "professional",
        "style": request.style,
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {
            **(request.metadata or {}),
            "platforms": request.platforms,
            "tags": request.tags,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    returned_task_id = await db_service.add_task(task_data)
    logger.info("Social task created: %s platforms=%s", returned_task_id, request.platforms)

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "task_type": "social_media",
        "topic": request.topic,
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": f"Social media task created for platforms: {', '.join(request.platforms or ['all'])}",
    }


async def _handle_email_creation(
    request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService
) -> dict[str, Any]:
    """Handle email task creation"""
    task_id = str(uuid_lib.uuid4())

    task_data = {
        "id": task_id,
        "task_name": f"Email: {request.topic}",
        "task_type": "email",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "tone": request.tone or "professional",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {}), "tags": request.tags},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    returned_task_id = await db_service.add_task(task_data)
    logger.info("Email task created: %s", returned_task_id)

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "task_type": "email",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Email task created and queued",
    }


async def _handle_newsletter_creation(
    request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService
) -> dict[str, Any]:
    """Handle newsletter task creation"""
    task_id = str(uuid_lib.uuid4())

    task_data = {
        "id": task_id,
        "task_name": f"Newsletter: {request.topic}",
        "task_type": "newsletter",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {}), "tags": request.tags},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    returned_task_id = await db_service.add_task(task_data)
    logger.info("Newsletter task created: %s", returned_task_id)

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "task_type": "newsletter",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Newsletter task created and queued",
    }


async def _handle_business_analytics_creation(
    request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService
) -> dict[str, Any]:
    """Handle business analytics task creation"""
    task_id = str(uuid_lib.uuid4())

    task_data = {
        "id": task_id,
        "task_name": f"Analytics: {request.topic}",
        "task_type": "business_analytics",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {
            **(request.metadata or {}),
            "metrics": request.metrics,
            "time_period": request.time_period,
            "business_context": request.business_context,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    returned_task_id = await db_service.add_task(task_data)
    logger.info("Analytics task created: %s metrics=%s", returned_task_id, request.metrics)

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "task_type": "business_analytics",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": f"Business analytics task created - Analyzing: {', '.join(request.metrics or [])}",
    }


async def _handle_data_retrieval_creation(
    request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService
) -> dict[str, Any]:
    """Handle data retrieval task creation"""
    task_id = str(uuid_lib.uuid4())

    task_data = {
        "id": task_id,
        "task_name": f"Data Retrieval: {request.topic}",
        "task_type": "data_retrieval",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {
            **(request.metadata or {}),
            "data_sources": request.data_sources,
            "filters": request.filters,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    returned_task_id = await db_service.add_task(task_data)
    logger.info("Data retrieval task created: %s sources=%s", returned_task_id, request.data_sources)

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "task_type": "data_retrieval",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": f"Data retrieval task created from sources: {', '.join(request.data_sources or [])}",
    }


async def _handle_market_research_creation(
    request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService
) -> dict[str, Any]:
    """Handle market research task creation"""
    task_id = str(uuid_lib.uuid4())

    task_data = {
        "id": task_id,
        "task_name": f"Market Research: {request.topic}",
        "task_type": "market_research",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {})},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    returned_task_id = await db_service.add_task(task_data)
    logger.info("Market research task created: %s", returned_task_id)

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "task_type": "market_research",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Market research task created and queued",
    }


async def _handle_financial_analysis_creation(
    request: UnifiedTaskRequest, current_user: dict, db_service: DatabaseService
) -> dict[str, Any]:
    """Handle financial analysis task creation"""
    task_id = str(uuid_lib.uuid4())

    task_data = {
        "id": task_id,
        "task_name": f"Financial Analysis: {request.topic}",
        "task_type": "financial_analysis",
        "topic": request.topic.strip(),
        "category": request.category or "general",
        "model_selections": request.models_by_phase or {},
        "quality_preference": request.quality_preference or "balanced",
        "status": "pending",
        "user_id": current_user.get("id", "system"),
        "metadata": {**(request.metadata or {})},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    returned_task_id = await db_service.add_task(task_data)
    logger.info("Financial analysis task created: %s", returned_task_id)

    return {
        "id": returned_task_id,
        "task_id": returned_task_id,
        "task_type": "financial_analysis",
        "status": "pending",
        "created_at": task_data["created_at"],
        "message": "Financial analysis task created and queued",
    }


# ---------------------------------------------------------------------------
# Task-type handler registry (Open/Closed Principle)
# Add new task types here — the dispatch site (create_task) never changes.
# ---------------------------------------------------------------------------
_TASK_TYPE_REGISTRY = {
    "blog_post": _handle_blog_post_creation,
    "social_media": _handle_social_media_creation,
    "email": _handle_email_creation,
    "newsletter": _handle_newsletter_creation,
    "business_analytics": _handle_business_analytics_creation,
    "data_retrieval": _handle_data_retrieval_creation,
    "market_research": _handle_market_research_creation,
    "financial_analysis": _handle_financial_analysis_creation,
}


# ============================================================================
# RETRIEVAL ENDPOINTS
# ============================================================================


@router.get("", response_model=TaskListResponse, summary="List all tasks with pagination")
async def list_tasks(
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(20, ge=1, le=100, description="Pagination limit (max 100)"),
    status: str | None = Query(
        None, description="Filter by status (queued, pending, running, completed, failed)"
    ),
    category: str | None = Query(None, description="Filter by category"),
    search: str | None = Query(
        None,
        max_length=200,
        description="Keyword search across task name, topic, and category (trigram-indexed)",
    ),
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """List all tasks with pagination and optional filtering."""
    try:
        # get_tasks_paginated returns a tuple (tasks, total)
        tasks, total = await db_service.get_tasks_paginated(
            offset=offset, limit=limit, status=status, category=category, search=search
        )

        # Convert raw task dicts to UnifiedTaskResponse objects if needed
        validated_tasks = []
        for raw_task in tasks:
            task = raw_task
            if isinstance(task, dict):
                # Normalize seo_keywords in all nested locations
                task = _normalize_seo_keywords_in_task(task)

                # CRITICAL: 'id' must match what POST /api/tasks returns so the
                # frontend can correlate optimistic inserts with server data.
                # POST returns task_id as id, so list must too.
                if task.get("task_id"):
                    task["id"] = str(task["task_id"])
                elif task.get("id"):
                    task["id"] = str(task["id"])

                # CRITICAL: Parse cost_breakdown from JSON string to dict
                if "cost_breakdown" in task and isinstance(task["cost_breakdown"], str):
                    try:
                        task["cost_breakdown"] = json.loads(task["cost_breakdown"])
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            "[get_tasks] cost_breakdown is not valid JSON for task %s — defaulting to None",
                            task.get("id"),
                        )
                        task["cost_breakdown"] = None

                validated_tasks.append(UnifiedTaskResponse(**task))
            else:
                validated_tasks.append(task)

        return TaskListResponse(
            tasks=validated_tasks,
            total=total,
            offset=offset,
            limit=limit,
        )
    except Exception as e:
        logger.error("Failed to list tasks: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list tasks") from e


# ============================================================================
# TASK DETAIL ENDPOINTS
# ============================================================================


@router.get("/{task_id}", response_model=UnifiedTaskResponse, summary="Get task details")
async def get_task(
    task_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Get details of a specific task by ID."""
    try:
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Ownership check: solo-operator mode bypasses via token string
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Convert task dict if needed, normalizing seo_keywords
        if isinstance(task, dict):
            task = _normalize_seo_keywords_in_task(task)
            return UnifiedTaskResponse(**task)
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch task") from e


@router.delete(
    "/{task_id}",
    summary="Delete task",
    tags=["Task Management"],
    status_code=204,
)
async def delete_task(
    task_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """Soft-delete a task by ID (sets status to 'cancelled' with deleted_at metadata)."""
    try:
        # Fetch task to verify it exists
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(
                status_code=404,
                detail=f"Task not found: {task_id}",
            )

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Soft delete: mark task as deleted with timestamp
        logger.info("Deleting task %s (operator)", task_id)

        # Update task status to 'cancelled' and add deleted_at metadata
        deleted_metadata = {
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "deleted_by": "operator",
            "soft_delete": True,
        }

        await db_service.update_task_status(
            task_id, "cancelled", result=json.dumps({"metadata": deleted_metadata})
        )

        logger.info("Task %s deleted successfully", task_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete task") from e


# ============================================================================
# SUB-ROUTERS
# ============================================================================
# Imported late to avoid circular imports — these routers depend on `router`.
from routes.task_publishing_routes import publishing_router  # noqa: E402
from routes.task_status_routes import status_router  # noqa: E402

router.include_router(status_router)
router.include_router(publishing_router)
