"""
Capability Tasks Service - Database operations for capability-based tasks.

Handles CRUD operations for task definitions and execution history.
Uses asyncpg directly, consistent with the rest of the database layer.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

import asyncpg

from .capability_task_executor import (
    CapabilityStep,
    CapabilityTaskDefinition,
    TaskExecutionResult,
)


class CapabilityTasksService:
    """Database service for capability-based tasks."""

    def __init__(self, pool: asyncpg.Pool):
        """Initialize service with asyncpg connection pool."""
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
        """Create a new capability task."""
        task_id = str(uuid.uuid4())
        steps_json = json.dumps([step.to_dict() for step in steps])
        tags_json = json.dumps(tags or [])
        now = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO capability_tasks (
                    id, name, description, owner_id, steps, tags,
                    created_at, updated_at, is_active, version,
                    execution_count, success_count, failure_count
                ) VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, true, 1, 0, 0, 0)
                """,
                task_id,
                name,
                description,
                owner_id,
                steps_json,
                tags_json,
                now,
                now,
            )

        return CapabilityTaskDefinition(
            id=task_id,
            name=name,
            description=description,
            steps=steps,
            tags=tags or [],
            owner_id=owner_id,
        )

    async def get_task(self, task_id: str, owner_id: str) -> Optional[CapabilityTaskDefinition]:
        """Get a task by ID with owner isolation."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, name, description, owner_id, steps, tags, created_at
                FROM capability_tasks
                WHERE id = $1 AND owner_id = $2 AND is_active = true
                """,
                task_id,
                owner_id,
            )

        if not row:
            return None

        return self._row_to_task(row)

    async def list_tasks(
        self,
        owner_id: str,
        skip: int = 0,
        limit: int = 50,
        tags: Optional[List[str]] = None,
        active_only: bool = True,
    ) -> Tuple[List[CapabilityTaskDefinition], int]:
        """List tasks for a user."""
        conditions = ["owner_id = $1"]
        params: list = [owner_id]

        if active_only:
            conditions.append("is_active = true")

        where_clause = " AND ".join(conditions)

        async with self.pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM capability_tasks WHERE {where_clause}",
                *params,
            )
            rows = await conn.fetch(
                f"""
                SELECT id, name, description, owner_id, steps, tags, created_at
                FROM capability_tasks
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                *params,
                limit,
                skip,
            )

        tasks = [self._row_to_task(row) for row in rows]
        return tasks, total or 0

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
        async with self.pool.acquire() as conn:
            existing = await conn.fetchrow(
                """
                SELECT version FROM capability_tasks
                WHERE id = $1 AND owner_id = $2 AND is_active = true
                """,
                task_id,
                owner_id,
            )

            if not existing:
                return None

            now = datetime.now(timezone.utc)
            set_parts = ["updated_at = $1", "version = $2"]
            params: list = [now, existing["version"] + 1]

            if name is not None:
                params.append(name)
                set_parts.append(f"name = ${len(params)}")
            if description is not None:
                params.append(description)
                set_parts.append(f"description = ${len(params)}")
            if steps is not None:
                params.append(json.dumps([s.to_dict() for s in steps]))
                set_parts.append(f"steps = ${len(params)}::jsonb")
            if tags is not None:
                params.append(json.dumps(tags))
                set_parts.append(f"tags = ${len(params)}::jsonb")

            params.append(task_id)
            id_param = len(params)
            params.append(owner_id)
            owner_param = len(params)

            await conn.execute(
                f"""
                UPDATE capability_tasks
                SET {", ".join(set_parts)}
                WHERE id = ${id_param} AND owner_id = ${owner_param}
                """,
                *params,
            )

        return await self.get_task(task_id, owner_id)

    async def delete_task(self, task_id: str, owner_id: str) -> bool:
        """Soft delete a task (sets is_active=False)."""
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE capability_tasks
                SET is_active = false, updated_at = $3
                WHERE id = $1 AND owner_id = $2
                """,
                task_id,
                owner_id,
                datetime.now(timezone.utc),
            )

        return result != "UPDATE 0"

    # ============ Execution CRUD ============

    async def persist_execution(self, result: TaskExecutionResult) -> str:
        """Save execution result to database."""
        now = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO capability_executions (
                        id, task_id, owner_id, status, error_message, step_results,
                        final_outputs, total_duration_ms, progress_percent,
                        completed_steps, total_steps, started_at, completed_at, created_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb,
                        $8, $9, $10, $11, $12, $13, $14
                    )
                    """,
                    result.execution_id,
                    result.task_id,
                    result.owner_id,
                    result.status,
                    result.error,
                    json.dumps([r.to_dict() for r in result.step_results]),
                    json.dumps(result.final_outputs),
                    result.total_duration_ms,
                    result.progress_percent,
                    sum(1 for r in result.step_results if r.status == "completed"),
                    len(result.step_results),
                    result.started_at,
                    result.completed_at,
                    now,
                )

                await conn.execute(
                    """
                    UPDATE capability_tasks SET
                        execution_count = execution_count + 1,
                        success_count = success_count + CASE WHEN $2 = 'completed' THEN 1 ELSE 0 END,
                        failure_count = failure_count + CASE WHEN $2 = 'failed' THEN 1 ELSE 0 END,
                        last_executed_at = $3
                    WHERE id = $1
                    """,
                    result.task_id,
                    result.status,
                    now,
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
                SELECT id, task_id, owner_id, status, error_message, step_results,
                       final_outputs, total_duration_ms, progress_percent,
                       started_at, completed_at
                FROM capability_executions
                WHERE id = $1 AND owner_id = $2
                """,
                execution_id,
                owner_id,
            )

        if not row:
            return None

        return self._row_to_execution(row)

    async def list_executions(
        self,
        task_id: str,
        owner_id: str,
        skip: int = 0,
        limit: int = 50,
        status_filter: Optional[str] = None,
    ) -> Tuple[List[TaskExecutionResult], int]:
        """List executions for a task."""
        conditions = ["task_id = $1", "owner_id = $2"]
        params: list = [task_id, owner_id]

        if status_filter:
            params.append(status_filter)
            conditions.append(f"status = ${len(params)}")

        where_clause = " AND ".join(conditions)
        limit_param = len(params) + 1
        offset_param = len(params) + 2

        async with self.pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM capability_executions WHERE {where_clause}",
                *params,
            )
            rows = await conn.fetch(
                f"""
                SELECT id, task_id, owner_id, status, error_message, step_results,
                       final_outputs, total_duration_ms, progress_percent,
                       started_at, completed_at
                FROM capability_executions
                WHERE {where_clause}
                ORDER BY started_at DESC
                LIMIT ${limit_param} OFFSET ${offset_param}
                """,
                *params,
                limit,
                skip,
            )

        executions = [self._row_to_execution(row) for row in rows]
        return executions, total or 0

    # ============ Private Helpers ============

    def _row_to_task(self, row: Any) -> CapabilityTaskDefinition:
        """Convert asyncpg row to CapabilityTaskDefinition."""
        steps_data = row["steps"] if isinstance(row["steps"], list) else json.loads(row["steps"])
        tags_data = row["tags"] if isinstance(row["tags"], list) else json.loads(row["tags"] or "[]")

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

        return CapabilityTaskDefinition(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            steps=steps,
            tags=tags_data,
            owner_id=row["owner_id"],
            created_at=row.get("created_at"),
        )

    def _row_to_execution(self, row: Any) -> TaskExecutionResult:
        """Convert asyncpg row to TaskExecutionResult."""
        from .capability_task_executor import StepResult

        step_results_data = (
            row["step_results"]
            if isinstance(row["step_results"], list)
            else json.loads(row["step_results"] or "[]")
        )
        final_outputs = (
            row["final_outputs"]
            if isinstance(row["final_outputs"], dict)
            else json.loads(row["final_outputs"] or "{}")
        )

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
            for step_data in step_results_data
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
