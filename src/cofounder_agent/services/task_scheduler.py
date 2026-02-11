"""
Task Scheduler Service for Glad Labs AI Co-Founder

⚠️ DEPRECATED: This abstract interface is no longer actively used.

Task scheduling is now handled by:
- TaskExecutor service for task execution and processing
- PostgreSQL command queue and cost_logs tables for persistence
- Direct database queries for task status and history

This module is preserved for potential future alternative scheduler implementations
(e.g., APScheduler or Celery integration), but is not currently activated.

For task management, use TaskExecutor and DatabaseService directly.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from abc import ABC, abstractmethod
import logging

# Import configuration
from config import get_config

logger = logging.getLogger(__name__)

# Get configuration
config = get_config()


class TaskScheduler(ABC):
    """
    ⚠️ DEPRECATED: Abstract base class for task scheduling services.
    
    This interface is preserved for potential future implementations
    but is not currently used in production code.
    """
    
    def __init__(self):
        self.config = config
        logger.warning("TaskScheduler is deprecated - use TaskExecutor instead")
    
    @abstractmethod
    async def schedule_task(self, task_data: Dict[str, Any]) -> str:
        """Schedule a new task."""
        raise NotImplementedError("Use TaskExecutor instead")
    
    @abstractmethod
    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get the status of a specific task."""
        raise NotImplementedError("Use DatabaseService.get_task() instead")
    
    @abstractmethod
    async def update_task_status(self, task_id: str, status: str, 
                                result: Optional[Dict[str, Any]] = None) -> None:
        """Update the status of a specific task."""
        raise NotImplementedError("Use DatabaseService.update_task() instead")
    
    @abstractmethod
    async def retry_task(self, task_id: str, max_retries: int = 3) -> bool:
        """Retry a failed task."""
        raise NotImplementedError("Use TaskExecutor retry logic instead")
    
    @abstractmethod
    async def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get all pending tasks."""
        raise NotImplementedError("Use DatabaseService.get_pending_tasks() instead")
    
    @abstractmethod
    async def get_task_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Get the history of a specific task."""
        raise NotImplementedError("Use DatabaseService.get_task_history() instead")


class TaskSchedulerService(TaskScheduler):
    """✅ DEPRECATED - Concrete implementation of TaskScheduler (not used)."""
    
    def __init__(self):
        super().__init__()
        self._tasks: Dict[str, Dict[str, Any]] = {}
        logger.warning("TaskSchedulerService instantiated but is deprecated - use TaskExecutor instead")
    
    async def schedule_task(self, task_data: Dict[str, Any]) -> str:
        """Schedule a new task - ⚠️ DEPRECATED, use TaskExecutor instead."""
        raise NotImplementedError("Use TaskExecutor.execute_task() instead")
    
    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get the status of a specific task - ⚠️ DEPRECATED, use DatabaseService instead."""
        raise NotImplementedError("Use DatabaseService.get_task() instead")
    
    async def update_task_status(self, task_id: str, status: str, 
                                result: Optional[Dict[str, Any]] = None) -> None:
        """Update the status of a specific task - ⚠️ DEPRECATED, use DatabaseService instead."""
        raise NotImplementedError("Use DatabaseService.update_task() instead")
    
    async def retry_task(self, task_id: str, max_retries: int = 3) -> bool:
        """Retry a failed task - ⚠️ DEPRECATED, use TaskExecutor retry logic instead."""
        raise NotImplementedError("TaskExecutor handles retries internally via update_task()")
    
    async def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get all pending tasks - ⚠️ DEPRECATED, use DatabaseService instead."""
        raise NotImplementedError("Use DatabaseService.get_pending_tasks() instead")
    
    async def get_task_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Get the history of a specific task - ⚠️ DEPRECATED, use DatabaseService instead."""
        raise NotImplementedError("Query cost_logs table or workflow_history for task history")
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check."""
        return {"status": "deprecated", "service": "task-scheduler", "message": "Use TaskExecutor instead"}
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get task scheduler metrics."""
        pending = len([t for t in self._tasks.values() if t["status"] == "pending"])
        completed = len([t for t in self._tasks.values() if t["status"] == "completed"])
        failed = len([t for t in self._tasks.values() if t["status"] == "failed"])
        
        return {
            "total_tasks": len(self._tasks),
            "pending_tasks": pending,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "success_rate": (completed / len(self._tasks) * 100) if self._tasks else 0,
        }
    
    async def initialize(self) -> None:
        """Initialize the task scheduler."""
        pass
    
    async def shutdown(self) -> None:
        """Shutdown the task scheduler."""
        pass