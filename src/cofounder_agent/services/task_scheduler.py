"""
Task Scheduler Service for Glad Labs AI Co-Founder

This module provides centralized task scheduling, retry, and status tracking.
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from abc import ABC, abstractmethod

# Import configuration
from config import get_config

# Get configuration
config = get_config()


class TaskScheduler(ABC):
    """Base class for task scheduling services."""
    
    def __init__(self):
        self.config = config
    
    @abstractmethod
    async def schedule_task(self, task_data: Dict[str, Any]) -> str:
        """Schedule a new task."""
        pass
    
    @abstractmethod
    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get the status of a specific task."""
        pass
    
    @abstractmethod
    async def update_task_status(self, task_id: str, status: str, 
                                result: Optional[Dict[str, Any]] = None) -> None:
        """Update the status of a specific task."""
        pass
    
    @abstractmethod
    async def retry_task(self, task_id: str, max_retries: int = 3) -> bool:
        """Retry a failed task."""
        pass
    
    @abstractmethod
    async def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get all pending tasks."""
        pass
    
    @abstractmethod
    async def get_task_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Get the history of a specific task."""
        pass


class TaskSchedulerService(TaskScheduler):
    """Concrete implementation of TaskScheduler."""
    
    def __init__(self):
        super().__init__()
        self._tasks: Dict[str, Dict[str, Any]] = {}
    
    async def schedule_task(self, task_data: Dict[str, Any]) -> str:
        """Schedule a new task."""
        task_id = f"task_{len(self._tasks) + 1}"
        task = {
            "id": task_id,
            "data": task_data,
            "status": "pending",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "retries": 0,
            "max_retries": task_data.get("max_retries", 3),
        }
        self._tasks[task_id] = task
        return task_id
    
    async def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get the status of a specific task."""
        return self._tasks.get(task_id, {})
    
    async def update_task_status(self, task_id: str, status: str, 
                                result: Optional[Dict[str, Any]] = None) -> None:
        """Update the status of a specific task."""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            task["status"] = status
            task["updated_at"] = datetime.utcnow()
            if result:
                task["result"] = result
            if status == "failed":
                task["retries"] += 1
    
    async def retry_task(self, task_id: str, max_retries: int = 3) -> bool:
        """Retry a failed task."""
        if task_id in self._tasks:
            task = self._tasks[task_id]
            if task["status"] == "failed" and task["retries"] < max_retries:
                task["status"] = "pending"
                task["retries"] += 1
                task["updated_at"] = datetime.utcnow()
                return True
        return False
    
    async def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get all pending tasks."""
        return [task for task in self._tasks.values() if task["status"] == "pending"]
    
    async def get_task_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Get the history of a specific task."""
        # For this mock implementation, we'll just return the task itself
        task = self._tasks.get(task_id, {})
        return [task] if task else []
    
    async def health_check(self) -> Dict[str, Any]:
        """Perform health check."""
        return {"status": "healthy", "service": "task-scheduler"}
    
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