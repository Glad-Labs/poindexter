"""
Capability Tasks Service - Database operations for capability-based tasks.

Handles CRUD operations for task definitions and execution history.
Uses asyncpg (matching the established pattern in tasks_db.py, admin_db.py)
rather than SQLAlchemy, which is not part of the production dependency set
(issue #795).
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

from asyncpg import Pool

from services.logger_config import get_logger

from .capability_task_executor import CapabilityStep, CapabilityTaskDefinition, TaskExecutionResult

logger = get_logger(__name__)


class CapabilityTasksService:
    """Database service for capability-based tasks."""

    def __init__(self, pool: Pool):
        """
        Initialize service with an asyncpg connection pool.

        Args:
            pool: asyncpg connection pool (the application-level instance
                  initialized once at startup).
        """
        self.pool = pool

    # ============ Task Definition CRUD ============

    async def create_task(
        self,
        name: str,
        description: str,
        steps: List[CapabilityStep],
        owner_id: str,
        tags: Optional[List[str]] = None,
    ) -> CapabilityTaskDefinition:
        """
        Create a new capability task.

        Args:
            name: Task name
            description: Task description
            steps: List of capability steps
            owner_id: Owner user ID (for isolation)
            tags: Optional tags

        Returns:
            CapabilityTaskDefinition
        """
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        steps_json = json.dumps([step.to_dict() for step in steps])
        tags_json = json.dumps(tags or [])

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO capability_tasks (
                    id, name, description, owner_id, steps, tags,
                    created_at, updated_at, is_active, version
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $7, TRUE, 1)
                """,
                task_id,
                name,
                description,
                owner_id,
                steps_json,
                tags_json,
                now,
            )

        logger.info("[capability_tasks] Created task %s for owner %s", task_id, owner_id)
        return CapabilityTaskDefinition(
            id=task_id,
            name=name,
            description=description,
            steps=steps,
            tags=tags or [],
            owner_id=owner_id,
        )

    async def get_task(self, task_id: str, owner_id: str) -> Optional[CapabilityTaskDefinition]:
        """
        Get a task by ID (with owner isolation).

        Args:
            task_id: Task ID
            owner_id: Owner user ID

        Returns:
            CapabilityTaskDefinition or None
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, description, owner_id, steps, tags, created_at
                FROM capability_tasks
                WHERE id = $1 AND owner_id = $2
                """,
                task_id,
                owner_id,
            )
        return self._row_to_task(row) if row else None

    async def list_tasks(
        self,
        owner_id: str,
        skip: int = 0,
        limit: int = 50,
        tags: Optional[List[str]] = None,
        active_only: bool = True,
    ) -> Tuple[List[CapabilityTaskDefinition], int]:
        """
        List tasks for a user.

        Args:
            owner_id: Owner user ID
            skip: Pagination offset
            limit: Pagination limit
            tags: Filter by tags (any match)
            active_only: Only return active tasks

        Returns:
            Tuple of (tasks, total_count)
        """
        conditions = ["owner_id = $1"]
        params: List[Any] = [owner_id]

        if active_only:
            conditions.append("is_active = TRUE")

        where_sql = "WHERE " + " AND ".join(conditions)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, name, description, owner_id, steps, tags, created_at,
                       COUNT(*) OVER () AS total_count
                FROM capability_tasks
                {where_sql}
                ORDER BY created_at DESC
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                limit,
                skip,
            )

        total = rows[0]["total_count"] if rows else 0
        tasks = [self._row_to_task(row) for row in rows]
        return tasks, total

    def _row_to_task(self, row: Any) -> CapabilityTaskDefinition:
        """Convert asyncpg row to CapabilityTaskDefinition."""
        steps_data = row["steps"]
        if isinstance(steps_data, str):
            steps_data = json.loads(steps_data)
        elif steps_data is None:
            steps_data = []

        steps = [
            CapabilityStep(
                capability_name=step["capability_name"],
                inputs=step["inputs"],
                output_key=step["output_key"],
                order=step.get("order", 0),
                metadata=step.get("metadata", {}),
            )
            for step in steps_data
        ]

        tags = row["tags"]
        if isinstance(tags, str):
            tags = json.loads(tags)
        elif tags is None:
            tags = []

        return CapabilityTaskDefinition(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            steps=steps,
            tags=tags,
            owner_id=row["owner_id"],
            created_at=row.get("created_at"),
        )

    async def update_task(
        self,
        task_id: str,
        owner_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        steps: Optional[List[CapabilityStep]] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[CapabilityTaskDefinition]:
        """Update a task definition."""
        update_parts = ["updated_at = $3", "version = version + 1"]
        params: List[Any] = [task_id, owner_id, datetime.now(timezone.utc)]

        if name is not None:
            params.append(name)
            update_parts.append(f"name = ${len(params)}")
        if description is not None:
            params.append(description)
            update_parts.append(f"description = ${len(params)}")
        if steps is not None:
            params.append(json.dumps([s.to_dict() for s in steps]))
            update_parts.append(f"steps = ${len(params)}::jsonb")
        if tags is not None:
            params.append(json.dumps(tags))
            update_parts.append(f"tags = ${len(params)}::jsonb")

        set_sql = ", ".join(update_parts)
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                f"""
                UPDATE capability_tasks
                SET {set_sql}
                WHERE id = $1 AND owner_id = $2
                """,
                *params,
            )

        if result == "UPDATE 0":
            return None

        return await self.get_task(task_id, owner_id)

    async def delete_task(self, task_id: str, owner_id: str) -> bool:
        """
        Delete a task (soft delete - sets is_active=False).

        Args:
            task_id: Task ID
            owner_id: Owner user ID

        Returns:
            True if deleted, False if not found
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE capability_tasks
                SET is_active = FALSE, updated_at = $3
                WHERE id = $1 AND owner_id = $2
                """,
                task_id,
                owner_id,
                datetime.now(timezone.utc),
            )
        return result != "UPDATE 0"

    # ============ Execution CRUD ============

    async def persist_execution(
        self,
        result: TaskExecutionResult,
    ) -> str:
        """
        Save execution result to database.

        Args:
            result: TaskExecutionResult from executor

        Returns:
            Execution ID
        """
        step_results_json = json.dumps([r.to_dict() for r in result.step_results])
        final_outputs_json = json.dumps(result.final_outputs or {})
        completed_steps = sum(1 for r in result.step_results if r.status == "completed")
        total_steps = len(result.step_results)
        now = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO capability_executions (
                        id, task_id, owner_id, status, error_message,
                        step_results, final_outputs, total_duration_ms,
                        progress_percent, completed_steps, total_steps,
                        started_at, completed_at, created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb,
                            $8, $9, $10, $11, $12, $13, $14)
                    """,
                    result.execution_id,
                    result.task_id,
                    result.owner_id,
                    result.status,
                    result.error,
                    step_results_json,
                    final_outputs_json,
                    result.total_duration_ms,
                    result.progress_percent,
                    completed_steps,
                    total_steps,
                    result.started_at,
                    result.completed_at,
                    now,
                )

                # Update task metrics atomically within same transaction
                success_increment = 1 if result.status == "completed" else 0
                failure_increment = 1 if result.status == "failed" else 0
                await conn.execute(
                    """
                    UPDATE capability_tasks
                    SET
                        execution_count = execution_count + 1,
                        success_count   = success_count + $2,
                        failure_count   = failure_count + $3,
                        last_executed_at = $4
                    WHERE id = $1
                    """,
                    result.task_id,
                    success_increment,
                    failure_increment,
                    now,
                )

        logger.info(
            "[capability_tasks] Persisted execution %s (status=%s)",
            result.execution_id,
            result.status,
        )
        return result.execution_id

    async def get_execution(
        self,
        execution_id: str,
        owner_id: str,
    ) -> Optional[TaskExecutionResult]:
        """Get execution result by ID."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, task_id, owner_id, status, error_message,
                       step_results, final_outputs, total_duration_ms,
                       progress_percent, started_at, completed_at
                FROM capability_executions
                WHERE id = $1 AND owner_id = $2
                """,
                execution_id,
                owner_id,
            )
        return self._row_to_execution(row) if row else None

    async def list_executions(
        self,
        task_id: str,
        owner_id: str,
        skip: int = 0,
        limit: int = 50,
        status_filter: Optional[str] = None,
    ) -> Tuple[List[TaskExecutionResult], int]:
        """
        List executions for a task.

        Args:
            task_id: Task ID
            owner_id: Owner user ID
            skip: Pagination offset
            limit: Pagination limit
            status_filter: Filter by status (pending, running, completed, failed)

        Returns:
            Tuple of (executions, total_count)
        """
        conditions = ["task_id = $1", "owner_id = $2"]
        params: List[Any] = [task_id, owner_id]

        if status_filter:
            params.append(status_filter)
            conditions.append(f"status = ${len(params)}")

        where_sql = "WHERE " + " AND ".join(conditions)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, task_id, owner_id, status, error_message,
                       step_results, final_outputs, total_duration_ms,
                       progress_percent, started_at, completed_at,
                       COUNT(*) OVER () AS total_count
                FROM capability_executions
                {where_sql}
                ORDER BY started_at DESC
                LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
                """,
                *params,
                limit,
                skip,
            )

        total = rows[0]["total_count"] if rows else 0
        executions = [self._row_to_execution(row) for row in rows]
        return executions, total

    def _row_to_execution(self, row: Any) -> TaskExecutionResult:
        """Convert asyncpg row to TaskExecutionResult."""
        from .capability_task_executor import StepResult

        raw_step_results = row["step_results"] or []
        if isinstance(raw_step_results, str):
            raw_step_results = json.loads(raw_step_results)

        final_outputs = row["final_outputs"] or {}
        if isinstance(final_outputs, str):
            final_outputs = json.loads(final_outputs)

        step_results = [
            StepResult(
                step_index=step_data["step_index"],
                capability_name=step_data["capability_name"],
                output_key=step_data["output_key"],
                output=step_data.get("output"),
                duration_ms=step_data.get("duration_ms", 0),
                error=step_data.get("error"),
                status=step_data.get("status", "completed"),
            )
            for step_data in raw_step_results
        ]

        return TaskExecutionResult(
            task_id=row["task_id"],
            execution_id=row["id"],
            owner_id=row["owner_id"],
            status=row["status"],
            step_results=step_results,
            final_outputs=final_outputs,
            total_duration_ms=row.get("total_duration_ms", 0),
            error=row.get("error_message"),
            started_at=row["started_at"],
            completed_at=row.get("completed_at"),
        )
