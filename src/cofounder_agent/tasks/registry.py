"""Central task registry for workflow execution."""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskRegistry:
    """
    Central registry for all available tasks.

    Provides:
    - Task discovery (list all tasks, get by name)
    - Task registration (add/remove tasks)
    - Task querying (by type, capability)
    - Task configuration (default pipelines)
    """

    def __init__(self):
        """Initialize empty task registry."""
        self._tasks: Dict[str, "Task"] = {}  # name -> task instance
        self._task_categories: Dict[str, List[str]] = {
            "content": [],
            "social": [],
            "business": [],
            "automation": [],
            "utility": [],
        }

    def register(self, task: "Task", category: str = "utility") -> None:
        """
        Register a task in the registry.

        Args:
            task: Task instance to register
            category: Task category for organization
        """
        if task.name in self._tasks:
            logger.warning(f"Task '{task.name}' already registered, overwriting")

        self._tasks[task.name] = task

        if category in self._task_categories:
            if task.name not in self._task_categories[category]:
                self._task_categories[category].append(task.name)
        else:
            logger.warning(f"Unknown category: {category}")

        logger.info(f"Registered task: {task.name} (category: {category})")

    def get(self, task_name: str) -> Optional["Task"]:
        """
        Get task by name.

        Args:
            task_name: Name of task to retrieve

        Returns:
            Task instance or None if not found
        """
        return self._tasks.get(task_name)

    def list_tasks(self, category: Optional[str] = None) -> List[str]:
        """
        List all registered tasks.

        Args:
            category: Filter by category (optional)

        Returns:
            List of task names
        """
        if category:
            return self._task_categories.get(category, [])
        return list(self._tasks.keys())

    def list_categories(self) -> Dict[str, List[str]]:
        """
        Get all tasks organized by category.

        Returns:
            Dictionary of category -> task names
        """
        return {
            cat: tasks
            for cat, tasks in self._task_categories.items()
            if tasks  # Only include categories with tasks
        }

    def validate_pipeline(self, pipeline: List[str]) -> tuple[bool, Optional[str]]:
        """
        Validate that all tasks in pipeline exist.

        Args:
            pipeline: List of task names

        Returns:
            Tuple of (is_valid, error_message)
        """
        for task_name in pipeline:
            if task_name not in self._tasks:
                return False, f"Unknown task: {task_name}"

        if not pipeline:
            return False, "Pipeline cannot be empty"

        return True, None

    def get_default_pipeline(self, workflow_type: str) -> List[str]:
        """
        Get default pipeline for workflow type.

        Args:
            workflow_type: Type of workflow

        Returns:
            Default task pipeline
        """
        default_pipelines = {
            "content_generation": [
                "research",
                "creative",
                "qa",
                "image_selection",
                "publish",
            ],
            "social_media": [
                "social_research",
                "social_creative",
                "social_image_format",
                "social_publish",
            ],
            "financial_analysis": [
                "financial_analysis",
            ],
            "market_analysis": [
                "market_analysis",
            ],
            "performance_review": [
                "performance_review",
            ],
            "content_with_approval": [
                "research",
                "creative",
                "image_selection",
                "approval_gate",
                "publish",
            ],
        }

        return default_pipelines.get(workflow_type, [])

    def __len__(self) -> int:
        """Number of registered tasks."""
        return len(self._tasks)

    def __repr__(self) -> str:
        """String representation."""
        return f"<TaskRegistry: {len(self._tasks)} tasks>"
