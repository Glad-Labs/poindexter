"""
Custom Workflow Schemas - Pydantic models for workflow building and execution

Defines:
- PhaseConfig: Configuration for a single phase in a workflow
- CustomWorkflow: Complete workflow definition with phases
- WorkflowExecutionRequest: Parameters for executing a workflow
- WorkflowExecutionResponse: Result of workflow execution
- WorkflowListResponse: List view of workflows
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class PhaseConfig(BaseModel):
    """Configuration for a single phase in a custom workflow"""

    name: str = Field(..., description="Phase name (research, draft, assess, refine, etc)")
    agent: str = Field(..., description="Agent name to use for this phase")
    description: Optional[str] = Field(None, description="Human-readable phase description")
    timeout_seconds: int = Field(300, ge=10, le=3600, description="Timeout for phase execution")
    max_retries: int = Field(3, ge=0, le=10, description="Maximum retry attempts")
    skip_on_error: bool = Field(False, description="Skip this phase if previous phase fails")
    required: bool = Field(True, description="Workflow fails if this phase fails")
    quality_threshold: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Quality score threshold (for assessment phases)"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Phase-specific metadata")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate phase name is not empty and alphanumeric with underscores"""
        if not v.strip():
            raise ValueError("Phase name cannot be empty")
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Phase name must be alphanumeric with underscores only")
        return v


class CustomWorkflow(BaseModel):
    """Complete custom workflow definition"""

    id: Optional[str] = Field(None, description="Workflow UUID (auto-generated)")
    name: str = Field(..., description="Workflow name (e.g., 'My Blog Pipeline')")
    description: str = Field(..., description="Workflow description and purpose")
    phases: List[PhaseConfig] = Field(..., description="Ordered list of phases")
    owner_id: Optional[str] = Field(None, description="User ID of workflow owner")
    created_at: Optional[datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")
    is_template: bool = Field(False, description="Whether this is a saved template")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate workflow name is not empty"""
        if not v.strip():
            raise ValueError("Workflow name cannot be empty")
        if len(v) > 255:
            raise ValueError("Workflow name must be 255 characters or less")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        """Validate description is not empty"""
        if not v.strip():
            raise ValueError("Workflow description cannot be empty")
        return v

    @field_validator("phases")
    @classmethod
    def validate_phases(cls, v: List[PhaseConfig]) -> List[PhaseConfig]:
        """Validate that at least one phase is defined"""
        if not v or len(v) == 0:
            raise ValueError("Workflow must have at least one phase")
        # Check for duplicate phase names
        phase_names = [p.name for p in v]
        if len(phase_names) != len(set(phase_names)):
            raise ValueError("Duplicate phase names in workflow")
        return v


class WorkflowExecutionRequest(BaseModel):
    """Parameters for executing a workflow"""

    workflow_id: Optional[str] = Field(None, description="ID of workflow to execute (for saved workflows)")
    phases: Optional[List[PhaseConfig]] = Field(None, description="Inline phases (for ad-hoc execution)")
    input_data: Dict[str, Any] = Field(default_factory=dict, description="Input parameters for workflow")
    skip_phases: Optional[List[str]] = Field(None, description="Phase names to skip")
    quality_threshold: Optional[float] = Field(None, ge=0.0, le=1.0, description="Override quality threshold")
    tags: Optional[List[str]] = Field(None, description="Tags for this execution")


class WorkflowExecutionResponse(BaseModel):
    """Response from starting a workflow execution"""

    workflow_id: str = Field(..., description="Workflow UUID")
    execution_id: str = Field(..., description="Execution tracking ID")
    status: str = Field(..., description="Current status (pending, running, completed, failed)")
    started_at: datetime = Field(..., description="Execution start time")
    phases: List[str] = Field(..., description="Phases in this workflow")
    progress_percent: int = Field(0, ge=0, le=100, description="Execution progress percentage")


class WorkflowListResponse(BaseModel):
    """Single workflow in list view"""

    id: str = Field(..., description="Workflow UUID")
    name: str = Field(..., description="Workflow name")
    description: str = Field(..., description="Workflow description")
    phase_count: int = Field(..., description="Number of phases")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")
    is_template: bool = Field(False, description="Whether this is a template")


class WorkflowListPageResponse(BaseModel):
    """Paginated list of workflows"""

    workflows: List[WorkflowListResponse] = Field(..., description="Workflows in page")
    total_count: int = Field(..., description="Total workflow count")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Page size")
    has_next: bool = Field(..., description="Whether more pages exist")


class WorkflowValidationResult(BaseModel):
    """Result of workflow validation"""

    valid: bool = Field(..., description="Whether workflow is valid")
    errors: List[str] = Field(default_factory=list, description="Validation errors")
    warnings: List[str] = Field(default_factory=list, description="Validation warnings")


class AvailablePhase(BaseModel):
    """Information about an available phase for workflow building"""

    name: str = Field(..., description="Phase name (e.g., 'research', 'draft')")
    description: str = Field(..., description="Human-readable description")
    category: str = Field(..., description="Phase category (e.g., 'content', 'analysis')")
    default_timeout_seconds: int = Field(..., description="Default timeout if not specified")
    compatible_agents: List[str] = Field(..., description="Agents that can handle this phase")
    capabilities: List[str] = Field(..., description="Capabilities provided (e.g., web_search)")
    default_retries: int = Field(..., description="Recommended retry count")
    version: str = Field("1.0", description="Phase handler version")


class AvailablePhasesResponse(BaseModel):
    """List of available phases that can be used in workflows"""

    phases: List[AvailablePhase] = Field(..., description="Available phases")
    total_count: int = Field(..., description="Total number of phases")
