"""
Template Execution Service - Maps workflow templates to CustomWorkflow and executor

Provides:
- Template name validation and mapping
- CustomWorkflow construction for templates
- Integration with CustomWorkflowsService for actual execution
- Phase configuration with proper defaults
- Progress tracking and WebSocket integration (Phase 2)
"""

import logging
from typing import Any, Dict, List, Optional

from schemas.custom_workflow_schemas import CustomWorkflow, WorkflowPhase

logger = logging.getLogger(__name__)


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

    def __init__(self, custom_workflows_service):
        """
        Initialize with CustomWorkflowsService for actual execution.

        Args:
            custom_workflows_service: Service for workflow execution and persistence
        """
        self.custom_workflows_service = custom_workflows_service
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
            phase_obj = WorkflowPhase(
                index=idx,
                name=phase_name,
                user_inputs={},
                input_mapping={},
                skip=False,
            )
            workflow_phases.append(phase_obj)

        # Create CustomWorkflow
        workflow = CustomWorkflow(
            name=f"Template: {template_name}",
            description=template_config["description"],
            phases=workflow_phases,
            owner_id=owner_id,
            tags=tags or [template_name],
            is_template=False,
        )

        logger.info(
            f"Built workflow from template '{template_name}' "
            f"with {len(workflow_phases)} phases for owner {owner_id}"
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

            logger.info(f"Executing template '{template_name}' with workflow {workflow.id}")

            # Execute via CustomWorkflowsService
            result = await self.custom_workflows_service.execute_workflow(
                workflow=workflow,
                initial_inputs=task_input,
                execution_id=execution_id,
            )

            # Enrich result with template information
            result["template"] = template_name
            result["workflow_id"] = workflow.id or result.get("workflow_id", "")

            logger.info(
                f"Template execution completed: {result.get('execution_id')} "
                f"(status: {result.get('status')})"
            )

            return result

        except ValueError as e:
            logger.error(f"Template validation error: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Template execution error: {str(e)}", exc_info=True)
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
            from routes.websocket_routes import broadcast_workflow_progress
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
                    logger.debug(f"Could not broadcast progress: {e}")

            progress_service.register_callback(actual_execution_id, broadcast_callback)

            logger.debug(f"Initialized progress tracking for execution {actual_execution_id}")

        except Exception as e:
            logger.warning(f"Could not initialize progress tracking: {e}")
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
            logger.error(f"Failed to get execution status: {str(e)}")
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
            return await self.custom_workflows_service.get_workflow_executions(
                owner_id=owner_id,
                limit=limit,
                offset=offset,
            )
        except Exception as e:
            logger.error(f"Failed to get execution history: {str(e)}")
            return {"executions": [], "total_count": 0}
