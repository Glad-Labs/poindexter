"""Enhanced status change validation and logging service."""

import logging
import json
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

from utils.task_status import StatusTransitionValidator, TaskStatus, is_valid_transition
from services.tasks_db import TasksDatabase

logger = logging.getLogger(__name__)


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
        reason: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[bool, str, List[str]]:
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
                logger.error(f"❌ {error}")
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
                logger.warning(f"❌ {error_msg}")
                logger.warning(f"   Errors: {errors}")
                return False, error_msg, errors

            # Prepare history metadata
            history_metadata = {
                "user_id": user_id,
                "reason": reason,
                "validation_context": metadata or {},
                "timestamp": datetime.utcnow().isoformat(),
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
                logger.warning(f"⚠️  Failed to log status change for {task_id}")

            # Update task
            update_data = {"status": new_status, "updated_at": datetime.utcnow()}

            if metadata:
                update_data["task_metadata"] = metadata

            updated = await self.db_service.update_task(task_id, update_data)

            if not updated:
                error = f"Failed to update task status"
                return False, error, ["update_failed"]

            success_msg = f"Status changed: {current_status} → {new_status}"
            logger.info(f"✅ {success_msg}")
            return True, success_msg, []

        except Exception as e:
            error = f"Error during status change: {str(e)}"
            logger.error(f"❌ {error}", exc_info=True)
            return False, error, ["internal_error"]

    async def get_status_audit_trail(self, task_id: str, limit: int = 50) -> Dict[str, Any]:
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
            logger.error(f"❌ Failed to get audit trail: {e}")
            return {"task_id": task_id, "history_count": 0, "history": [], "error": str(e)}

    async def get_validation_failures(self, task_id: str, limit: int = 50) -> Dict[str, Any]:
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
            logger.error(f"❌ Failed to get validation failures: {e}")
            return {"task_id": task_id, "failure_count": 0, "failures": [], "error": str(e)}
