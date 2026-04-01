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
from typing import Dict, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from middleware.api_token_auth import verify_api_token
from routes.revalidate_routes import trigger_nextjs_revalidation
from routes.task_routes import _check_task_ownership
from schemas.model_converter import ModelConverter
from schemas.unified_task_response import UnifiedTaskResponse
from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.webhook_delivery_service import emit_webhook_event
from utils.json_encoder import convert_decimals, safe_json_dumps
from utils.route_utils import get_database_dependency
from utils.text_utils import extract_title_from_content

logger = get_logger(__name__)

publishing_router = APIRouter(tags=["Task Publishing"])


async def _embed_published_post(db_service: DatabaseService, post_dict: dict) -> None:
    """Embed a newly published post into pgvector as a background task.

    Non-blocking: if Ollama or pgvector is unavailable, logs a warning
    and returns silently so the publish flow is never interrupted.
    """
    try:
        from services.ollama_client import OllamaClient
        from services.embedding_service import EmbeddingService

        embeddings_db = getattr(db_service, "embeddings", None)
        if not embeddings_db:
            logger.debug("[RAG] Skipping post embedding: embeddings DB not available")
            return

        ollama = OllamaClient()
        embedding_svc = EmbeddingService(ollama_client=ollama, embeddings_db=embeddings_db)
        await embedding_svc.embed_post(post_dict)
        await ollama.close()
        logger.info("[RAG] Embedded published post for future RAG: %s", post_dict.get("title", "")[:60])
    except Exception as e:
        logger.warning("[RAG] Failed to embed published post (non-fatal): %s", e)


async def _sync_published_post(post_id: str) -> None:
    """Push a newly published post to the cloud Railway DB as a background task.

    Non-blocking: if either database is unreachable, logs a warning
    and returns silently so the publish flow is never interrupted.
    Skipped entirely when LOCAL_DATABASE_URL is not set (Railway coordinator mode).
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
    local workstation with the brain DB. On Railway (coordinator mode)
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
    human_feedback: Optional[str] = None,
    reviewer_id: Optional[str] = None,
    featured_image_url: Optional[str] = None,
    image_source: Optional[str] = None,
    auto_publish: bool = False,
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
    - auto_publish: Automatically publish after approval (default: false - publishing is manual)

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
        # Accept both UUID and numeric task IDs (backwards compatibility)
        try:
            UUID(task_id)
        except ValueError as exc:
            # If not a valid UUID, check if it's a numeric ID (legacy tasks)
            if not task_id.isdigit():
                raise HTTPException(status_code=400, detail="Invalid task ID format") from exc

        # Fetch task
        task = await db_service.get_task(task_id)

        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        # Ownership check
        if isinstance(task, dict):
            _check_task_ownership(task, token)

        # Check if task is in a state that can be approved/rejected
        current_status = task.get("status", "unknown")
        # Allow approval for multiple statuses: awaiting_approval (ideal), but also handle failed,
        # completed, pending tasks that may need approval decision
        allowed_statuses = [
            "awaiting_approval",
            "pending",
            "in_progress",
            "completed",
            "rejected",
            "failed",
            "approved",
            "published",
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
            f"{'Approving' if approved else 'Rejecting'} task {task_id} (current status: {current_status})"
        )
        logger.info(f"   Has featured_image_url: {bool(merged_result.get('featured_image_url'))}")
        logger.info(f"   Has content: {bool(merged_result.get('content'))}")

        # Convert any Decimal values to float before JSON serialization
        safe_result = convert_decimals({"metadata": approval_metadata, **merged_result})

        try:
            await db_service.update_task_status(
                task_id, new_status, result=safe_json_dumps(safe_result)
            )
        except Exception as e:
            logger.error(f"Failed to update task status to {new_status}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to update task status") from e

        # Auto-publish if approved and auto_publish=True
        if approved and auto_publish:
            logger.info(f"Auto-publishing approved task {task_id}")
            try:
                # IMPORTANT: Update task status to published FIRST, before creating post
                # This ensures task state is consistent even if post creation fails
                publish_metadata = {
                    "published_at": datetime.now(timezone.utc).isoformat(),
                    "published_by": "operator",
                }

                # Convert Decimals before serialization
                safe_publish_result = convert_decimals(
                    {"metadata": {**approval_metadata, **publish_metadata}, **merged_result}
                )

                try:
                    await db_service.update_task_status(
                        task_id, "published", result=safe_json_dumps(safe_publish_result)
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to update task status to published: {str(e)}", exc_info=True
                    )
                    # Still continue with post creation if status update fails
                    # The task will be in 'approved' state but post may be created

                # Create post in posts table when publishing (not before)
                logger.info(f"Creating posts table entry for published task {task_id}")
                try:
                    # Extract content from merged_result (includes both result and task_metadata)
                    topic = task.get("topic", "") or merged_result.get("topic", "")
                    draft_content = (
                        merged_result.get("draft_content", "")
                        or merged_result.get("content", "")
                        or ""
                    )
                    seo_description = merged_result.get("seo_description", "")
                    seo_keywords = merged_result.get("seo_keywords", [])
                    featured_image = featured_image_url or merged_result.get("featured_image_url")
                    metadata = merged_result.get("metadata", {})

                    # 🔑 EXTRACT TITLE: LLM often generates "#Title" at start of content
                    # Extract it and use as the post title, remove from content
                    extracted_title, cleaned_content = extract_title_from_content(draft_content)

                    # Use extracted title if available, otherwise fall back to topic
                    post_title = extracted_title or merged_result.get("title") or topic

                    # Use cleaned content (title removed)
                    post_content = cleaned_content

                    logger.info(f"📝 Post title: {post_title}")
                    logger.info(f"   Extracted from content: {bool(extracted_title)}")
                    logger.info(f"   Content length: {len(post_content or '')} chars")

                    if post_content and post_title:
                        # Create slug from title
                        slug = (
                            re.sub(r"[^\w\s-]", "", post_title)
                            .lower()
                            .replace(" ", "-")[:50]
                        )
                        slug = f"{slug}-{task_id[:8]}"

                        # Get author and category
                        from services.content_router_service import (
                            _get_or_create_default_author,
                            _select_category_for_topic,
                        )

                        author_id = await _get_or_create_default_author(db_service)
                        category_id = await _select_category_for_topic(post_title, db_service)

                        # Create post with status='published'
                        post = await db_service.create_post(
                            {
                                "title": post_title,
                                "slug": slug,
                                "content": post_content,
                                "excerpt": seo_description,
                                "featured_image_url": featured_image,
                                "author_id": author_id,
                                "category_id": category_id,
                                "status": "published",  # Published, not draft
                                "seo_title": post_title,
                                "seo_description": seo_description,
                                "seo_keywords": ",".join(seo_keywords) if seo_keywords else "",
                                "metadata": metadata,
                            }
                        )
                        logger.info(f"✅ Post created with status='published': {post.id}")  # type: ignore[attr-defined]
                        logger.info(f"   Title: {post_title}")
                        logger.info(f"   Slug: {slug}")
                        logger.info(
                            "[content_published] task_id=%s post_id=%s user_id=%s slug=%s",
                            task_id,
                            str(post.id) if hasattr(post, "id") else post.get("id"),  # type: ignore[attr-defined]
                            "operator",
                            slug,
                        )

                        # Store post info in merged_result for response
                        merged_result["post_id"] = (
                            str(post.id) if hasattr(post, "id") else str(post.get("id"))  # type: ignore[attr-defined]
                        )
                        merged_result["post_slug"] = slug
                        merged_result["published_url"] = (
                            f"/posts/{slug}"  # Relative URL for public site
                        )

                        # Emit webhook event for published post
                        try:
                            await emit_webhook_event(db_service.pool, "post.published", {
                                "task_id": str(task_id), "title": post_title, "site": "default",
                            })
                        except Exception:
                            logger.debug("[WEBHOOK] Failed to emit post.published event", exc_info=True)

                        # Fire-and-forget sync + embed (no BackgroundTasks available in approve)
                        if _should_run_post_publish_hooks():
                            _auto_pub_post_id = merged_result.get("post_id", "")
                            _auto_pub_post_dict = {
                                "id": _auto_pub_post_id,
                                "title": post_title,
                                "excerpt": seo_description,
                                "content": post_content,
                            }
                            asyncio.ensure_future(_sync_published_post(_auto_pub_post_id))
                            asyncio.ensure_future(_embed_published_post(db_service, _auto_pub_post_dict))
                            logger.info("[AUTO-PUBLISH] Queued sync + embed for post %s", _auto_pub_post_id)
                    else:
                        logger.warning("⚠️  Skipping post creation: missing content or topic")
                except (ValueError, KeyError, TypeError) as e:
                    # Catch specific exceptions from post creation
                    logger.error(
                        f"Failed to create post for published task: {type(e).__name__}: {str(e)}",
                        exc_info=True,
                    )
                    # Don't fail the publish operation if post creation fails
                    # Post table may have constraints or data issues, but task should stay published
                except Exception as e:
                    logger.critical(
                        f"Unexpected error creating post for published task: {type(e).__name__}: {str(e)}",
                        exc_info=True,
                    )
                    # Don't fail the publish operation if post creation fails

            except (ValueError, KeyError, TypeError) as e:
                logger.error(
                    f"Error during auto-publish process: {type(e).__name__}: {str(e)}",
                    exc_info=True,
                )
                # Don't fail approval if auto-publish fails
            except Exception as e:
                logger.critical(
                    f"Unexpected error during auto-publish: {type(e).__name__}: {str(e)}",
                    exc_info=True,
                )
                # Don't fail approval if auto-publish fails

        # Trigger ISR revalidation after auto-publish (#955)
        if approved and auto_publish:
            try:
                reval_paths = ["/", "/archive", "/posts"]
                slug_val = (
                    merged_result.get("post_slug") if isinstance(merged_result, dict) else None
                )
                if slug_val:
                    reval_paths.append(f"/posts/{slug_val}")
                await trigger_nextjs_revalidation(reval_paths)
            except Exception as reval_err:
                logger.warning(
                    f"[approve_task] ISR revalidation error (non-fatal): {reval_err}", exc_info=True
                )

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
            f"Data validation error in approve_task: {type(e).__name__}: {str(e)}", exc_info=True
        )
        raise HTTPException(status_code=400, detail="Invalid task data") from e
    except Exception as e:
        logger.error(
            f"Failed to approve task {task_id}: {type(e).__name__}: {str(e)}", exc_info=True
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
        # Accept both UUID and numeric task IDs (backwards compatibility)
        try:
            UUID(task_id)
        except ValueError as exc:
            # If not a valid UUID, check if it's a numeric ID (legacy tasks)
            if not task_id.isdigit():
                raise HTTPException(status_code=400, detail="Invalid task ID format") from exc

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

        # Update task status to published
        logger.info(f"Publishing task {task_id}")
        publish_metadata = {
            "published_at": datetime.now(timezone.utc).isoformat(),
            "published_by": "operator",
        }
        # Preserve existing task content: merge publish_metadata into result (do not overwrite)
        existing_result = task.get("result", {})
        if isinstance(existing_result, str):
            try:
                existing_result = json.loads(existing_result) if existing_result else {}
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "[publish_task] result is not valid JSON for task %s — defaulting to {}",
                    task_id,
                )
                existing_result = {}
        existing_result = existing_result or {}
        task_meta = task.get("task_metadata", {})
        if isinstance(task_meta, str):
            try:
                task_meta = json.loads(task_meta) if task_meta else {}
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "[publish_task] task_metadata is not valid JSON for task %s — defaulting to {}",
                    task_id,
                )
                task_meta = {}
        task_meta = task_meta or {}
        # task_result wins over task_metadata; publish_metadata stored under its own key
        merged_result = convert_decimals(
            {**task_meta, **existing_result, "publish_metadata": publish_metadata}
        )
        await db_service.update_task_status(
            task_id, "published", result=safe_json_dumps(merged_result)
        )

        # Create post in posts table when publishing (not before)
        # This ensures posts only exist for published content
        logger.info(f"Creating posts table entry for published task {task_id}")
        try:
            # Use merged content (task_metadata + result) so content is found whatever key the agent used
            task_result = merged_result

            # Extract content — check task columns first, then metadata/result
            topic = task.get("topic", "") or task_result.get("topic", "")
            draft_content = (
                task.get("content", "")
                or task_result.get("draft_content", "")
                or task_result.get("content", "")
                or task_result.get("body", "")
                or task_result.get("article", "")
                or ""
            )
            seo_description = task_result.get("seo_description", "") or task.get(
                "seo_description", ""
            )
            seo_keywords = task_result.get("seo_keywords", [])
            featured_image_url = task_result.get("featured_image_url") or task.get(
                "featured_image_url"
            )
            metadata = task_result.get("metadata", {})

            if draft_content and topic:
                # 🔑 EXTRACT TITLE: LLM often generates "#Title" at start of content
                # Extract it and use as the post title, remove from content
                extracted_title, cleaned_content = extract_title_from_content(draft_content)

                # Use extracted title if available, otherwise fall back to topic
                post_title = extracted_title or task_result.get("title") or topic

                # Use cleaned content (title removed)
                post_content = cleaned_content

                logger.info(f"📝 Post title: {post_title}")
                logger.info(f"   Extracted from content: {bool(extracted_title)}")
                logger.info(f"   Content length: {len(post_content or '')} chars")

                # Create slug from title (not topic)
                slug = re.sub(r"[^\w\s-]", "", post_title).lower().replace(" ", "-")[:50]
                slug = f"{slug}-{task_id[:8]}"

                # Get author and category
                from services.content_router_service import (
                    _get_or_create_default_author,
                    _select_category_for_topic,
                )

                author_id = await _get_or_create_default_author(db_service)
                category_id = await _select_category_for_topic(post_title, db_service)

                # Create post with status='published'
                post = await db_service.create_post(
                    {
                        "title": post_title,  # Use extracted title
                        "slug": slug,
                        "content": post_content,  # Use cleaned content
                        "excerpt": seo_description,
                        "featured_image_url": featured_image_url,
                        "author_id": author_id,
                        "category_id": category_id,
                        "status": "published",  # Published, not draft
                        "seo_title": post_title,  # Use extracted title for SEO
                        "seo_description": seo_description,
                        "seo_keywords": ",".join(seo_keywords) if seo_keywords else "",
                        "metadata": metadata,
                    }
                )
                logger.info(f"✅ Post created with status='published': {post.id}")  # type: ignore[attr-defined]
                logger.info(f"   Title: {post_title}")
                logger.info(f"   Slug: {slug}")
                post_id_val = str(post.id) if hasattr(post, "id") else str(post.get("id", ""))  # type: ignore[union-attr]
                logger.info(
                    "[content_published] task_id=%s post_id=%s user_id=%s slug=%s",
                    task_id,
                    post_id_val,
                    "operator",
                    slug,
                )
                # Persist post info back to task result so frontend gets published_url
                merged_result["post_id"] = post_id_val
                merged_result["post_slug"] = slug
                merged_result["published_url"] = f"/posts/{slug}"
                await db_service.update_task_status(
                    task_id, "published", result=safe_json_dumps(convert_decimals(merged_result))
                )

                # Emit webhook event for published post
                try:
                    await emit_webhook_event(db_service.pool, "post.published", {
                        "task_id": str(task_id), "title": post_title, "site": "default",
                    })
                except Exception:
                    logger.debug("[WEBHOOK] Failed to emit post.published event", exc_info=True)

                # Queue sync + embed as fire-and-forget background tasks
                if _should_run_post_publish_hooks() and background_tasks:
                    post_dict_for_embed = {
                        "id": post_id_val,
                        "title": post_title,
                        "excerpt": seo_description,
                        "content": post_content,
                    }
                    background_tasks.add_task(_sync_published_post, post_id_val)
                    background_tasks.add_task(_embed_published_post, db_service, post_dict_for_embed)
                    logger.info("[PUBLISH] Queued sync + embed background tasks for post %s", post_id_val)
            else:
                logger.warning("⚠️  Skipping post creation: missing content or topic")
        except Exception as e:
            logger.error(f"Failed to create post for published task: {str(e)}", exc_info=True)
            # Don't fail the publish operation if post creation fails
            # The task is still published, just warn about the post creation issue

        # Generate and distribute social media posts (non-fatal)
        try:
            from services.social_poster import generate_and_distribute_social_posts
            _post_slug = merged_result.get("post_slug", "")
            _post_title = task.get("title") or task.get("topic") or ""
            _seo_desc = task.get("seo_description") or ""
            _seo_kw = task.get("seo_keywords") or []
            if isinstance(_seo_kw, str):
                _seo_kw = [k.strip() for k in _seo_kw.split(",") if k.strip()]
            if _post_title and _post_slug:
                if background_tasks:
                    background_tasks.add_task(
                        generate_and_distribute_social_posts,
                        title=_post_title,
                        slug=_post_slug,
                        excerpt=_seo_desc,
                        keywords=_seo_kw,
                    )
                    logger.info("[SOCIAL] Queued social post generation for %s", _post_slug)
                else:
                    await generate_and_distribute_social_posts(
                        title=_post_title,
                        slug=_post_slug,
                        excerpt=_seo_desc,
                        keywords=_seo_kw,
                    )
                    logger.info("[SOCIAL] Social posts generated for %s", _post_slug)
        except Exception as e:
            logger.debug("[SOCIAL] Social posting failed (non-fatal): %s", e)

        # Trigger ISR revalidation on public site (#955)
        # Non-fatal: publish succeeds even if revalidation fails
        revalidation_success = False
        post_slug_val = merged_result.get("post_slug")
        revalidation_paths = ["/", "/archive", "/posts"]
        if post_slug_val:
            revalidation_paths.append(f"/posts/{post_slug_val}")
        try:
            revalidation_success = await trigger_nextjs_revalidation(revalidation_paths)
            if not revalidation_success:
                logger.warning(
                    "[publish_task] ISR revalidation returned failure — post is published but cache may be stale"
                )
        except Exception as reval_err:
            logger.warning(
                f"[publish_task] ISR revalidation error (non-fatal): {reval_err}", exc_info=True
            )

        # Store revalidation status in result for frontend signal
        merged_result["revalidation"] = {
            "triggered": True,
            "success": revalidation_success,
            "paths": revalidation_paths,
        }
        await db_service.update_task_status(
            task_id, "published", result=safe_json_dumps(convert_decimals(merged_result))
        )

        # Fetch updated task
        updated_task = await db_service.get_task(task_id)

        # Convert to response schema
        try:
            return UnifiedTaskResponse(
                **ModelConverter.task_response_to_unified(
                    ModelConverter.to_task_response(updated_task)
                )
            )
        except Exception as resp_err:
            logger.warning(
                f"[publish_task] Response model conversion failed ({resp_err}); returning minimal response",
                exc_info=True,
            )
            return {  # type: ignore[return-value]
                "id": task_id,
                "status": "published",
                "published_url": merged_result.get("published_url"),
                "post_id": merged_result.get("post_id"),
                "post_slug": merged_result.get("post_slug"),
                "revalidation": merged_result.get("revalidation"),
                "message": "Task published successfully",
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to publish task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to publish task") from e


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
        # Accept both UUID and numeric task IDs (backwards compatibility)
        try:
            UUID(task_id)
        except ValueError as exc:
            # If not a valid UUID, check if it's a numeric ID (legacy tasks)
            if not task_id.isdigit():
                raise HTTPException(status_code=400, detail="Invalid task ID format") from exc

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
        logger.info(f"Rejecting task {task_id} (current status: {current_status})")
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
        logger.error(f"Failed to reject task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to reject task") from e


class GenerateImageRequest(BaseModel):
    """Request model for image generation"""

    source: str = "pexels"  # "pexels" or "sdxl"
    topic: Optional[str] = None
    content_summary: Optional[str] = None
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
) -> Dict[str, str]:
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
        logger.info(f"Generating image for task {task_id} using {source}")

        image_url = None

        if source == "pexels":
            # Use Pexels API to search for images
            try:
                import aiohttp

                pexels_key = os.getenv("PEXELS_API_KEY")
                if not pexels_key:
                    raise HTTPException(status_code=400, detail="Pexels API key not configured")

                search_query = request.topic or task.get("topic", "business")
                current_image_url = task.get("featured_image_url")
                page = max(1, request.page)  # Ensure page is at least 1

                logger.info("🔎 Pexels API request:")
                logger.info(f"   - Query: '{search_query}'")
                logger.info(f"   - Page: {page}")
                logger.info("   - Per page: 50")
                logger.info(
                    f"   - Current featured image: {current_image_url[:80]}..."
                    if current_image_url
                    else "   - No current image"
                )

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.pexels.com/v1/search",
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
                                logger.info(f"✅ Pexels API response: {resp.status} OK")
                                logger.info(f"   - Response size: {len(str(data))} bytes")

                                if data.get("photos"):
                                    photos_count = len(data.get("photos", []))
                                    logger.info(f"   - Total photos in response: {photos_count}")
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
                                        f"Image filtering: Pexels returned {len(data['photos'])} photos, "
                                        f"currently using: {current_image_url}, "
                                        f"excluded: {len(excluded_urls)} URLs"
                                    )

                                    # Filter out any previously used images
                                    photos = [
                                        p
                                        for p in data["photos"]
                                        if p["src"]["large"] not in excluded_urls
                                    ]

                                    logger.info(
                                        f"   - After filtering: {len(photos)} available photos"
                                    )

                                    # If no new images available (rare), use the original list
                                    if not photos:
                                        logger.warning(
                                            f"⚠️ No new images after filtering, using all {len(data['photos'])} available"
                                        )
                                        photos = data["photos"]

                                    if photos:
                                        # Randomly select instead of always picking first
                                        photo = random.choice(photos)
                                        image_url = photo["src"]["large"]
                                        logger.info(
                                            f"✅ Selected image #{photos.index(photo) + 1}: {image_url}"
                                        )
                                        logger.info(
                                            f"   - Photographer: {photo.get('photographer', 'Unknown')}"
                                        )
                                        logger.info(f"   - Source: {photo['src']['original']}")

                                        # Store image URL and metadata in task for persistence
                                        # Track this image URL in recent_image_urls for future filtering
                                        updated_recent_urls = (
                                            recently_used + [image_url]
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
                                    f"Failed to parse Pexels response JSON: {je}", exc_info=True
                                )
                                raise ValueError(f"Invalid JSON from Pexels API: {str(je)}") from je
                        elif resp.status == 429:
                            logger.warning("Pexels rate limit exceeded")
                            raise HTTPException(
                                status_code=429,
                                detail="Image service rate limit exceeded. Please try again later.",
                            )
                        else:
                            logger.warning(f"Pexels API returned {resp.status}")
                            raise ValueError(f"Pexels API error: HTTP {resp.status}")

            except ValueError as ve:
                logger.error(f"Pexels API error: {ve}", exc_info=True)
                raise HTTPException(
                    status_code=500, detail="Error fetching image from Pexels"
                ) from ve
            except asyncio.TimeoutError as exc:
                logger.warning(f"Pexels API timeout for query: {search_query}", exc_info=True)
                raise HTTPException(status_code=504, detail="Pexels API timeout. Please try again.") from exc
            except Exception as e:
                logger.error(
                    f"Unexpected error fetching from Pexels: {type(e).__name__}: {e}", exc_info=True
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

                logger.info(f"🎨 Generating image with SDXL: {generation_prompt}")

                # Save to user's Downloads folder for preview
                downloads_path = str(Path.home() / "Downloads" / "glad-labs-generated-images")
                os.makedirs(downloads_path, exist_ok=True)

                # Create filename with UUID to prevent collisions (UUID instead of timestamp)
                unique_id = str(uuid_lib.uuid4())[:8]
                output_file = f"sdxl_{unique_id}.png"
                output_path = os.path.join(downloads_path, output_file)

                logger.info(f"📁 Generating SDXL image to: {output_path}")

                # Generate image with SDXL
                success = await image_service.generate_image(
                    prompt=generation_prompt,
                    output_path=output_path,
                    num_inference_steps=50,  # Good quality/speed balance
                    guidance_scale=7.5,
                    high_quality=False,
                    task_id=task_id,
                )

                if success and os.path.exists(output_path):
                    logger.info(f"✅ SDXL image generated: {output_path}")
                    image_url = output_path
                    logger.info("   Generated image saved locally for preview")
                else:
                    raise RuntimeError("SDXL image generation failed or file not created")

            except asyncio.TimeoutError as exc:
                logger.warning(f"SDXL image generation timeout for task {task_id}", exc_info=True)
                raise HTTPException(
                    status_code=408,
                    detail="Image generation timeout. Please try again with 'pexels' source.",
                ) from exc
            except (OSError, IOError, RuntimeError, ValueError) as e:
                logger.error(
                    f"SDXL image generation error - {type(e).__name__}: {e}", exc_info=True
                )
                raise HTTPException(
                    status_code=500,
                    detail="SDXL image generation failed. Ensure GPU available or use 'pexels' source.",
                ) from e
            except Exception as e:
                logger.critical(
                    f"Unexpected error in SDXL generation: {type(e).__name__}: {e}", exc_info=True
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
        logger.error(f"Failed to generate image for task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate image") from e
