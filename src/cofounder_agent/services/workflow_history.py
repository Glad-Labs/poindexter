"""
Workflow Execution History Service

Tracks workflow executions, performance metrics, and learning patterns.
Uses asyncpg for async PostgreSQL access with connection pooling.

Responsibilities:
- Save workflow execution results
- Retrieve execution history
- Calculate performance statistics
- Track execution patterns for optimization
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class WorkflowHistoryService:
    """
    Service for managing workflow execution history in PostgreSQL.

    Tracks all workflow executions with:
    - Input/output data
    - Task results
    - Execution metadata
    - Performance metrics
    - Error information
    """

    def __init__(self, db_pool):
        """
        Initialize workflow history service

        Args:
            db_pool: asyncpg connection pool from DatabaseService
        """
        self.pool = db_pool

    async def save_workflow_execution(
        self,
        workflow_id: str,
        workflow_type: str,
        user_id: str,
        status: str,
        input_data: Dict[str, Any],
        output_data: Optional[Dict[str, Any]] = None,
        task_results: Optional[List[Dict[str, Any]]] = None,
        error_message: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        duration_seconds: Optional[float] = None,
        execution_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Save workflow execution to database

        Args:
            workflow_id: ID of the workflow executed
            workflow_type: Type of workflow (e.g., 'content_generation')
            user_id: ID of user who triggered execution
            status: Execution status (PENDING, RUNNING, COMPLETED, FAILED, PAUSED)
            input_data: Input parameters for the workflow
            output_data: Output/results from workflow execution
            task_results: List of individual task results
            error_message: Error message if execution failed
            start_time: When execution started
            end_time: When execution ended
            duration_seconds: Total execution duration in seconds
            execution_metadata: Additional execution metadata

        Returns:
            Dict with execution record (id, created_at, etc.)

        Raises:
            ValueError: If required fields are missing
            asyncpg.PostgresError: If database operation fails
        """
        if not all([workflow_id, workflow_type, user_id, status]):
            raise ValueError("workflow_id, workflow_type, user_id, status are required")

        execution_id = str(uuid4())
        now = datetime.utcnow()

        # Use provided times or defaults
        start_time = start_time or now
        end_time = end_time or (now if status == "COMPLETED" else None)

        # Calculate duration if not provided
        if duration_seconds is None and end_time:
            duration_seconds = (end_time - start_time).total_seconds()

        execution_metadata = execution_metadata or {}

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO workflow_executions (
                        id, workflow_id, workflow_type, user_id, status,
                        input_data, output_data, task_results, error_message,
                        start_time, end_time, duration_seconds, execution_metadata
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    RETURNING *
                    """,
                    execution_id,
                    workflow_id,
                    workflow_type,
                    user_id,
                    status,
                    input_data,
                    output_data,
                    task_results or [],
                    error_message,
                    start_time,
                    end_time,
                    duration_seconds,
                    execution_metadata,
                )

                logger.info(
                    f"✅ Workflow execution saved: {workflow_id} "
                    f"(user: {user_id}, status: {status}, duration: {duration_seconds}s)"
                )

                return self._row_to_dict(row)

        except Exception as e:
            logger.error(f"❌ Failed to save workflow execution: {e}")
            raise

    async def get_workflow_execution(self, execution_id: str) -> Optional[Dict[str, Any]]:
        """
        Get specific workflow execution

        Args:
            execution_id: ID of execution to retrieve

        Returns:
            Execution record dict or None if not found
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM workflow_executions WHERE id = $1",
                    execution_id,
                )
                return self._row_to_dict(row) if row else None
        except Exception as e:
            logger.error(f"❌ Failed to get workflow execution {execution_id}: {e}")
            raise

    async def get_user_workflow_history(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        status_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get workflow execution history for a user

        Args:
            user_id: User ID to get history for
            limit: Number of results to return (default: 50)
            offset: Offset for pagination (default: 0)
            status_filter: Optional status filter (e.g., 'COMPLETED')

        Returns:
            Dict with:
            - executions: List of execution records
            - total: Total count of executions
            - limit, offset: Pagination info
        """
        try:
            async with self.pool.acquire() as conn:
                # Build query with optional status filter
                where_clause = "user_id = $1"
                params = [user_id, limit, offset]

                if status_filter:
                    where_clause += " AND status = $4"
                    params = [user_id, limit, offset, status_filter]

                # Get total count
                count_row = await conn.fetchval(
                    f"SELECT COUNT(*) FROM workflow_executions WHERE user_id = $1",
                    user_id,
                )

                # Get paginated results
                rows = await conn.fetch(
                    f"""
                    SELECT * FROM workflow_executions
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    *params[:3],  # Use only user_id, limit, offset for main query
                )

                if status_filter:
                    # Re-fetch with status filter
                    rows = await conn.fetch(
                        f"""
                        SELECT * FROM workflow_executions
                        WHERE user_id = $1 AND status = $4
                        ORDER BY created_at DESC
                        LIMIT $2 OFFSET $3
                        """,
                        *params,
                    )

                return {
                    "executions": [self._row_to_dict(row) for row in rows],
                    "total": count_row,
                    "limit": limit,
                    "offset": offset,
                    "status_filter": status_filter,
                }

        except Exception as e:
            logger.error(f"❌ Failed to get workflow history for user {user_id}: {e}")
            raise

    async def get_workflow_statistics(
        self,
        user_id: str,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get workflow execution statistics for a user

        Args:
            user_id: User ID to get stats for
            days: Number of days to include (default: 30)

        Returns:
            Dict with statistics:
            - total_executions: Total number of executions
            - completed: Number completed successfully
            - failed: Number that failed
            - success_rate: Percentage of successful executions
            - average_duration: Average execution time in seconds
            - workflows: Dict of stats per workflow type
            - most_common_workflow: Most frequently executed workflow type
        """
        try:
            async with self.pool.acquire() as conn:
                cutoff_date = datetime.utcnow() - timedelta(days=days)

                # Get overall stats
                stats = await conn.fetchrow(
                    """
                    SELECT
                        COUNT(*) as total_executions,
                        SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
                        AVG(duration_seconds) as average_duration,
                        MIN(start_time) as first_execution,
                        MAX(end_time) as last_execution
                    FROM workflow_executions
                    WHERE user_id = $1 AND created_at >= $2
                    """,
                    user_id,
                    cutoff_date,
                )

                # Get stats per workflow type
                workflow_stats = await conn.fetch(
                    """
                    SELECT
                        workflow_type,
                        COUNT(*) as executions,
                        SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as completed,
                        SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed,
                        AVG(duration_seconds) as average_duration
                    FROM workflow_executions
                    WHERE user_id = $1 AND created_at >= $2
                    GROUP BY workflow_type
                    ORDER BY executions DESC
                    """,
                    user_id,
                    cutoff_date,
                )

                # Calculate success rate
                total = stats["total_executions"] or 0
                completed = stats["completed"] or 0
                failed = stats["failed"] or 0
                success_rate = (completed / total * 100) if total > 0 else 0

                # Most common workflow type
                most_common = workflow_stats[0] if workflow_stats else None
                most_common_workflow = most_common["workflow_type"] if most_common else None

                return {
                    "user_id": user_id,
                    "period_days": days,
                    "total_executions": total,
                    "completed_executions": completed,
                    "failed_executions": failed,
                    "success_rate_percent": round(success_rate, 2),
                    "average_duration_seconds": (
                        float(stats["average_duration"]) if stats["average_duration"] else 0
                    ),
                    "first_execution": stats["first_execution"],
                    "last_execution": stats["last_execution"],
                    "workflows": [
                        {
                            "workflow_type": row["workflow_type"],
                            "executions": row["executions"],
                            "completed": row["completed"],
                            "failed": row["failed"],
                            "average_duration": (
                                float(row["average_duration"]) if row["average_duration"] else 0
                            ),
                            "success_rate": (
                                (row["completed"] / row["executions"] * 100)
                                if row["executions"] > 0
                                else 0
                            ),
                        }
                        for row in workflow_stats
                    ],
                    "most_common_workflow": most_common_workflow,
                }

        except Exception as e:
            logger.error(f"❌ Failed to get statistics for user {user_id}: {e}")
            raise

    async def update_workflow_execution(
        self, execution_id: str, **updates
    ) -> Optional[Dict[str, Any]]:
        """
        Update workflow execution record

        Args:
            execution_id: ID of execution to update
            **updates: Fields to update (status, output_data, error_message, etc.)

        Returns:
            Updated execution record or None if not found
        """
        if not updates:
            return await self.get_workflow_execution(execution_id)

        # Build dynamic UPDATE query
        set_clauses = []
        values = []
        param_count = 1

        updates["updated_at"] = datetime.utcnow()

        for key, value in updates.items():
            set_clauses.append(f"{key} = ${param_count}")
            values.append(value)
            param_count += 1

        values.append(execution_id)

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    UPDATE workflow_executions
                    SET {', '.join(set_clauses)}
                    WHERE id = ${param_count}
                    RETURNING *
                    """,
                    *values,
                )

                logger.info(f"✅ Updated workflow execution {execution_id}")
                return self._row_to_dict(row) if row else None

        except Exception as e:
            logger.error(f"❌ Failed to update workflow execution {execution_id}: {e}")
            raise

    async def get_performance_metrics(
        self,
        user_id: str,
        workflow_type: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Get detailed performance metrics for workflow optimization

        Args:
            user_id: User ID to analyze
            workflow_type: Optional specific workflow type to analyze
            days: Number of days to include (default: 30)

        Returns:
            Dict with performance metrics:
            - execution_times: Distribution of execution times
            - common_errors: Most frequent error types
            - optimization_opportunities: Suggested optimizations
            - efficiency_trend: Trend over time
        """
        try:
            async with self.pool.acquire() as conn:
                cutoff_date = datetime.utcnow() - timedelta(days=days)

                # Query condition
                where = "user_id = $1 AND created_at >= $2"
                params = [user_id, cutoff_date]
                param_idx = 3

                if workflow_type:
                    where += f" AND workflow_type = ${param_idx}"
                    params.insert(2, workflow_type)
                    param_idx = 4

                # Get execution time distribution
                time_dist = await conn.fetch(
                    f"""
                    SELECT
                        CASE
                            WHEN duration_seconds < 5 THEN 'very_fast'
                            WHEN duration_seconds < 15 THEN 'fast'
                            WHEN duration_seconds < 60 THEN 'normal'
                            WHEN duration_seconds < 300 THEN 'slow'
                            ELSE 'very_slow'
                        END as speed_category,
                        COUNT(*) as count,
                        AVG(duration_seconds) as avg_duration
                    FROM workflow_executions
                    WHERE {where}
                    GROUP BY speed_category
                    """,
                    *params[:2],
                )

                # Get error patterns
                errors = await conn.fetch(
                    f"""
                    SELECT
                        error_message,
                        COUNT(*) as frequency
                    FROM workflow_executions
                    WHERE {where} AND status = 'FAILED' AND error_message IS NOT NULL
                    GROUP BY error_message
                    ORDER BY frequency DESC
                    LIMIT 5
                    """,
                    *params[:2],
                )

                return {
                    "user_id": user_id,
                    "workflow_type": workflow_type,
                    "period_days": days,
                    "execution_time_distribution": [
                        {
                            "category": row["speed_category"],
                            "count": row["count"],
                            "average_seconds": float(row["avg_duration"]),
                        }
                        for row in time_dist
                    ],
                    "error_patterns": [
                        {
                            "error": row["error_message"],
                            "frequency": row["frequency"],
                        }
                        for row in errors
                    ],
                    "optimization_tips": self._generate_optimization_tips(time_dist, errors),
                }

        except Exception as e:
            logger.error(f"❌ Failed to get performance metrics for {user_id}: {e}")
            raise

    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert asyncpg row to dict with proper serialization"""
        if row is None:
            return None

        data = dict(row)

        # Convert datetime objects to ISO format strings
        for key in ["start_time", "end_time", "created_at", "updated_at"]:
            if key in data and data[key]:
                data[key] = data[key].isoformat()

        # Convert UUID to string
        if "id" in data and data["id"]:
            data["id"] = str(data["id"])
        if "workflow_id" in data and data["workflow_id"]:
            data["workflow_id"] = str(data["workflow_id"])

        # Convert Decimal to float
        if "duration_seconds" in data and data["duration_seconds"]:
            data["duration_seconds"] = float(data["duration_seconds"])

        return data

    def _generate_optimization_tips(self, time_dist, errors) -> List[str]:
        """Generate optimization tips based on metrics"""
        tips = []

        # Check for slow executions
        for row in time_dist:
            if row["speed_category"] in ["slow", "very_slow"]:
                tips.append(
                    f"Workflows in '{row['speed_category']}' category "
                    f"({row['count']} executions). Consider parallel processing or caching."
                )

        # Check for frequent errors
        if errors and errors[0]["frequency"] > 3:
            tips.append(
                f"Most common error appears {errors[0]['frequency']} times. "
                f"Consider adding error handling or input validation."
            )

        if not tips:
            tips.append("Performance is good! Continue monitoring.")

        return tips
