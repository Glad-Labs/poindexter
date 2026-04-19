"""ContentTaskStore — CRUD adapter over DatabaseService for content_tasks rows.

Lifted from content_router_service.py during Phase E2. Nothing fancy —
the class is a thin async-first wrapper around the database service,
extracted so content_router_service.py can shrink to just the
orchestrator + re-exports.

Also exposes :func:`get_content_task_store` singleton (lazy-initialized),
which the routes layer uses to avoid threading DatabaseService through
every endpoint.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from services.audit_log import audit_log_bg
from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class ContentTaskStore:
    """Unified task storage adapter delegating to persistent DatabaseService backend."""

    def __init__(self, database_service: DatabaseService | None = None):
        self.database_service = database_service

    @property
    def persistent_store(self):
        """Backward-compatible property — returns the DatabaseService."""
        return self.database_service

    async def create_task(
        self,
        topic: str,
        style: str,
        tone: str,
        target_length: int,
        tags: list[str] | None = None,
        generate_featured_image: bool = True,
        request_type: str = "basic",
        task_type: str = "blog_post",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a new task in persistent storage. Returns task ID."""
        logger.info("[CONTENT_TASK_STORE] Creating task (async)")
        logger.info("   Topic: %s%s", topic[:60], "..." if len(topic) > 60 else "")
        logger.info("   Style: %s | Tone: %s | Length: %sw", style, tone, target_length)
        logger.info("   Tags: %s", ", ".join(tags) if tags else "none")
        logger.debug("   Type: %s | Image: %s", request_type, generate_featured_image)

        # Legacy: the caller's metadata arg is overwritten here with the
        # image flag only. Preserved verbatim to keep observable behavior.
        metadata = {"generate_featured_image": generate_featured_image}
        logger.debug("   Metadata: %s", metadata)

        try:
            if not self.database_service:
                raise ValueError(
                    "DatabaseService not initialized - cannot persist tasks"
                )

            logger.debug("   Calling database_service.add_task() (async)...")

            task_name = (
                f"{topic[:50]}" if len(topic) <= 50 else f"{topic[:47]}..."
            )

            task_id = await self.database_service.add_task(
                {
                    "task_name": task_name,
                    "topic": topic,
                    "style": style,
                    "tone": tone,
                    "target_length": target_length,
                    "tags": tags or [],
                    "request_type": request_type,
                    "task_type": task_type,
                    "metadata": metadata or {},
                }
            )

            logger.info("[CONTENT_TASK_STORE] Task CREATED and PERSISTED (async)")
            logger.info("   Task ID: %s", task_id)
            logger.info("   Status: pending")
            logger.debug("   Ready for processing")
            audit_log_bg(
                "task_created", "content_router",
                {
                    "topic": topic[:100], "style": style, "tone": tone,
                    "target_length": target_length, "request_type": request_type,
                },
                task_id=task_id,
            )
            return task_id

        except Exception as e:  # noqa: BLE001 — re-raise after logging
            logger.error("[CONTENT_TASK_STORE] ERROR: %s", e, exc_info=True)
            raise

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get task by ID from persistent storage (async, non-blocking)."""
        if not self.database_service:
            return None
        return await self.database_service.get_task(task_id)

    async def update_task(
        self, task_id: str, updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update task data in persistent storage (async, non-blocking)."""
        if not self.database_service:
            return False  # type: ignore[return-value] — legacy return shape

        if "metadata" in updates:
            updates["task_metadata"] = json.dumps(updates.pop("metadata"))
        return await self.database_service.update_task(task_id, updates)

    async def delete_task(self, task_id: str) -> bool:
        """Delete task from persistent storage (async, non-blocking)."""
        if not self.database_service:
            return False
        return await self.database_service.delete_task(task_id)

    async def list_tasks(
        self, status: str | None = None, limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List tasks from persistent storage with optional filtering."""
        if not self.database_service:
            return []
        tasks, _total = await self.database_service.get_tasks_paginated(
            offset=offset, limit=limit, status=status,
        )
        return tasks

    async def get_drafts(self, limit: int = 20, offset: int = 0) -> tuple:
        """Get list of drafts from persistent storage (async, non-blocking)."""
        if not self.database_service:
            return []  # type: ignore[return-value] — legacy return shape
        return await self.database_service.get_drafts(limit=limit, offset=offset)


# Global unified task store (lazy-initialized).
_content_task_store: ContentTaskStore | None = None


def get_content_task_store(
    database_service: DatabaseService | None = None,
) -> ContentTaskStore:
    """Return the global content task store; inject DB service if missing."""
    global _content_task_store
    if _content_task_store is None:
        _content_task_store = ContentTaskStore(database_service)
    elif database_service and _content_task_store.database_service is None:
        _content_task_store.database_service = database_service
    return _content_task_store
