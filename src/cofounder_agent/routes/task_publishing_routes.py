"""
Task Publishing Routes - Approve, publish, reject, and image generation.

Sub-router for task_routes.py. Handles:
- POST /{task_id}/approve — Approve task for publishing
- POST /{task_id}/publish — Publish approved task
- POST /{task_id}/reject — Reject task for revision
- POST /{task_id}/generate-image — Generate or fetch image for task
"""

import asyncio
import json
import os
import re
import uuid as uuid_lib
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from routes.task_routes import _check_task_ownership
from schemas.model_converter import ModelConverter
from schemas.unified_task_response import UnifiedTaskResponse
from services.database_service import DatabaseService
from services.logger_config import get_logger
from utils.json_encoder import convert_decimals, safe_json_dumps
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)

publishing_router = APIRouter(tags=["Task Publishing"])


async def _embed_published_post(db_service: DatabaseService, post_dict: dict) -> None:
    """Embed a newly published post into pgvector as a background task.

    Non-blocking: if Ollama or pgvector is unavailable, logs a warning
    and returns silently so the publish flow is never interrupted.
    """
    try:
        from plugins.registry import get_llm_providers
        from services.embedding_service import EmbeddingService

        embeddings_db = getattr(db_service, "embeddings", None)
        if not embeddings_db:
            logger.debug("[RAG] Skipping post embedding: embeddings DB not available")
            return

        # v2.2b: Provider Protocol instead of concrete OllamaClient.
        # ``plugin.llm_provider.primary.free`` in app_settings decides
        # which backend does the embedding without editing this code.
        providers = {p.name: p for p in get_llm_providers()}
        provider = providers.get("ollama_native")
        if provider is None:
            logger.debug("[RAG] Skipping post embedding: ollama_native provider not registered")
            return

        embedding_svc = EmbeddingService(provider=provider, embeddings_db=embeddings_db)
        await embedding_svc.embed_post(post_dict)
        logger.info("[RAG] Embedded published post for future RAG: %s", post_dict.get("title", "")[:60])
    except Exception as e:
        logger.warning("[RAG] Failed to embed published post (non-fatal): %s", e)


async def _sync_published_post(post_id: str) -> None:
    """Push a newly published post to the cloud DB as a background task.

    Non-blocking: if either database is unreachable, logs a warning
    and returns silently so the publish flow is never interrupted.
    Skipped entirely when LOCAL_DATABASE_URL is not set (coordinator mode).
    """
    if not os.getenv("LOCAL_DATABASE_URL"):
        logger.debug("[SYNC] Skipping post sync: LOCAL_DATABASE_URL not set (coordinator mode)")
        return

    try:
        from services.sync_service import SyncService

        async with SyncService() as sync:
            ok = await sync.push_post(post_id)
            if ok:
                logger.info("[SYNC] Pushed published post to cloud DB: %s", post_id)
            else:
                logger.warning("[SYNC] push_post returned False for post %s", post_id)
    except Exception as e:
        logger.warning("[SYNC] Failed to sync published post (non-fatal): %s", e)


def _should_run_post_publish_hooks() -> bool:
    """Return True if post-publish hooks (sync + embed) should run.

    They require LOCAL_DATABASE_URL to be set, meaning we are on the
    local workstation with the brain DB. On the cloud coordinator,
    LOCAL_DATABASE_URL is absent and hooks are silently skipped.
    """
    return bool(os.getenv("LOCAL_DATABASE_URL"))


# ============================================================================
# CONTENT CLEANING UTILITIES
# ============================================================================
# extract_title_from_content is imported from utils.text_utils (canonical copy).


def clean_generated_content(content: str, title: str = "") -> str:
    """
    Clean up LLM-generated content by removing:
    - Leading markdown titles (# Title, ## Title)
    - "Introduction:" prefixes
    - Duplicate title text
    - Extra whitespace

    Args:
        content: Raw generated content from LLM
        title: Blog post title to remove if it appears in content

    Returns:
        Cleaned content ready for publishing
    """
    if not content:
        return content

    # Remove markdown-style titles at the start
    # Remove leading # or ## followed by space and text (with optional title match)
    content = re.sub(r"^#+\s+[^\n]*\n?", "", content.strip())

    # Remove "Title:" or "Title: " at the very beginning
    content = re.sub(r"^Title:\s*", "", content)

    # Remove common section prefixes if they appear as standalone lines
    content = re.sub(r"^\s*Introduction:\s*\n?", "", content, flags=re.MULTILINE)
    content = re.sub(r"^\s*Conclusion:\s*\n?", "", content, flags=re.MULTILINE)

    # Remove leftover [IMAGE-N] placeholders that weren't replaced with actual images
    content = re.sub(r"\[IMAGE-\d+\]", "", content)

    # Strip AI-generated "Recommended External Resources" sections (and variants)
    # These are generic "go read the docs" links that signal AI generation
    content = re.sub(
        r"\n##?\s*(Suggested|Recommended)\s*(External\s*)?(Resources|Links|URLs|Sources).*",
        "", content, flags=re.DOTALL | re.IGNORECASE,
    )

    # If a title was provided, remove it if it appears as a standalone paragraph
    if title:
        # Escape special regex characters in title
        title_escaped = re.escape(title)
        # Remove the title if it appears on its own line
        content = re.sub(
            rf"^\s*{title_escaped}\s*\n+", "", content, flags=re.MULTILINE | re.IGNORECASE
        )

    # Remove extra blank lines (more than 2 consecutive newlines)
    content = re.sub(r"\n{3,}", "\n\n", content)

    # Strip leading/trailing whitespace
    content = content.strip()

    return content


# ============================================================================
# TASK APPROVAL & PUBLISHING ENDPOINTS
# ============================================================================


@publishing_router.post(
    "/{task_id}/approve", response_model=UnifiedTaskResponse, summary="Approve task for publishing"
)
async def approve_task(
    task_id: str,
    approved: bool = True,
    human_feedback: str | None = None,
    reviewer_id: str | None = None,
    featured_image_url: str | None = None,
    image_source: str | None = None,
    auto_publish: bool = True,
    publish_at: str | None = None,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Approve or reject a task for publishing.

    Changes task status from 'awaiting_approval' to 'approved' or 'rejected'.
    Can include human feedback, image URL, and reviewer information.
    Publishing is now a SEPARATE step - call /publish endpoint to publish.

    **Parameters:**
    - task_id: Task ID (UUID or numeric ID for backwards compatibility)
    - approved: Boolean - true to approve, false to reject
    - human_feedback: Optional feedback from reviewer
    - reviewer_id: Optional ID of reviewer
    - featured_image_url: Optional featured image URL for the task
    - image_source: Optional source of image (pexels, sdxl)
    - auto_publish: Automatically publish after approval (default: true - approve = publish)

    **Returns:**
    - Updated task with status 'approved' or 'rejected' (and 'published' if auto_publish=true)

    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/approve \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer YOUR_JWT_TOKEN" \
      -d '{
        "approved": true,
        "human_feedback": "Great content!",
        "reviewer_id": "user123",
        "featured_image_url": "https://...",
        "image_source": "pexels",
        "auto_publish": true
      }'
    ```
    """
    try:
        # Accept UUID, numeric ID, or short prefix (first 6+ chars of UUID) (#176)
        try:
            UUID(task_id)
        except ValueError:
            if len(task_id) >= 6 and hasattr(db_service, 'pool') and db_service.pool:
                resolved = await db_service.pool.fetchval(
                    "SELECT task_id FROM pipeline_tasks WHERE task_id::text LIKE $1 || '%' LIMIT 1",
                    task_id,
                )
                if resolved:
                    task_id = str(resolved)
                elif not task_id.isdigit():
                    raise HTTPException(status_code=400, detail=f"No task found matching prefix '{task_id}'") from None
            elif not task_id.isdigit():
                raise HTTPException(status_code=400, detail="Invalid task ID format") from None

        # Fetch task
        task = await db_service.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Check if task is in a state that can be approved/rejected
        current_status = task.get("status", "unknown")
        allowed_statuses = [
            "awaiting_approval",
            "completed",  # QA-passed tasks that haven't been reviewed yet
        ]
        if current_status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve/reject task with status '{current_status}'. Task status is not in approvable state.",
            )

        # Prepare metadata
        approval_metadata = {
            "approved_at" if approved else "rejected_at": datetime.now(timezone.utc).isoformat(),
            "approved_by" if approved else "rejected_by": reviewer_id or "operator",
        }

        if human_feedback:
            approval_metadata["human_feedback"] = human_feedback

        if image_source:
            approval_metadata["image_source"] = image_source

        # Update task result with featured image and content from task_metadata
        # 🔑 CRITICAL: Read from task_metadata for failed/partially-generated tasks
        task_metadata = task.get("task_metadata", {})
        if isinstance(task_metadata, str):
            try:
                task_metadata = json.loads(task_metadata) if task_metadata else {}
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "[get_task_detail] task_metadata is not valid JSON for task %s — defaulting to {}",
                    task.get("id"),
                )
                task_metadata = {}
        elif task_metadata is None:
            task_metadata = {}
        elif not isinstance(task_metadata, dict):
            task_metadata = {}

        # Read from result field, but fallback to task_metadata if result is empty
        task_result = task.get("result", {})
        if isinstance(task_result, str):
            try:
                task_result = json.loads(task_result) if task_result else {}
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "[get_task_detail] result is not valid JSON for task %s — defaulting to {}",
                    task.get("id"),
                )
                task_result = {}
        elif task_result is None:
            task_result = {}
        elif not isinstance(task_result, dict):
            task_result = {}

        # ✅ Merge task_metadata into task_result to preserve all data from generation
        # This ensures content and images from failed tasks are preserved through approval
        merged_result = {**task_metadata, **task_result}

        if featured_image_url:
            merged_result["featured_image_url"] = featured_image_url

        # Update task status and result
        new_status = "approved" if approved else "rejected"
        logger.info(
            "%s task %s (current status: %s)",
            "Approving" if approved else "Rejecting", task_id, current_status,
        )
        logger.info("   Has featured_image_url: %s", bool(merged_result.get("featured_image_url")))
        logger.info("   Has content: %s", bool(merged_result.get("content")))

        # Convert any Decimal values to float before JSON serialization
        safe_result = convert_decimals({"metadata": approval_metadata, **merged_result})

        try:
            await db_service.update_task_status(
                task_id, new_status, result=safe_json_dumps(safe_result)
            )
        except Exception as e:
            logger.error("Failed to update task status to %s: %s", new_status, e, exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to update task status") from e

        # NOTE: staging_mode is still present in app_settings but no longer
        # gates publish — approval now goes live immediately. The setting is
        # reserved for a future scheduling / release-time-optimization feature
        # that will honor pacing and scheduled slots instead of the previous
        # "create a draft and wait for a second publish step" semantics.

        # Scheduled publish: write publish_at to scheduled_at, skip immediate publish
        if approved and publish_at:
            try:
                from datetime import datetime as _dt
                _pa = _dt.fromisoformat(publish_at.replace("Z", "+00:00"))
                await db_service.pool.execute(
                    "UPDATE pipeline_tasks SET scheduled_at = $1 WHERE task_id = $2::uuid OR id = $3",
                    _pa, task_id if len(task_id) > 10 else None, int(task_id) if task_id.isdigit() else 0,
                )
                logger.info("Scheduled task %s for publish at %s", task_id, _pa.isoformat())
                auto_publish = False
            except Exception as e:
                logger.warning("Failed to parse publish_at '%s': %s — publishing immediately", publish_at, e)

        if approved and auto_publish:
            logger.info("Publishing approved task %s (approve → go-live)", task_id)
            try:
                from services.publish_service import publish_post_from_task

                pub_result = await publish_post_from_task(
                    db_service, task, task_id,
                    publisher="operator",
                    trigger_revalidation=True,
                    queue_social=True,
                    draft_mode=False,
                    honor_pacing=False,
                )
                if pub_result.success:
                    merged_result["post_id"] = pub_result.post_id
                    merged_result["post_slug"] = pub_result.post_slug
                    merged_result["published_url"] = pub_result.published_url
                else:
                    logger.warning(
                        "[approve_task] Publish failed: %s", pub_result.error
                    )
            except Exception as e:
                logger.critical(
                    "Unexpected error during publish: %s: %s",
                    type(e).__name__, e, exc_info=True,
                )
                # Don't fail approval if publish fails — the task is still
                # marked approved and a human can retry publish via /publish.

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Extract published info from result if available
        if updated_task is None:
            updated_task = {}
        task_result_data = updated_task.get("result", {})
        if isinstance(task_result_data, str):
            task_result_data = json.loads(task_result_data) if task_result_data else {}

        published_url = task_result_data.get("published_url")
        post_id = task_result_data.get("post_id")
        post_slug = task_result_data.get("post_slug")

        # Convert to response schema
        response_data = ModelConverter.task_response_to_unified(
            ModelConverter.to_task_response(updated_task)
        )

        # Add published URL info to response
        if published_url:
            response_data["published_url"] = published_url
        if post_id:
            response_data["post_id"] = post_id
        if post_slug:
            response_data["post_slug"] = post_slug

        return UnifiedTaskResponse(**response_data)

    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as e:
        logger.error(
            "Data validation error in approve_task: %s: %s", type(e).__name__, e, exc_info=True
        )
        raise HTTPException(status_code=400, detail="Invalid task data") from e
    except Exception as e:
        logger.error(
            "Failed to approve task %s: %s: %s", task_id, type(e).__name__, e, exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to approve task") from e


@publishing_router.post(
    "/{task_id}/publish", response_model=UnifiedTaskResponse, summary="Publish approved task"
)
async def publish_task(
    task_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
):
    """
    Publish an approved task to specified channels.

    Changes task status from 'approved' to 'published'.
    Handles distribution to CMS, social media, email, etc.

    **Parameters:**
    - task_id: Task UUID

    **Returns:**
    - Updated task with status 'published'

    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/publish \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        # Accept UUID, numeric ID, or short prefix (first 6+ chars of UUID) (#176)
        try:
            UUID(task_id)
        except ValueError:
            if len(task_id) >= 6 and hasattr(db_service, 'pool') and db_service.pool:
                resolved = await db_service.pool.fetchval(
                    "SELECT task_id FROM pipeline_tasks WHERE task_id::text LIKE $1 || '%' LIMIT 1",
                    task_id,
                )
                if resolved:
                    task_id = str(resolved)
                elif not task_id.isdigit():
                    raise HTTPException(status_code=400, detail=f"No task found matching prefix '{task_id}'") from None
            elif not task_id.isdigit():
                raise HTTPException(status_code=400, detail="Invalid task ID format") from None

        # Fetch task
        task = await db_service.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Check if task is approved
        current_status = task.get("status", "unknown")
        if current_status != "approved":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot publish task with status '{current_status}'. Must be 'approved'.",
            )

        # Publish via shared service (post creation, ISR, social, sync, embed)
        logger.info("Publishing task %s", task_id)
        from services.publish_service import publish_post_from_task

        pub_result = await publish_post_from_task(
            db_service, task, task_id,
            publisher="operator",
            trigger_revalidation=True,
            queue_social=True,
            background_tasks=background_tasks,
        )

        if not pub_result.success:
            logger.error("[publish_task] Post creation failed: %s", pub_result.error)
            # Task stays in approved state — don't fail the request entirely
            # but warn about the issue

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Convert to response schema
        try:
            response_data = ModelConverter.task_response_to_unified(
                ModelConverter.to_task_response(updated_task)
            )
            if pub_result.published_url:
                response_data["published_url"] = pub_result.published_url
            if pub_result.post_id:
                response_data["post_id"] = pub_result.post_id
            if pub_result.post_slug:
                response_data["post_slug"] = pub_result.post_slug
            response_data["revalidation"] = {
                "triggered": True,
                "success": pub_result.revalidation_success,
            }
            return UnifiedTaskResponse(**response_data)
        except Exception as resp_err:
            logger.warning(
                "[publish_task] Response model conversion failed (%s); returning minimal response",
                resp_err, exc_info=True,
            )
            return {  # type: ignore[return-value]
                "id": task_id,
                "status": "published",
                "published_url": pub_result.published_url,
                "post_id": pub_result.post_id,
                "post_slug": pub_result.post_slug,
                "revalidation": {"triggered": True, "success": pub_result.revalidation_success},
                "message": "Task published successfully",
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to publish task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to publish task") from e


@publishing_router.post(
    "/{post_id}/go-live", summary="Promote a draft post to published (go live)"
)
async def go_live(
    post_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config_dep = Depends(get_site_config_dependency),
):
    """Promote a draft post to published status. Triggers RSS, social, revalidation."""
    pool = getattr(db_service, "cloud_pool", None) or db_service.pool

    # Verify post exists and is a draft
    row = await pool.fetchrow(
        "SELECT id, title, slug, status FROM posts WHERE id::text = $1", post_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Post not found")
    if row["status"] != "draft":
        raise HTTPException(status_code=400, detail=f"Post is '{row['status']}', not 'draft'")

    # Promote to published
    from datetime import datetime, timezone
    await pool.execute(
        """UPDATE posts SET status = 'published', published_at = $1, preview_token = NULL
           WHERE id::text = $2""",
        datetime.now(timezone.utc), post_id,
    )

    # Trigger ISR revalidation on the public site so Vercel busts its cache.
    # Includes both routes (revalidatePath) and tags (revalidateTag).
    # The tags are critical — revalidatePath alone does NOT invalidate the
    # data cache keyed by fetch URL, so null responses from before publish
    # stick around for the 5-minute TTL.
    try:
        from routes.revalidate_routes import trigger_nextjs_revalidation
        reval_ok = await trigger_nextjs_revalidation(
            paths=[
                f"/posts/{row['slug']}",
                "/",
                "/archive",
                "/archive/1",
                "/posts",
            ],
            tags=[
                "posts",
                "post-index",
                f"post:{row['slug']}",
            ],
        )
        if reval_ok:
            logger.info("[GO-LIVE] ISR revalidation triggered for %s", row["slug"])
        else:
            logger.warning("[GO-LIVE] ISR revalidation returned failure for %s", row["slug"])
    except Exception:
        logger.warning("[GO-LIVE] Revalidation call raised (non-fatal)", exc_info=True)

    # Queue social/podcast/video (they check for existing files)
    if _should_run_post_publish_hooks():
        try:
            from services.task_executor import _notify_openclaw
            _site_url = site_config_dep.require("site_url")
            await _notify_openclaw(
                f"🚀 Published: \"{row['title']}\"\n{_site_url}/posts/{row['slug']}",
                critical=True,
            )
        except Exception:
            logger.warning("[GO-LIVE] Openclaw notification failed (non-fatal)", exc_info=True)

    logger.info("[GO-LIVE] Post %s promoted to published: %s", post_id, row["slug"])
    return {
        "success": True,
        "post_id": post_id,
        "slug": row["slug"],
        "published_url": f"/posts/{row['slug']}",
        "message": "Post is now live",
    }


@publishing_router.post(
    "/{task_id}/reject", response_model=UnifiedTaskResponse, summary="Reject task for revision"
)
async def reject_task(
    task_id: str,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
):
    """
    Reject a task and send it back for revision.

    Changes task status to 'rejected' with optional feedback.
    Task can be revised and resubmitted.

    **Parameters:**
    - task_id: Task UUID or numeric ID

    **Returns:**
    - Updated task with status 'rejected'

    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/reject \
      -H "Authorization: Bearer YOUR_JWT_TOKEN"
    ```
    """
    try:
        # Accept UUID, numeric ID, or short prefix (first 6+ chars of UUID) (#176)
        try:
            UUID(task_id)
        except ValueError:
            if len(task_id) >= 6 and hasattr(db_service, 'pool') and db_service.pool:
                resolved = await db_service.pool.fetchval(
                    "SELECT task_id FROM pipeline_tasks WHERE task_id::text LIKE $1 || '%' LIMIT 1",
                    task_id,
                )
                if resolved:
                    task_id = str(resolved)
                elif not task_id.isdigit():
                    raise HTTPException(status_code=400, detail=f"No task found matching prefix '{task_id}'") from None
            elif not task_id.isdigit():
                raise HTTPException(status_code=400, detail="Invalid task ID format") from None

        # Fetch task
        task = await db_service.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Check if task is in a state that can be rejected
        current_status = task.get("status", "unknown")
        if current_status not in ["completed", "approved", "awaiting_approval"]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reject task with status '{current_status}'. Must be 'completed', 'approved', or 'awaiting_approval'.",
            )

        # Update task status to rejected
        logger.info("Rejecting task %s (current status: %s)", task_id, current_status)
        reject_metadata = {
            "rejected_at": datetime.now(timezone.utc).isoformat(),
            "rejected_by": "operator",
        }
        await db_service.update_task_status(
            task_id, "rejected", result=json.dumps({"metadata": reject_metadata})
        )

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Convert to response schema
        return UnifiedTaskResponse(
            **ModelConverter.task_response_to_unified(ModelConverter.to_task_response(updated_task))
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to reject task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reject task") from e


class GenerateImageRequest(BaseModel):
    """Request model for image generation"""

    source: str = "pexels"  # "pexels" or "sdxl"
    topic: str | None = None
    content_summary: str | None = None
    page: int = 1  # Pagination for Pexels results (1-based)


@publishing_router.post(
    "/{task_id}/generate-image",
    response_model=dict,
    summary="Generate or fetch image for task",
    tags=["content"],
)
async def generate_task_image(
    task_id: str,
    request: GenerateImageRequest,
    token: str = Depends(verify_api_token),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config_dep = Depends(get_site_config_dependency),
) -> dict[str, str]:
    """
    Generate or fetch an image for a task using Pexels or SDXL.

    **Request Body Parameters:**
    - source: Image source - "pexels" or "sdxl" (default: "pexels")
    - topic: Topic for image search/generation (optional)
    - content_summary: Summary of content for image generation (optional)

    **Returns:**
    - { "image_url": "https://..." }

    **Example cURL:**
    ```bash
    curl -X POST http://localhost:8000/api/tasks/550e8400-e29b-41d4-a716-446655440000/generate-image \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer YOUR_JWT_TOKEN" \
      -d '{
        "source": "sdxl",
        "topic": "AI Marketing",
        "content_summary": "How AI is transforming marketing..."
      }'
    ```
    """
    try:
        # Validate task exists
        task = await db_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Extract source from request for consistency
        source = request.source
        logger.info("Generating image for task %s using %s", task_id, source)

        image_url = None

        if source == "pexels":
            # Use Pexels API to search for images
            try:
                import aiohttp

                pexels_key = site_config_dep.get("pexels_api_key")
                if not pexels_key:
                    raise HTTPException(status_code=400, detail="Pexels API key not configured")

                search_query = request.topic or task.get("topic", "business")
                current_image_url = task.get("featured_image_url")
                page = max(1, request.page)  # Ensure page is at least 1

                logger.info("Pexels API request:")
                logger.info("   - Query: '%s'", search_query)
                logger.info("   - Page: %s", page)
                logger.info("   - Per page: 50")
                if current_image_url:
                    logger.info("   - Current featured image: %s...", current_image_url[:80])
                else:
                    logger.info("   - No current image")

                _pex_base = site_config_dep.get(
                    "pexels_api_base", "https://api.pexels.com/v1",
                ).rstrip("/")
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{_pex_base}/search",
                        params={
                            "query": search_query,
                            "per_page": 50,  # Get more results for better variety
                            "page": page,
                            "orientation": "landscape",
                        },
                        headers={"Authorization": pexels_key},
                        timeout=10.0,  # type: ignore[arg-type]
                    ) as resp:
                        if resp.status == 200:
                            try:
                                import random

                                data = await resp.json()
                                logger.info("Pexels API response: %s OK", resp.status)
                                logger.info("   - Response size: %s bytes", len(str(data)))

                                if data.get("photos"):
                                    photos_count = len(data.get("photos", []))
                                    logger.info("   - Total photos in response: %s", photos_count)
                                    # Filter out the current image URL AND recent images to get variety
                                    # Get list of recently used image URLs from task metadata
                                    recently_used = []
                                    if task_metadata := task.get("task_metadata"):
                                        if isinstance(task_metadata, dict):
                                            recently_used = task_metadata.get(
                                                "recent_image_urls", []
                                            )
                                        elif isinstance(task_metadata, str):
                                            try:
                                                meta = json.loads(task_metadata)
                                                recently_used = meta.get("recent_image_urls", [])
                                            except json.JSONDecodeError:
                                                logger.warning(
                                                    "[update_task_image] task_metadata is not valid JSON for task %s — skipping recent_image_urls",
                                                    task_id,
                                                )
                                                recently_used = []

                                    # Create comprehensive exclusion list
                                    excluded_urls = set(recently_used) if recently_used else set()
                                    if current_image_url:
                                        excluded_urls.add(current_image_url)

                                    logger.debug(
                                        "Image filtering: Pexels returned %s photos, "
                                        "currently using: %s, excluded: %s URLs",
                                        len(data["photos"]), current_image_url, len(excluded_urls),
                                    )

                                    # Filter out any previously used images
                                    photos = [
                                        p
                                        for p in data["photos"]
                                        if p["src"]["large"] not in excluded_urls
                                    ]

                                    logger.info(
                                        "   - After filtering: %s available photos", len(photos)
                                    )

                                    # If no new images available (rare), use the original list
                                    if not photos:
                                        logger.warning(
                                            "No new images after filtering, using all %s available",
                                            len(data["photos"]),
                                        )
                                        photos = data["photos"]

                                    if photos:
                                        # Randomly select instead of always picking first
                                        photo = random.choice(photos)
                                        image_url = photo["src"]["large"]
                                        logger.info(
                                            "Selected image #%s: %s", photos.index(photo) + 1, image_url
                                        )
                                        logger.info(
                                            "   - Photographer: %s", photo.get("photographer", "Unknown")
                                        )
                                        logger.info("   - Source: %s", photo["src"]["original"])

                                        # Store image URL and metadata in task for persistence
                                        # Track this image URL in recent_image_urls for future filtering
                                        updated_recent_urls = (
                                            [*recently_used, image_url]
                                            if recently_used
                                            else [image_url]
                                        )
                                        # Keep only last 10 images to avoid list getting too long
                                        updated_recent_urls = updated_recent_urls[-10:]

                                        await db_service.update_task(
                                            task_id,
                                            {
                                                "featured_image_url": image_url,
                                                "task_metadata": {
                                                    "featured_image_url": image_url,
                                                    "featured_image_source": "pexels",
                                                    "featured_image_photographer": photo.get(
                                                        "photographer", "Unknown"
                                                    ),
                                                    "recent_image_urls": updated_recent_urls,
                                                },
                                            },
                                        )
                            except json.JSONDecodeError as je:
                                logger.error(
                                    "Failed to parse Pexels response JSON: %s", je, exc_info=True
                                )
                                raise ValueError(f"Invalid JSON from Pexels API: {je!s}") from je
                        elif resp.status == 429:
                            logger.warning("Pexels rate limit exceeded")
                            raise HTTPException(
                                status_code=429,
                                detail="Image service rate limit exceeded. Please try again later.",
                            )
                        else:
                            logger.warning("Pexels API returned %s", resp.status)
                            raise ValueError(f"Pexels API error: HTTP {resp.status}")

            except ValueError as ve:
                logger.error("Pexels API error: %s", ve, exc_info=True)
                raise HTTPException(
                    status_code=500, detail="Error fetching image from Pexels"
                ) from ve
            except asyncio.TimeoutError as exc:
                logger.warning("Pexels API timeout for query: %s", search_query, exc_info=True)
                raise HTTPException(status_code=504, detail="Pexels API timeout. Please try again.") from exc
            except Exception as e:
                logger.error(
                    "Unexpected error fetching from Pexels: %s: %s", type(e).__name__, e, exc_info=True
                )
                raise HTTPException(
                    status_code=500, detail="Unexpected error fetching image from Pexels"
                ) from e

        elif source == "sdxl":
            # Use SDXL to generate an image
            try:
                from pathlib import Path

                from services.image_service import ImageService

                image_service = ImageService()

                # Build generation prompt from topic and content
                generation_prompt = f"{request.topic}"
                if request.content_summary:
                    # Extract key concepts from content summary
                    generation_prompt = f"{request.topic}: {request.content_summary[:200]}"

                logger.info("Generating image with SDXL: %s", generation_prompt)

                # Save to user's Downloads folder for preview
                downloads_path = str(Path.home() / "Downloads" / "glad-labs-generated-images")
                os.makedirs(downloads_path, exist_ok=True)

                # Create filename with UUID to prevent collisions (UUID instead of timestamp)
                unique_id = str(uuid_lib.uuid4())[:8]
                output_file = f"sdxl_{unique_id}.png"
                output_path = os.path.join(downloads_path, output_file)

                logger.info("Generating SDXL image to: %s", output_path)

                # Generate image with SDXL
                success = await image_service.generate_image(
                    prompt=generation_prompt,
                    output_path=output_path,
                    num_inference_steps=50,  # Good quality/speed balance
                    guidance_scale=7.5,
                    task_id=task_id,
                )

                if success and os.path.exists(output_path):
                    logger.info("SDXL image generated: %s", output_path)
                    image_url = output_path
                    logger.info("   Generated image saved locally for preview")
                else:
                    raise RuntimeError("SDXL image generation failed or file not created")

            except asyncio.TimeoutError as exc:
                logger.warning("SDXL image generation timeout for task %s", task_id, exc_info=True)
                raise HTTPException(
                    status_code=408,
                    detail="Image generation timeout. Please try again with 'pexels' source.",
                ) from exc
            except (OSError, RuntimeError, ValueError) as e:
                logger.error(
                    "SDXL image generation error - %s: %s", type(e).__name__, e, exc_info=True
                )
                raise HTTPException(
                    status_code=500,
                    detail="SDXL image generation failed. Ensure GPU available or use 'pexels' source.",
                ) from e
            except Exception as e:
                logger.critical(
                    "Unexpected error in SDXL generation: %s: %s", type(e).__name__, e, exc_info=True
                )
                raise HTTPException(
                    status_code=500, detail="Internal server error during image generation"
                ) from e
        else:
            raise HTTPException(
                status_code=400, detail=f"Invalid image source: {source}. Use 'pexels' or 'sdxl'"
            )

        if not image_url:
            raise HTTPException(status_code=500, detail="Failed to generate or fetch image")

        # Update task metadata with generated image URL
        task_result = task.get("result")

        # Handle None, empty string, or JSON string
        if task_result is None:
            task_result = {}
        elif isinstance(task_result, str):
            try:
                task_result = json.loads(task_result) if task_result.strip() else {}
            except (json.JSONDecodeError, AttributeError):
                logger.warning(
                    "[update_task_featured_image] result is not valid JSON for task %s — defaulting to {}",
                    task_id,
                )
                task_result = {}
        elif not isinstance(task_result, dict):
            task_result = {}

        # Also update task_metadata for consistency
        task_metadata = task.get("task_metadata", {})
        if isinstance(task_metadata, str):
            try:
                task_metadata = json.loads(task_metadata) if task_metadata.strip() else {}
            except (json.JSONDecodeError, AttributeError):
                logger.warning(
                    "[update_task_featured_image] task_metadata is not valid JSON for task %s — defaulting to {}",
                    task_id,
                )
                task_metadata = {}

        task_result["featured_image_url"] = image_url
        task_metadata["featured_image_url"] = image_url

        await db_service.update_task(
            task_id,
            {
                "result": safe_json_dumps(task_result),
                "task_metadata": safe_json_dumps(task_metadata),
            },
        )

        return {
            "image_url": image_url,
            "source": source,
            "message": f"✅ Image generated/fetched from {source}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to generate image for task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate image") from e
