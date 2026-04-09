"""
Template Execution Service - Maps workflow templates to CustomWorkflow and executor

Provides:
- Template name validation and mapping
- CustomWorkflow construction for templates
- Integration with CustomWorkflowsService for actual execution
- Phase configuration with proper defaults
- Progress tracking and WebSocket integration (Phase 2)
"""

import uuid
from typing import Any, Dict, List, Optional

from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase
from services.logger_config import get_logger

logger = get_logger(__name__)


class TemplateExecutionService:
    """Service for executing workflow templates"""

    # Template definitions with phases and metadata
    TEMPLATES = {
        "blog_post": {
            "description": "Complete blog post generation with research, drafting, quality assessment, refinement, and publishing",
            "phases": ["research", "draft", "assess", "refine", "image", "publish"],
            "estimated_duration_seconds": 900,
            "metadata": {
                "word_count_target": 1500,
                "quality_threshold": 0.75,
                "requires_approval": True,
            },
        },
        "social_media": {
            "description": "Social media content generation with quick assessment and publishing",
            "phases": ["draft", "assess", "publish"],
            "estimated_duration_seconds": 300,
            "metadata": {
                "word_count_target": 280,
                "quality_threshold": 0.7,
                "requires_approval": False,
            },
        },
        "email": {
            "description": "Email content generation with assessment and formatting",
            "phases": ["draft", "assess", "publish"],
            "estimated_duration_seconds": 240,
            "metadata": {
                "word_count_target": 350,
                "quality_threshold": 0.75,
                "requires_approval": True,
            },
        },
        "newsletter": {
            "description": "Newsletter generation with full pipeline including research and refinement",
            "phases": ["research", "draft", "assess", "refine", "image", "publish"],
            "estimated_duration_seconds": 1200,
            "metadata": {
                "word_count_target": 2000,
                "quality_threshold": 0.8,
                "requires_approval": True,
            },
        },
        "market_analysis": {
            "description": "Market analysis generation with research and assessment",
            "phases": ["research", "assess", "publish"],
            "estimated_duration_seconds": 600,
            "metadata": {
                "word_count_target": 2500,
                "quality_threshold": 0.85,
                "requires_approval": True,
            },
        },
    }

    def __init__(self, custom_workflows_service, workflow_executor=None):
        """
        Initialize with CustomWorkflowsService for persistence and WorkflowExecutor for execution.

        Args:
            custom_workflows_service: Service for workflow persistence
            workflow_executor: WorkflowExecutor for running workflow phases
        """
        self.custom_workflows_service = custom_workflows_service
        if workflow_executor is None:
            from services.workflow_executor import WorkflowExecutor

            workflow_executor = WorkflowExecutor()
        self.workflow_executor = workflow_executor
        logger.info("TemplateExecutionService initialized")

    @staticmethod
    def validate_template_name(template_name: str) -> bool:
        """
        Validate that template name exists.

        Args:
            template_name: Template name to validate

        Returns:
            True if template exists

        Raises:
            ValueError: If template not found
        """
        if template_name not in TemplateExecutionService.TEMPLATES:
            valid_templates = list(TemplateExecutionService.TEMPLATES.keys())
            raise ValueError(
                f"Template '{template_name}' not found. Valid templates: {valid_templates}"
            )
        return True

    @staticmethod
    def get_template_definitions() -> Dict[str, Dict[str, Any]]:
        """
        Get all template definitions.

        Returns:
            Dict of template_name -> template config
        """
        return TemplateExecutionService.TEMPLATES

    def build_workflow_from_template(
        self,
        template_name: str,
        skip_phases: Optional[List[str]] = None,
        quality_threshold: Optional[float] = None,
        owner_id: str = "system",
        tags: Optional[List[str]] = None,
    ) -> CustomWorkflow:
        """
        Build a CustomWorkflow object from a template definition.

        Args:
            template_name: Name of the template
            skip_phases: Optional list of phases to skip
            quality_threshold: Optional override for quality threshold
            owner_id: Owner of the workflow (defaults to "system")
            tags: Optional tags for categorization

        Returns:
            CustomWorkflow ready for execution

        Raises:
            ValueError: If template not found
        """
        self.validate_template_name(template_name)

        template_config = self.TEMPLATES[template_name]
        all_phases = template_config["phases"]

        # Filter phases
        if skip_phases:
            phases_to_use = [p for p in all_phases if p not in skip_phases]
        else:
            phases_to_use = all_phases

        # Build WorkflowPhase objects
        workflow_phases = []
        for idx, phase_name in enumerate(phases_to_use):
            phase_obj = WorkflowPhase(  # type: ignore[call-arg]
                index=idx,
                name=phase_name,
                user_inputs={},
                input_mapping={},
                skip=False,
            )
            workflow_phases.append(phase_obj)

        # Create CustomWorkflow
        workflow = CustomWorkflow(  # type: ignore[call-arg]
            name=f"Template: {template_name}",
            description=template_config["description"],
            phases=workflow_phases,
            owner_id=owner_id,
            tags=tags or [template_name],
            is_template=False,
        )

        logger.info(
            "Built workflow from template '%s' with %d phases for owner %s",
            template_name, len(workflow_phases), owner_id,
        )

        return workflow

    async def execute_template(
        self,
        template_name: str,
        task_input: Dict[str, Any],
        skip_phases: Optional[List[str]] = None,
        quality_threshold: Optional[float] = None,
        owner_id: str = "system",
        tags: Optional[List[str]] = None,
        execution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a workflow template.

        This method:
        1. Validates the template name
        2. Builds a CustomWorkflow from template
        3. Initializes progress tracking (Phase 2)
        4. Delegates to CustomWorkflowsService for execution
        5. Returns execution results with execution_id for tracking

        Args:
            template_name: Name of the template
            task_input: Input data for workflow
            skip_phases: Optional phases to skip
            quality_threshold: Optional quality threshold override
            owner_id: Owner ID for tracking
            tags: Optional tags
            execution_id: Optional execution ID (generated if not provided)

        Returns:
            Dict with execution results:
            {
                "execution_id": str,
                "workflow_id": str,
                "template": str,
                "status": "completed" | "failed",
                "phase_results": {...},
                "final_output": {...},
                "error_message": null,
                "duration_ms": float
            }

        Raises:
            ValueError: If template not found or workflow invalid
        """
        try:
            # Validate template
            self.validate_template_name(template_name)

            # Build workflow from template
            workflow = self.build_workflow_from_template(
                template_name=template_name,
                skip_phases=skip_phases,
                quality_threshold=quality_threshold,
                owner_id=owner_id,
                tags=tags,
            )

            # Initialize progress tracking (Phase 2)
            self._initialize_progress_tracking(execution_id, workflow, template_name)

            logger.info("Executing template '%s' with workflow %s", template_name, workflow.id)

            # Persist template workflow to database before execution
            # This ensures the foreign key constraint in workflow_executions is satisfied
            if not workflow.id:
                try:
                    # Try to find existing template workflow by name and owner
                    existing = await self.custom_workflows_service.get_workflow_by_name(
                        name=f"Template: {template_name}", owner_id=owner_id
                    )
                    if existing:
                        workflow.id = existing.id
                        logger.info("Using existing template workflow: %s", workflow.id)
                    else:
                        # Create new template workflow
                        workflow_save = await self.custom_workflows_service.create_workflow(
                            workflow=workflow, owner_id=owner_id
                        )
                        workflow.id = workflow_save.id
                        logger.info("Persisted template workflow: %s", workflow.id)
                except Exception as e:
                    logger.warning(
                        "[_execute_template] Could not persist/retrieve template workflow, "
                        "assigning ephemeral ID: %s", e,
                        exc_info=True,
                    )
                    # Assign an ephemeral ID and continue — execution results
                    # won't be persisted to workflow_executions but the pipeline runs.
                    workflow.id = str(uuid.uuid4())

            # Extract model parameter from task_input if provided
            selected_model = None
            if task_input and isinstance(task_input, dict):
                selected_model = task_input.get("model")
                if selected_model:
                    logger.info("[template_execution] Model selected: %s", selected_model)

            # Execute via WorkflowExecutor
            phase_results = await self.workflow_executor.execute_workflow(
                workflow=workflow,
                initial_inputs=task_input,
                execution_id=execution_id,
            )

            # Build response from phase results
            # PhaseResult uses .status ("completed"/"failed"), not .success (bool)
            all_succeeded = all(
                getattr(pr, "status", "") == "completed" for pr in phase_results.values()
            )
            result = {
                "execution_id": execution_id,
                "template": template_name,
                "workflow_id": workflow.id or "",
                "status": "completed" if all_succeeded else "failed",
                "phases": {
                    name: {
                        "success": getattr(pr, "status", "") == "completed",
                        "output": getattr(pr, "output", None),
                        "error": getattr(pr, "error", None),
                    }
                    for name, pr in phase_results.items()
                },
            }

            logger.info(
                "Template execution completed: %s (status: %s, phases: %d)",
                execution_id, result['status'], len(phase_results),
            )

            return result

        except ValueError as e:
            logger.error("[_execute_template] Template validation error: %s", str(e), exc_info=True)
            raise
        except Exception as e:
            logger.error("[_execute_template] Template execution error: %s", str(e), exc_info=True)
            raise

    def _initialize_progress_tracking(
        self,
        execution_id: Optional[str],
        workflow: CustomWorkflow,
        template_name: str,
    ) -> None:
        """
        Initialize progress tracking for workflow execution.

        Sets up progress service and optional WebSocket broadcasting.
        """
        try:
            from services.progress_broadcaster import broadcast_workflow_progress
            from services.workflow_progress_service import get_workflow_progress_service

            progress_service = get_workflow_progress_service()

            # Create progress tracking entry
            actual_execution_id = execution_id or workflow.id or "unknown"
            total_phases = len(workflow.phases) if workflow.phases else 0

            progress_service.create_progress(
                execution_id=actual_execution_id,
                workflow_id=str(workflow.id) if workflow.id else None,
                template=template_name,
                total_phases=total_phases,
            )

            # Register callback for WebSocket broadcasting
            def broadcast_callback(progress):
                """Callback to broadcast progress via WebSocket"""
                try:
                    import asyncio

                    # Schedule the broadcast in a non-blocking way
                    asyncio.create_task(broadcast_workflow_progress(actual_execution_id, progress))
                except Exception as e:
                    logger.error(
                        "[_broadcast_callback] Could not broadcast progress: %s", e, exc_info=True
                    )

            progress_service.register_callback(actual_execution_id, broadcast_callback)

            logger.debug("Initialized progress tracking for execution %s", actual_execution_id)

        except Exception as e:
            logger.error(
                "[_initialize_progress_tracking] Could not initialize progress tracking: %s", e,
                exc_info=True,
            )
            # Continue execution even if progress tracking fails

    async def get_execution_status(
        self, execution_id: str, owner_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get status of a workflow execution.

        Args:
            execution_id: Workflow execution ID
            owner_id: Owner ID for authorization

        Returns:
            Dict with execution details or None if not found
        """
        try:
            return await self.custom_workflows_service.get_workflow_execution(
                execution_id, owner_id
            )
        except Exception as e:
            logger.error(
                "[_get_execution_status] Failed to get execution status: %s", str(e), exc_info=True
            )
            return None

    async def get_execution_history(
        self,
        owner_id: str,
        template_name: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """
        Get execution history for a user, optionally filtered by template.

        Args:
            owner_id: Owner ID to filter by
            template_name: Optional template name to filter by
            limit: Number of results to return
            offset: Offset for pagination

        Returns:
            Dict with executions list and total count
        """
        try:
            executions = await self.custom_workflows_service.get_all_executions(
                owner_id=owner_id,
                limit=limit,
                offset=offset,
            )

            if template_name:
                template_prefix = f"Template: {template_name}"
                executions = [
                    execution
                    for execution in executions
                    if isinstance(execution, dict)
                    and str(execution.get("workflow_name", "")).startswith(template_prefix)
                ]

            return {
                "executions": executions,
                "total": len(executions),
            }
        except Exception as e:
            logger.error(
                "[_get_execution_history] Failed to get execution history: %s", str(e), exc_info=True
            )
            return {"executions": [], "total_count": 0}
