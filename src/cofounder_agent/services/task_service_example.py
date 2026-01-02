"""
Example Service Implementation: TaskService

Demonstrates how to convert existing services to the new ServiceBase pattern.
This can be used as a template for refactoring the 76 existing services.

Key concepts:
1. Inherit from ServiceBase
2. Define service metadata (name, version, description)
3. Override get_actions() to define available actions
4. Implement action methods as async functions
5. Use action_<action_name> naming convention
6. Call other services via self.call_service()
"""

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
import uuid
from .service_base import (
    ServiceBase, 
    ServiceAction, 
    JsonSchema, 
    ServiceError,
    ActionResult,
    ActionStatus
)


class TaskService(ServiceBase):
    """
    Task Management Service
    
    Handles all task-related operations:
    - Create tasks
    - List tasks with pagination
    - Get task details
    - Update task status
    - Delete tasks
    """
    
    # Service metadata
    name = "tasks"
    version = "1.0.0"
    description = "Manage content generation tasks and workflows"
    
    def get_actions(self) -> List[ServiceAction]:
        """Define all actions provided by TaskService"""
        return [
            ServiceAction(
                name="create_task",
                description="Create a new task for content generation",
                input_schema=JsonSchema(
                    type="object",
                    properties={
                        "task_name": {"type": "string", "description": "Name of the task"},
                        "topic": {"type": "string", "description": "Content topic"},
                        "category": {"type": "string", "description": "Content category"},
                        "primary_keyword": {"type": "string", "description": "Primary SEO keyword (optional)"},
                        "target_audience": {"type": "string", "description": "Target audience (optional)"},
                        "metadata": {"type": "object", "description": "Additional metadata (optional)"},
                    },
                    required=["task_name", "topic"],
                ),
                output_schema=JsonSchema(
                    type="object",
                    properties={
                        "id": {"type": "string", "description": "Task ID"},
                        "status": {"type": "string", "description": "Task status"},
                        "created_at": {"type": "string", "description": "Creation timestamp"},
                    },
                    required=["id", "status", "created_at"],
                ),
                error_codes=["VALIDATION_ERROR", "DATABASE_ERROR"],
            ),
            ServiceAction(
                name="list_tasks",
                description="List tasks with pagination and filtering",
                input_schema=JsonSchema(
                    type="object",
                    properties={
                        "offset": {"type": "integer", "description": "Pagination offset (default: 0)"},
                        "limit": {"type": "integer", "description": "Results per page (default: 20)"},
                        "status": {"type": "string", "description": "Filter by status (optional)"},
                        "category": {"type": "string", "description": "Filter by category (optional)"},
                    },
                    required=[],
                ),
                output_schema=JsonSchema(
                    type="object",
                    properties={
                        "tasks": {"type": "array", "description": "List of tasks"},
                        "total": {"type": "integer", "description": "Total task count"},
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    required=["tasks", "total"],
                ),
                error_codes=["DATABASE_ERROR"],
            ),
            ServiceAction(
                name="get_task",
                description="Get details of a specific task",
                input_schema=JsonSchema(
                    type="object",
                    properties={
                        "task_id": {"type": "string", "description": "Task ID"},
                    },
                    required=["task_id"],
                ),
                output_schema=JsonSchema(
                    type="object",
                    properties={
                        "id": {"type": "string"},
                        "task_name": {"type": "string"},
                        "status": {"type": "string"},
                        "created_at": {"type": "string"},
                    },
                    required=["id", "status"],
                ),
                error_codes=["TASK_NOT_FOUND", "DATABASE_ERROR"],
            ),
            ServiceAction(
                name="update_task_status",
                description="Update the status of a task",
                input_schema=JsonSchema(
                    type="object",
                    properties={
                        "task_id": {"type": "string", "description": "Task ID"},
                        "status": {"type": "string", "description": "New status (pending, in_progress, completed, failed)"},
                    },
                    required=["task_id", "status"],
                ),
                output_schema=JsonSchema(
                    type="object",
                    properties={
                        "id": {"type": "string"},
                        "status": {"type": "string"},
                        "updated_at": {"type": "string"},
                    },
                    required=["id", "status"],
                ),
                error_codes=["TASK_NOT_FOUND", "INVALID_STATUS", "DATABASE_ERROR"],
            ),
        ]
    
    # ========================================================================
    # ACTION IMPLEMENTATIONS
    # ========================================================================
    
    async def action_create_task(
        self,
        task_name: str,
        topic: str,
        category: Optional[str] = None,
        primary_keyword: Optional[str] = None,
        target_audience: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new task.
        
        Implementation notes:
        - In real usage, would call self.db_service.add_task()
        - Or call database service via: await self.call_service("database", "add_task", {...})
        """
        try:
            task_id = str(uuid.uuid4())
            
            task_data = {
                "id": task_id,
                "task_name": task_name.strip(),
                "topic": topic.strip(),
                "category": (category or "general").strip(),
                "primary_keyword": (primary_keyword or "").strip(),
                "target_audience": (target_audience or "").strip(),
                "status": "pending",
                "metadata": metadata or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            
            # TODO: Call database service
            # result = await self.call_service("database", "insert_task", task_data)
            
            self.logger.info(f"Created task {task_id}: {task_name}")
            
            return {
                "id": task_id,
                "status": "pending",
                "created_at": task_data["created_at"],
                "message": "Task created successfully",
            }
            
        except Exception as e:
            raise ServiceError(
                error_code="DATABASE_ERROR",
                message=f"Failed to create task: {str(e)}",
            )
    
    async def action_list_tasks(
        self,
        offset: int = 0,
        limit: int = 20,
        status: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List tasks with pagination and filtering"""
        try:
            # TODO: Call database service
            # tasks, total = await self.call_service(
            #     "database", 
            #     "get_tasks_paginated",
            #     {"offset": offset, "limit": limit, "status": status, "category": category}
            # )
            
            # Placeholder implementation
            return {
                "tasks": [],
                "total": 0,
                "offset": offset,
                "limit": limit,
            }
            
        except Exception as e:
            raise ServiceError(
                error_code="DATABASE_ERROR",
                message=f"Failed to list tasks: {str(e)}",
            )
    
    async def action_get_task(self, task_id: str) -> Dict[str, Any]:
        """Get a single task"""
        try:
            # TODO: Call database service
            # task = await self.call_service("database", "get_task", {"task_id": task_id})
            
            if not task_id:
                raise ServiceError(
                    error_code="TASK_NOT_FOUND",
                    message=f"Task {task_id} not found",
                )
            
            # Placeholder
            return {"id": task_id, "status": "pending"}
            
        except Exception as e:
            raise ServiceError(
                error_code="DATABASE_ERROR",
                message=f"Failed to get task: {str(e)}",
            )
    
    async def action_update_task_status(
        self,
        task_id: str,
        status: str,
    ) -> Dict[str, Any]:
        """Update task status"""
        valid_statuses = ["pending", "in_progress", "completed", "failed"]
        if status not in valid_statuses:
            raise ServiceError(
                error_code="INVALID_STATUS",
                message=f"Invalid status '{status}'. Must be one of: {valid_statuses}",
            )
        
        try:
            # TODO: Call database service
            # await self.call_service(
            #     "database",
            #     "update_task_status",
            #     {"task_id": task_id, "status": status}
            # )
            
            self.logger.info(f"Updated task {task_id} status to {status}")
            
            return {
                "id": task_id,
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            
        except Exception as e:
            raise ServiceError(
                error_code="DATABASE_ERROR",
                message=f"Failed to update task: {str(e)}",
            )


# ============================================================================
# EXAMPLE: How to use TaskService with LLM
# ============================================================================

"""
Step 1: Register the service
    registry = get_service_registry()
    registry.register(TaskService())

Step 2: LLM queries available actions
    GET /api/services/registry
    Response:
    {
        "services": [
            {
                "name": "tasks",
                "version": "1.0.0",
                "description": "Manage content generation tasks",
                "actions": [
                    {
                        "name": "create_task",
                        "description": "Create a new task for content generation",
                        "input_schema": {...},
                        "output_schema": {...},
                        "error_codes": ["VALIDATION_ERROR", "DATABASE_ERROR"],
                        "requires_auth": true,
                        "is_async": true
                    },
                    ...
                ]
            }
        ]
    }

Step 3: LLM interprets natural language into action call
    User: "Create a blog post about AI ethics"
    LLM converts to:
    {
        "service": "tasks",
        "action": "create_task",
        "params": {
            "task_name": "Blog Post - AI Ethics",
            "topic": "Ethical considerations in AI development",
            "primary_keyword": "AI ethics"
        }
    }

Step 4: Execute action
    POST /api/services/tasks/actions/create_task
    Body: {...params...}
    Response:
    {
        "action": "create_task",
        "status": "completed",
        "data": {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "status": "pending",
            "created_at": "2026-01-01T18:00:00Z"
        },
        "execution_time_ms": 45.2,
        "timestamp": "2026-01-01T18:00:00Z"
    }

Step 5: LLM chains multiple service calls
    - Create task via TaskService
    - Generate content via ContentService
    - Publish via PublishingService
    - Track metrics via MetricsService
"""
