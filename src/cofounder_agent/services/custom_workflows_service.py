"""
Custom Workflows Service - Business logic for creating, managing, and executing custom workflows

Provides:
- CRUD operations for custom workflows (persisted to PostgreSQL)
- Workflow validation
- Workflow execution orchestration
- Phase discovery and metadata
"""

import json
from services.logger_config import get_logger
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase, WorkflowValidationResult
from services.phase_registry import PhaseRegistry
from services.workflow_executor import WorkflowExecutor
from services.workflow_validator import WorkflowValidator
from utils.sql_safety import ParameterizedQueryBuilder, SQLOperator

logger = get_logger(__name__)


class CustomWorkflowsService:
    """Service for managing custom workflows"""

    def __init__(self, database_service):
        """Initialize with database service"""
        self.database_service = database_service
        self._available_phases_cache: Optional[List[Dict[str, Any]]] = None
        self.phase_registry = PhaseRegistry.get_instance()
        self.workflow_validator = WorkflowValidator()
        self.workflow_executor = WorkflowExecutor()
        logger.info("CustomWorkflowsService initialized")

    async def create_workflow(self, workflow: CustomWorkflow, owner_id: str) -> CustomWorkflow:
        """
        Create and persist a new custom workflow.

        Args:
            workflow: Workflow definition
            owner_id: Owner user ID

        Returns:
            Created workflow with ID and timestamps

        Raises:
            ValueError: If workflow is invalid
        """
        # Validate before saving
        validation = self.validate_workflow(workflow)
        if not validation.valid:
            raise ValueError(f"Workflow validation failed: {', '.join(validation.errors)}")

        # Add metadata
        workflow.id = str(uuid.uuid4())
        workflow.owner_id = owner_id
        now = datetime.now(timezone.utc)
        workflow.created_at = now
        workflow.updated_at = now

        # Save to database
        try:
            await self._insert_workflow(workflow)
            logger.info(
                "Created custom workflow: %s ('%s') for user %s", workflow.id, workflow.name, owner_id
            )
            return workflow
        except Exception as e:
            logger.error("Failed to create workflow: %s", e, exc_info=True)
            raise

    async def get_workflow(self, workflow_id: str, owner_id: str) -> Optional[CustomWorkflow]:
        """
        Retrieve a workflow by ID (user must own it or it must be a template).

        Args:
            workflow_id: Workflow UUID
            owner_id: Requesting user ID

        Returns:
            CustomWorkflow or None if not found
        """
        try:
            row = await self.database_service.pool.fetchrow(
                """
                SELECT id, name, description, phases, owner_id, created_at, updated_at, tags, is_template
                FROM custom_workflows
                WHERE id = $1 AND (owner_id = $2 OR is_template = true)
                """,
                workflow_id,
                owner_id,
            )
            if not row:
                logger.warning(
                    "Workflow %s not found or access denied for user %s", workflow_id, owner_id
                )
                return None
            return self._row_to_workflow(row)
        except Exception as e:
            logger.error("Error retrieving workflow %s: %s", workflow_id, e, exc_info=True)
            raise

    async def get_workflow_by_name(self, name: str, owner_id: str) -> Optional[CustomWorkflow]:
        """
        Retrieve a workflow by name for a given owner.

        Args:
            name: Workflow name
            owner_id: Requesting user ID

        Returns:
            CustomWorkflow or None if not found
        """
        try:
            row = await self.database_service.pool.fetchrow(
                """
                SELECT id, name, description, phases, owner_id, created_at, updated_at, tags, is_template
                FROM custom_workflows
                WHERE name = $1 AND owner_id = $2
                """,
                name,
                owner_id,
            )
            if not row:
                logger.warning("Workflow '%s' not found for user %s", name, owner_id)
                return None
            return self._row_to_workflow(row)
        except Exception as e:
            logger.error("Error retrieving workflow by name '%s': %s", name, e, exc_info=True)
            raise

    async def list_workflows(
        self, owner_id: str, include_templates: bool = True, page: int = 1, page_size: int = 20
    ) -> Dict[str, Any]:
        """
        List workflows for a user.

        Args:
            owner_id: Owner user ID
            include_templates: Whether to include shared templates
            page: Page number (1-indexed)
            page_size: Results per page

        Returns:
            Dict with workflows, total_count, pagination info
        """
        try:
            offset = (page - 1) * page_size

            # Single acquire: window function avoids a second COUNT round-trip and keeps
            # both count and data under the same connection snapshot.
            async with self.database_service.pool.acquire() as conn:
                if include_templates:
                    rows = await conn.fetch(
                        """
                        SELECT id, name, description, phases, owner_id, created_at, updated_at,
                               tags, is_template, COUNT(*) OVER () AS total_count
                        FROM custom_workflows
                        WHERE owner_id = $1 OR is_template = true
                        ORDER BY updated_at DESC
                        LIMIT $2 OFFSET $3
                        """,
                        owner_id,
                        page_size,
                        offset,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, name, description, phases, owner_id, created_at, updated_at,
                               tags, is_template, COUNT(*) OVER () AS total_count
                        FROM custom_workflows
                        WHERE owner_id = $1
                        ORDER BY updated_at DESC
                        LIMIT $2 OFFSET $3
                        """,
                        owner_id,
                        page_size,
                        offset,
                    )

            total_count = rows[0]["total_count"] if rows else 0
            workflows = [self._row_to_workflow(row) for row in rows]

            logger.info("Listed %d workflows for user %s", len(workflows), owner_id)
            return {
                "workflows": workflows,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "has_next": (page * page_size) < total_count,
            }
        except Exception as e:
            logger.error("Error listing workflows: %s", e, exc_info=True)
            return {
                "workflows": [],
                "total_count": 0,
                "page": page,
                "page_size": page_size,
                "has_next": False,
            }

    async def update_workflow(
        self, workflow_id: str, workflow: CustomWorkflow, owner_id: str
    ) -> CustomWorkflow:
        """
        Update an existing workflow.

        Args:
            workflow_id: Workflow UUID
            workflow: Updated workflow definition
            owner_id: Owner user ID

        Returns:
            Updated workflow

        Raises:
            ValueError: If user doesn't own workflow or validation fails
        """
        # Verify ownership
        existing = await self.get_workflow(workflow_id, owner_id)
        if not existing:
            raise ValueError(f"Workflow {workflow_id} not found or access denied")

        if existing.owner_id != owner_id:
            raise ValueError("Cannot update workflow owned by different user")

        # Validate
        validation = self.validate_workflow(workflow)
        if not validation.valid:
            raise ValueError(f"Workflow validation failed: {', '.join(validation.errors)}")

        # Update metadata
        workflow.id = workflow_id
        workflow.owner_id = owner_id
        workflow.created_at = existing.created_at
        workflow.updated_at = datetime.now(timezone.utc)

        # Save to database
        try:
            await self._update_workflow_in_db(workflow)
            logger.info("Updated custom workflow: %s for user %s", workflow_id, owner_id)
            return workflow
        except Exception as e:
            logger.error("Failed to update workflow: %s", e, exc_info=True)
            raise

    async def delete_workflow(self, workflow_id: str, owner_id: str) -> bool:
        """
        Delete a workflow (if user owns it).

        Args:
            workflow_id: Workflow UUID
            owner_id: Owner user ID

        Returns:
            True if deleted

        Raises:
            ValueError: If user doesn't own workflow
        """
        # Verify ownership
        existing = await self.get_workflow(workflow_id, owner_id)
        if not existing:
            raise ValueError(f"Workflow {workflow_id} not found or access denied")

        if existing.owner_id != owner_id:
            raise ValueError("Cannot delete workflow owned by different user")

        try:
            await self.database_service.pool.execute(
                "DELETE FROM custom_workflows WHERE id = $1", workflow_id
            )
            logger.info("Deleted custom workflow: %s for user %s", workflow_id, owner_id)
            return True
        except Exception as e:
            logger.error("Failed to delete workflow: %s", e, exc_info=True)
            raise

    def validate_workflow(self, workflow: CustomWorkflow) -> WorkflowValidationResult:
        """
        Validate a workflow definition.

        Delegates to the injected WorkflowValidator instance.

        Args:
            workflow: Workflow to validate

        Returns:
            ValidationResult with errors and warnings
        """
        valid, errors, warnings = self.workflow_validator.validate_workflow(workflow)
        return WorkflowValidationResult(valid=valid, errors=errors, warnings=warnings)

    async def get_available_phases(self) -> List[Dict[str, Any]]:
        """
        Get list of available phases that can be used in workflows.

        Delegates to the PhaseRegistry for the authoritative list of phases.

        Returns:
            List of available phase dicts
        """
        # Use cached version if available
        if self._available_phases_cache is not None:
            return self._available_phases_cache

        phases = self.phase_registry.list_phases()
        available_phases = [
            {
                "name": p.name,
                "agent_type": p.agent_type,
                "description": p.description,
                "timeout_seconds": p.timeout_seconds,
                "max_retries": p.max_retries,
                "required": p.required,
                "quality_threshold": p.quality_threshold,
                "tags": p.tags,
                "input_schema": p.input_schema,
                "output_schema": p.output_schema,
            }
            for p in phases
        ]

        self._available_phases_cache = available_phases
        logger.info("Loaded %d available phases", len(available_phases))
        return available_phases

    # ========================================================================
    # Private Methods
    # ========================================================================

    async def _insert_workflow(self, workflow: CustomWorkflow) -> None:
        """Insert workflow into database"""
        phases_json = json.dumps(
            [
                {
                    "name": p.name,
                    "agent": getattr(p, "agent", ""),
                    "description": getattr(p, "description", ""),
                    "timeout_seconds": getattr(p, "timeout_seconds", 300),
                    "max_retries": getattr(p, "max_retries", 2),
                    "skip_on_error": getattr(p, "skip_on_error", False),
                    "required": getattr(p, "required", True),
                    "quality_threshold": getattr(p, "quality_threshold", None),
                    "metadata": getattr(p, "metadata", {}),
                }
                for p in workflow.phases
            ]
        )

        await self.database_service.pool.execute(
            """
            INSERT INTO custom_workflows
            (id, name, description, phases, owner_id, created_at, updated_at, tags, is_template)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            workflow.id,
            workflow.name,
            workflow.description,
            phases_json,
            workflow.owner_id,
            workflow.created_at,
            workflow.updated_at,
            json.dumps(workflow.tags),
            workflow.is_template,
        )

    async def _update_workflow_in_db(self, workflow: CustomWorkflow) -> None:
        """Update workflow in database"""
        phases_json = json.dumps(
            [
                {
                    "name": p.name,
                    "agent": getattr(p, "agent", ""),
                    "description": getattr(p, "description", ""),
                    "timeout_seconds": getattr(p, "timeout_seconds", 300),
                    "max_retries": getattr(p, "max_retries", 2),
                    "skip_on_error": getattr(p, "skip_on_error", False),
                    "required": getattr(p, "required", True),
                    "quality_threshold": getattr(p, "quality_threshold", None),
                    "metadata": getattr(p, "metadata", {}),
                }
                for p in workflow.phases
            ]
        )

        await self.database_service.pool.execute(
            """
            UPDATE custom_workflows
            SET name = $1, description = $2, phases = $3, updated_at = $4, tags = $5, is_template = $6
            WHERE id = $7
            """,
            workflow.name,
            workflow.description,
            phases_json,
            workflow.updated_at,
            json.dumps(workflow.tags),
            workflow.is_template,
            workflow.id,
        )

    def _row_to_workflow(self, row) -> CustomWorkflow:
        """Convert database row to CustomWorkflow object"""
        phases_data = json.loads(row["phases"]) if isinstance(row["phases"], str) else row["phases"]
        tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row.get("tags", [])

        phases = []
        for i, p in enumerate(phases_data):
            if "index" in p:
                # New WorkflowPhase format
                phases.append(
                    WorkflowPhase(
                        index=p["index"],
                        name=p["name"],
                        user_inputs=p.get("user_inputs", {}),
                        input_mapping=p.get("input_mapping", {}),
                    )
                )
            else:
                # Legacy PhaseConfig format — convert to WorkflowPhase using list position
                phases.append(
                    WorkflowPhase(
                        index=i,
                        name=p["name"],
                        user_inputs={},
                        input_mapping={},
                    )
                )

        return CustomWorkflow(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            phases=phases,
            owner_id=row["owner_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            tags=tags,
            is_template=row.get("is_template", False),
        )

    # ========== Workflow Execution Persistence ==========

    async def persist_workflow_execution(
        self,
        execution_id: str,
        workflow_id: str,
        owner_id: str,
        execution_status: str,
        phase_results: dict,
        duration_ms: int,
        initial_input: Optional[dict] = None,
        final_output: Optional[dict] = None,
        error_message: Optional[str] = None,
        completed_phases: int = 0,
        total_phases: int = 0,
        progress_percent: int = 0,
        tags: Optional[list] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        Save workflow execution results to database.

        Args:
            execution_id: Unique execution ID
            workflow_id: ID of the workflow executed
            owner_id: User ID who owns the workflow
            execution_status: Final status (completed, failed, cancelled)
            phase_results: Dict of phase name -> PhaseResult
            duration_ms: Total execution time
            initial_input: Input data for workflow
            final_output: Final output from workflow
            error_message: Error message if failed
            completed_phases: Number of phases completed
            total_phases: Total phases in workflow
            progress_percent: Completion percentage
            tags: Optional tags for execution
            metadata: Optional metadata

        Returns:
            True if successful
        """
        try:
            now = datetime.now(timezone.utc)

            # Convert phase results to JSON
            phase_results_json = json.dumps(phase_results) if phase_results else "{}"

            await self.database_service.pool.execute(
                """
                INSERT INTO workflow_executions (
                    id, workflow_id, owner_id, execution_status,
                    created_at, started_at, completed_at, duration_ms,
                    initial_input, phase_results, final_output, error_message,
                    progress_percent, completed_phases, total_phases,
                    tags, metadata
                ) VALUES (
                    $1, $2, $3, $4,
                    $5, $6, $7, $8,
                    $9, $10, $11, $12,
                    $13, $14, $15,
                    $16, $17
                )
                ON CONFLICT (id) DO UPDATE SET
                    execution_status = EXCLUDED.execution_status,
                    completed_at = EXCLUDED.completed_at,
                    duration_ms = EXCLUDED.duration_ms,
                    phase_results = EXCLUDED.phase_results,
                    final_output = EXCLUDED.final_output,
                    error_message = EXCLUDED.error_message,
                    progress_percent = EXCLUDED.progress_percent,
                    completed_phases = EXCLUDED.completed_phases,
                    total_phases = EXCLUDED.total_phases,
                    tags = EXCLUDED.tags,
                    metadata = EXCLUDED.metadata
                """,
                execution_id,
                workflow_id,
                owner_id,
                execution_status,
                now,  # created_at
                now,  # started_at
                now if execution_status in ["completed", "failed"] else None,  # completed_at
                duration_ms,
                json.dumps(initial_input) if initial_input else None,
                phase_results_json,
                json.dumps(final_output) if final_output else None,
                error_message,
                progress_percent,
                completed_phases,
                total_phases,
                json.dumps(tags or []),
                json.dumps(metadata or {}),
            )

            logger.info(
                "Persisted workflow execution: %s for workflow %s, status: %s, duration: %dms",
                execution_id, workflow_id, execution_status, duration_ms
            )
            return True

        except Exception as e:
            logger.error("Failed to persist workflow execution %s: %s", execution_id, e, exc_info=True)
            return False

    async def get_workflow_execution(
        self, execution_id: str, owner_id: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get a workflow execution by ID.

        Args:
            execution_id: ID of execution to retrieve
            owner_id: Optional owner ID to verify ownership

        Returns:
            Execution record or None if not found
        """
        try:
            if owner_id:
                row = await self.database_service.pool.fetchrow(
                    "SELECT * FROM workflow_executions WHERE id = $1 AND owner_id = $2",
                    execution_id,
                    owner_id,
                )
            else:
                row = await self.database_service.pool.fetchrow(
                    "SELECT * FROM workflow_executions WHERE id = $1",
                    execution_id,
                )

            if not row:
                return None

            return self._row_to_execution(row)

        except Exception as e:
            logger.error("Failed to get workflow execution %s: %s", execution_id, e, exc_info=True)
            return None

    async def get_workflow_executions(
        self,
        workflow_id: str,
        owner_id: str,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ):
        """
        Get executions for a workflow.

        Args:
            workflow_id: ID of workflow
            owner_id: Owner ID for authorization
            limit: Max results
            offset: Pagination offset
            status: Optional status filter (completed, failed, pending)

        Returns:
            List of execution records and total count
        """
        try:
            # Use ParameterizedQueryBuilder for safe, consistent parameterization.
            # Window function keeps COUNT and data in one round-trip.
            builder = ParameterizedQueryBuilder()
            where_conditions: List[tuple] = [
                ("workflow_id", SQLOperator.EQ, workflow_id),
                ("owner_id", SQLOperator.EQ, owner_id),
            ]
            if status:
                where_conditions.append(("execution_status", SQLOperator.EQ, status))

            _base_sql, base_params = builder.select(
                columns=["*", "COUNT(*) OVER () AS total_count"],
                table="workflow_executions",
                where_clauses=where_conditions,
                order_by=[("created_at", "DESC")],
            )
            # Append LIMIT/OFFSET as the final parameters
            limit_ph = builder.add_param(limit)
            offset_ph = builder.add_param(offset)
            full_sql = _base_sql + f" LIMIT {limit_ph} OFFSET {offset_ph}"

            async with self.database_service.pool.acquire() as conn:
                rows = await conn.fetch(full_sql, *builder.params)

            total_count = rows[0]["total_count"] if rows else 0

            executions = [self._row_to_execution(row) for row in rows]

            return {
                "total": total_count,
                "executions": executions,
                "limit": limit,
                "offset": offset,
            }

        except Exception as e:
            logger.error(
                "[get_workflow_executions] Failed to get workflow executions for %s: %s",
                workflow_id, e,
                exc_info=True,
            )
            return {
                "total": 0,
                "executions": [],
                "limit": limit,
                "offset": offset,
            }

    def _row_to_execution(self, row) -> Dict:
        """Convert database row to execution dictionary"""
        return {
            "id": str(row["id"]),
            "workflow_id": str(row["workflow_id"]),
            "owner_id": row["owner_id"],
            "execution_status": row["execution_status"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
            "duration_ms": row["duration_ms"],
            "initial_input": json.loads(row["initial_input"]) if row["initial_input"] else None,
            "phase_results": json.loads(row["phase_results"]) if row["phase_results"] else {},
            "final_output": json.loads(row["final_output"]) if row["final_output"] else None,
            "error_message": row["error_message"],
            "progress_percent": row["progress_percent"],
            "completed_phases": row["completed_phases"],
            "total_phases": row["total_phases"],
            "tags": json.loads(row["tags"]) if row["tags"] else [],
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        }
