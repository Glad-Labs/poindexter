"""
Typed-Record layer for the most-called SQL helpers.

These ``TypedDict`` shapes mirror the Pydantic models in
:mod:`schemas.database_response_models` but cost nothing at runtime.
They exist because the legacy helpers return ``dict[str, Any]`` to
preserve subscription-style access (``task["status"]``) for callers
that pre-date the Pydantic layer. Annotating those returns as a
proper TypedDict gives mypy / pyright field-level knowledge without
forcing every consumer to migrate to attribute-style access.

When to reach for which:

* **Pydantic model** (e.g. ``TaskResponse``) — at API / route boundaries
  where validation, OpenAPI schema, and JSON serialization all matter.
* **TypedDict** (this module) — for the in-process dict that internal
  callers already iterate as ``row["field"]``. Pure type info, no
  ``BaseModel`` overhead per call.

The two are deliberately structurally compatible: every TypedDict here
covers the same field set as the Pydantic peer, with ``total=False`` so
callers can pass any subset (consistent with how rows from views like
``content_tasks`` come back with nullable columns).

Reference: glad-labs-stack #201 (top-5 helper typing pass).
"""

from datetime import datetime
from typing import Any, TypedDict


# ============================================================================
# TASK RECORD
# ============================================================================


class TaskRecord(TypedDict, total=False):
    """Dict shape returned by :meth:`TasksDatabase.get_task` and friends.

    Mirrors :class:`schemas.database_response_models.TaskResponse` but as a
    TypedDict so callers doing ``task["status"]`` keep working unchanged
    while still getting field-level type info from mypy / pyright.

    Every key is ``NotRequired`` (``total=False``) because the
    ``content_tasks`` view projects nullable columns and ModelConverter
    can drop keys that came through as ``None`` after Pydantic
    serialization. Treat absence and ``None`` as equivalent at read sites.
    """

    id: str
    task_id: str | None
    user_id: str | None
    title: str | None
    task_name: str | None
    description: str | None
    topic: str | None
    request_type: str | None
    task_type: str | None
    status: str | None
    category: str | None
    niche_slug: str | None
    priority: int | None
    style: str | None
    tone: str | None
    target_length: int | None
    primary_keyword: str | None
    target_audience: str | None
    content: str | None
    excerpt: str | None
    featured_image_url: str | None
    featured_image_data: dict[str, Any] | None
    featured_image_prompt: str | None
    qa_feedback: str | None
    quality_score: float | None
    seo_title: str | None
    seo_description: str | None
    seo_keywords: str | None
    stage: str | None
    percentage: int | None
    message: str | None
    agent_id: str | None
    model_used: str | None
    error_message: str | None
    tags: list[str] | None
    task_metadata: dict[str, Any] | None
    result: dict[str, Any] | None
    progress: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cost_breakdown: dict[str, Any] | None
    post_id: str | None
    post_slug: str | None
    published_url: str | None


# ============================================================================
# POST RECORD
# ============================================================================


class PostRecord(TypedDict, total=False):
    """Dict shape for published posts.

    Mirrors :class:`schemas.database_response_models.PostResponse`. Used
    when callers receive a post via ``model_dump()`` or via a helper that
    returns the dict form for compatibility.
    """

    id: str
    title: str
    slug: str
    content: str
    excerpt: str | None
    featured_image_url: str | None
    cover_image_url: str | None
    author_id: str | None
    category_id: str | None
    tag_ids: list[str] | None
    status: str
    seo_title: str | None
    seo_description: str | None
    seo_keywords: str | None
    created_by: str | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime


# ============================================================================
# USER RECORD
# ============================================================================


class UserRecord(TypedDict, total=False):
    """Dict shape for user profile rows.

    Mirrors :class:`schemas.database_response_models.UserResponse`.
    """

    id: str
    email: str
    username: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


# ============================================================================
# COST BREAKDOWN RECORD
# ============================================================================


class CostBreakdownRecord(TypedDict, total=False):
    """Per-task cost breakdown.

    Mirrors :class:`schemas.database_response_models.TaskCostBreakdownResponse`.
    Phase keys (``research``, ``outline``, ``draft``, …) each carry a
    ``{cost, model, count}`` blob that the dashboards aggregate.
    """

    total: float
    research: dict[str, Any] | None
    outline: dict[str, Any] | None
    draft: dict[str, Any] | None
    assess: dict[str, Any] | None
    refine: dict[str, Any] | None
    finalize: dict[str, Any] | None
    entries: list[dict[str, Any]] | None


# ============================================================================
# TASK COUNTS RECORD
# ============================================================================


class TaskCountsRecord(TypedDict, total=False):
    """Status-bucketed task counts.

    Mirrors :class:`schemas.database_response_models.TaskCountsResponse`.
    """

    total: int
    pending: int
    in_progress: int
    completed: int
    failed: int
    awaiting_approval: int
    approved: int


# ============================================================================
# PAGINATED RESULT
# ============================================================================


# Type alias for the (rows, total) tuple returned by paginated helpers.
# Kept as a plain alias instead of a TypedDict because the shape is a
# tuple, not a mapping — callers destructure as ``rows, total = await …``.
PaginatedTasksResult = tuple[list[TaskRecord], int]


__all__ = [
    "TaskRecord",
    "PostRecord",
    "UserRecord",
    "CostBreakdownRecord",
    "TaskCountsRecord",
    "PaginatedTasksResult",
]
