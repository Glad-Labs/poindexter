"""Enhanced status change validation and logging service."""

from datetime import datetime, timezone
from typing import Any

from services.logger_config import get_logger
from services.tasks_db import TasksDatabase
from utils.json_encoder import safe_json_load
from utils.task_status import StatusTransitionValidator

logger = get_logger(__name__)


class EnhancedStatusChangeService:
    """Service for validated status changes with comprehensive logging."""

    def __init__(self, db_service: TasksDatabase):
        """
        Initialize service.

        Args:
            db_service: Database service for persistence
        """
        self.db_service = db_service
        self.validator = StatusTransitionValidator()

    async def validate_and_change_status(
        self,
        task_id: str,
        new_status: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> tuple[bool, str, list[str]]:
        """
        Validate and execute status change with full audit trail.

        Args:
            task_id: Task ID
            new_status: Target status
            reason: Change reason
            metadata: Additional metadata
            user_id: User ID making the change

        Returns:
            Tuple of (success, message, errors)
        """
        try:
            # Get current task
            task = await self.db_service.get_task(task_id)
            if not task:
                error = f"Task not found: {task_id}"
                logger.error("%s", error)
                return False, error, ["task_not_found"]

            current_status = task.get("status", "pending")

            # Validate transition
            is_valid, errors = self.validator.validate_transition(
                current_status=current_status,
                new_status=new_status,
                task_id=task_id,
                additional_context=metadata,
            )

            if not is_valid:
                error_msg = f"Invalid status transition: {current_status} → {new_status}"
                logger.warning("%s", error_msg)
                logger.warning("   Errors: %s", errors)
                return False, error_msg, errors

            # Prepare history metadata
            history_metadata = {
                "user_id": user_id,
                "reason": reason,
                "validation_context": metadata or {},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Log to history table
            logged = await self.db_service.log_status_change(
                task_id=task_id,
                old_status=current_status,
                new_status=new_status,
                reason=reason,
                metadata=history_metadata,
            )

            if not logged:
                logger.warning("Failed to log status change for %s", task_id)

            # Update task
            update_data = {"status": new_status, "updated_at": datetime.now(timezone.utc)}

            if metadata:
                # Merge incoming metadata with existing task_metadata to avoid overwriting
                # previously persisted generation results and validation diagnostics.
                existing_metadata = safe_json_load(task.get("task_metadata") or {}, fallback={})
                if not isinstance(existing_metadata, dict):
                    existing_metadata = {}

                merged_metadata = {**existing_metadata, **metadata}

                # Persist retry counters for UI badge and auditability.
                if str(metadata.get("action", "")).lower() == "retry":
                    prior_retry_count = existing_metadata.get("retry_count", 0)
                    try:
                        prior_retry_count = int(prior_retry_count)
                    except (TypeError, ValueError):
                        prior_retry_count = 0

                    merged_metadata["retry_count"] = prior_retry_count + 1
                    merged_metadata["last_retry_at"] = datetime.now(timezone.utc).isoformat()
                    merged_metadata["last_retry_by"] = user_id

                update_data["task_metadata"] = merged_metadata

            updated = await self.db_service.update_task(task_id, update_data)

            if not updated:
                error = "Failed to update task status"
                return False, error, ["update_failed"]

            success_msg = f"Status changed: {current_status} → {new_status}"
            logger.info("%s", success_msg)
            return True, success_msg, []

        except Exception as e:
            error = f"Error during status change: {e!s}"
            logger.error("[_validate_and_change_status] %s", error, exc_info=True)
            return False, error, ["internal_error"]

    async def get_status_audit_trail(self, task_id: str, limit: int = 50) -> dict[str, Any]:
        """
        Get complete audit trail for a task.

        Args:
            task_id: Task ID
            limit: Maximum records to return

        Returns:
            Dict with audit trail data
        """
        try:
            history = await self.db_service.get_status_history(task_id, limit)

            return {"task_id": task_id, "history_count": len(history), "history": history}
        except Exception as e:
            logger.error(
                "[_get_status_audit_trail] Failed to get audit trail: %s", e, exc_info=True
            )
            return {"task_id": task_id, "history_count": 0, "history": [], "error": str(e)}

    async def get_validation_failures(self, task_id: str, limit: int = 50) -> dict[str, Any]:
        """
        Get all validation failures for a task.

        Args:
            task_id: Task ID
            limit: Maximum records to return

        Returns:
            Dict with validation failure details
        """
        try:
            failures = await self.db_service.get_validation_failures(task_id, limit)

            return {"task_id": task_id, "failure_count": len(failures), "failures": failures}
        except Exception as e:
            logger.error(
                "[_get_validation_failures] Failed to get validation failures: %s",
                e, exc_info=True,
            )
            return {"task_id": task_id, "failure_count": 0, "failures": [], "error": str(e)}
