"""
Custom Workflows Service - Business logic for creating, managing, and executing custom workflows

Provides:
- CRUD operations for custom workflows (persisted to PostgreSQL)
- Workflow validation
- Workflow execution orchestration
- Phase discovery and metadata
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from schemas.custom_workflow_schemas import (
    AvailablePhase,
    CustomWorkflow,
    PhaseConfig,
    WorkflowValidationResult,
)

logger = logging.getLogger(__name__)


class CustomWorkflowsService:
    """Service for managing custom workflows"""

    def __init__(self, database_service):
        """Initialize with database service"""
        self.database_service = database_service
        self._available_phases_cache: Optional[List[AvailablePhase]] = None
        logger.info("CustomWorkflowsService initialized")

    async def create_workflow(
        self, workflow: CustomWorkflow, owner_id: str
    ) -> CustomWorkflow:
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
                f"Created custom workflow: {workflow.id} ('{workflow.name}') for user {owner_id}"
            )
            return workflow
        except Exception as e:
            logger.error(f"Failed to create workflow: {str(e)}", exc_info=True)
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
                logger.warning(f"Workflow {workflow_id} not found or access denied for user {owner_id}")
                return None
            return self._row_to_workflow(row)
        except Exception as e:
            logger.error(f"Error retrieving workflow {workflow_id}: {str(e)}", exc_info=True)
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

            # Get workflows owned by user or public templates
            if include_templates:
                rows = await self.database_service.pool.fetch(
                    """
                    SELECT id, name, description, phases, owner_id, created_at, updated_at, tags, is_template
                    FROM custom_workflows
                    WHERE owner_id = $1 OR is_template = true
                    ORDER BY updated_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    owner_id,
                    page_size,
                    offset,
                )
                total_count_row = await self.database_service.pool.fetchval(
                    """
                    SELECT COUNT(*) FROM custom_workflows
                    WHERE owner_id = $1 OR is_template = true
                    """,
                    owner_id,
                )
            else:
                rows = await self.database_service.pool.fetch(
                    """
                    SELECT id, name, description, phases, owner_id, created_at, updated_at, tags, is_template
                    FROM custom_workflows
                    WHERE owner_id = $1
                    ORDER BY updated_at DESC
                    LIMIT $2 OFFSET $3
                    """,
                    owner_id,
                    page_size,
                    offset,
                )
                total_count_row = await self.database_service.pool.fetchval(
                    """
                    SELECT COUNT(*) FROM custom_workflows WHERE owner_id = $1
                    """,
                    owner_id,
                )

            total_count = total_count_row or 0
            workflows = [self._row_to_workflow(row) for row in rows]

            logger.info(f"Listed {len(workflows)} workflows for user {owner_id}")
            return {
                "workflows": workflows,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "has_next": (page * page_size) < total_count,
            }
        except Exception as e:
            logger.error(f"Error listing workflows: {str(e)}", exc_info=True)
            raise

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
            raise ValueError(f"Cannot update workflow owned by different user")

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
            logger.info(f"Updated custom workflow: {workflow_id} for user {owner_id}")
            return workflow
        except Exception as e:
            logger.error(f"Failed to update workflow: {str(e)}", exc_info=True)
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
            raise ValueError(f"Cannot delete workflow owned by different user")

        try:
            await self.database_service.pool.execute(
                "DELETE FROM custom_workflows WHERE id = $1", workflow_id
            )
            logger.info(f"Deleted custom workflow: {workflow_id} for user {owner_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete workflow: {str(e)}", exc_info=True)
            raise

    def validate_workflow(self, workflow: CustomWorkflow) -> WorkflowValidationResult:
        """
        Validate a workflow definition.

        Checks:
        - Name and description not empty
        - At least one phase defined
        - No duplicate phase names
        - Phases are sequential (no cycles)
        - All referenced agents exist

        Args:
            workflow: Workflow to validate

        Returns:
            ValidationResult with errors and warnings
        """
        errors: List[str] = []
        warnings: List[str] = []

        # Basic validation
        if not workflow.name or not workflow.name.strip():
            errors.append("Workflow name cannot be empty")

        if not workflow.description or not workflow.description.strip():
            errors.append("Workflow description cannot be empty")

        if not workflow.phases or len(workflow.phases) == 0:
            errors.append("Workflow must have at least one phase")
            return WorkflowValidationResult(valid=False, errors=errors, warnings=warnings)

        # Check for duplicate phase names
        phase_names = [p.name for p in workflow.phases]
        if len(phase_names) != len(set(phase_names)):
            errors.append("Duplicate phase names in workflow")

        # Validate each phase
        for i, phase in enumerate(workflow.phases):
            try:
                # Validate component-level constraints
                if phase.timeout_seconds < 10:
                    warnings.append(f"Phase '{phase.name}' timeout {phase.timeout_seconds}s is very short")
                if phase.timeout_seconds > 3600:
                    warnings.append(f"Phase '{phase.name}' timeout {phase.timeout_seconds}s is very long")

                # TODO: Validate agent exists in registry when available
                # For now, just warn if agent looks invalid
                if not phase.agent or not phase.agent.strip():
                    errors.append(f"Phase '{phase.name}' must specify an agent")

            except Exception as e:
                errors.append(f"Error validating phase '{phase.name}': {str(e)}")

        # Workflow is valid if no errors (warnings don't block)
        valid = len(errors) == 0
        return WorkflowValidationResult(valid=valid, errors=errors, warnings=warnings)

    async def get_available_phases(self) -> List[AvailablePhase]:
        """
        Get list of available phases that can be used in workflows.

        This will be populated from agents/phases discovered at startup.
        For MVP, returns hardcoded list of known phases.

        Returns:
            List of available phases
        """
        # Use cached version if available
        if self._available_phases_cache:
            return self._available_phases_cache

        # Hardcoded known phases (TODO: Derive from agent registry)
        available_phases = [
            AvailablePhase(
                name="research",
                description="Web search and research phase - gathers information on topic",
                category="content",
                default_timeout_seconds=300,
                compatible_agents=["ResearchAgent"],
                capabilities=["web_search", "data_analysis"],
                default_retries=3,
                version="1.0",
            ),
            AvailablePhase(
                name="draft",
                description="Creative draft generation - produces initial content",
                category="content",
                default_timeout_seconds=300,
                compatible_agents=["CreativeAgent"],
                capabilities=["content_generation", "style_matching"],
                default_retries=2,
                version="1.0",
            ),
            AvailablePhase(
                name="assess",
                description="Quality assessment - evaluates content quality",
                category="quality",
                default_timeout_seconds=240,
                compatible_agents=["QAAgent", "QualityService"],
                capabilities=["quality_scoring", "feedback"],
                default_retries=1,
                version="1.0",
            ),
            AvailablePhase(
                name="refine",
                description="Content refinement - improves based on feedback",
                category="content",
                default_timeout_seconds=300,
                compatible_agents=["CreativeAgent"],
                capabilities=["content_refinement", "iteration"],
                default_retries=2,
                version="1.0",
            ),
            AvailablePhase(
                name="image",
                description="Image selection and generation - adds visual elements",
                category="media",
                default_timeout_seconds=600,
                compatible_agents=["ImageAgent"],
                capabilities=["image_generation", "image_selection"],
                default_retries=2,
                version="1.0",
            ),
            AvailablePhase(
                name="publish",
                description="Publishing - publishes to configured CMS",
                category="distribution",
                default_timeout_seconds=180,
                compatible_agents=["PublishingAgent"],
                capabilities=["publishing", "seo_optimization"],
                default_retries=1,
                version="1.0",
            ),
        ]

        self._available_phases_cache = available_phases
        logger.info(f"Loaded {len(available_phases)} available phases")
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
                    "agent": p.agent,
                    "description": p.description,
                    "timeout_seconds": p.timeout_seconds,
                    "max_retries": p.max_retries,
                    "skip_on_error": p.skip_on_error,
                    "required": p.required,
                    "quality_threshold": p.quality_threshold,
                    "metadata": p.metadata,
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
                    "agent": p.agent,
                    "description": p.description,
                    "timeout_seconds": p.timeout_seconds,
                    "max_retries": p.max_retries,
                    "skip_on_error": p.skip_on_error,
                    "required": p.required,
                    "quality_threshold": p.quality_threshold,
                    "metadata": p.metadata,
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

        phases = [
            PhaseConfig(
                name=p["name"],
                agent=p["agent"],
                description=p.get("description"),
                timeout_seconds=p.get("timeout_seconds", 300),
                max_retries=p.get("max_retries", 3),
                skip_on_error=p.get("skip_on_error", False),
                required=p.get("required", True),
                quality_threshold=p.get("quality_threshold"),
                metadata=p.get("metadata", {}),
            )
            for p in phases_data
        ]

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
            from datetime import datetime, timezone
            
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
                f"Persisted workflow execution: {execution_id} for workflow {workflow_id}, "
                f"status: {execution_status}, duration: {duration_ms}ms"
            )
            return True
            
        except Exception as e:
            logger.error(f"Failed to persist workflow execution {execution_id}: {e}", exc_info=True)
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
            logger.error(f"Failed to get workflow execution {execution_id}: {e}")
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
            # Build query
            where_clauses = ["workflow_id = $1", "owner_id = $2"]
            params = [workflow_id, owner_id]
            param_index = 3
            
            if status:
                where_clauses.append(f"execution_status = ${param_index}")
                params.append(status)
                param_index += 1
            
            where_sql = " AND ".join(where_clauses)
            
            # Get total count
            total_count = await self.database_service.pool.fetchval(
                f"SELECT COUNT(*) FROM workflow_executions WHERE {where_sql}",
                *params,
            )
            
            # Get paginated results
            rows = await self.database_service.pool.fetch(
                f"""
                SELECT * FROM workflow_executions
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ${param_index} OFFSET ${param_index + 1}
                """,
                *params,
                limit,
                offset,
            )
            
            executions = [self._row_to_execution(row) for row in rows]
            
            return {
                "total": total_count,
                "executions": executions,
                "limit": limit,
                "offset": offset,
            }
            
        except Exception as e:
            logger.error(f"Failed to get workflow executions for {workflow_id}: {e}")
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
