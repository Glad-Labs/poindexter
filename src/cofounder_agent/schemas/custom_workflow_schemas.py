"""
Custom Workflow Schemas - Pydantic models for workflow building and execution

Defines:
- PhaseConfig: Configuration for a single phase in a workflow
- WorkflowPhase: Flexible phase with index, user inputs, and input mapping
- CustomWorkflow: Complete workflow definition with phases
- WorkflowExecutionRequest: Parameters for executing a workflow
- WorkflowExecutionResponse: Result of workflow execution
- PhaseResult: Execution result with input tracing
- WorkflowListResponse: List view of workflows
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class PhaseConfig(BaseModel):
    """Configuration for a single phase in a custom workflow"""

    name: str = Field(..., description="Phase name (research, draft, assess, refine, etc)")
    agent: str = Field(..., description="Agent name to use for this phase")
    description: str | None = Field(None, description="Human-readable phase description")
    timeout_seconds: int = Field(300, ge=10, le=3600, description="Timeout for phase execution")
    max_retries: int = Field(3, ge=0, le=10, description="Maximum retry attempts")
    skip_on_error: bool = Field(False, description="Skip this phase if previous phase fails")
    required: bool = Field(True, description="Workflow fails if this phase fails")
    quality_threshold: float | None = Field(
        None, ge=0.0, le=1.0, description="Quality score threshold (for assessment phases)"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Phase-specific metadata")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate phase name is not empty and alphanumeric with underscores"""
        if not v.strip():
            raise ValueError("Phase name cannot be empty")
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Phase name must be alphanumeric with underscores only")
        return v


class WorkflowPhase(BaseModel):
    """Flexible phase definition supporting any order and input mapping"""

    index: int = Field(..., description="Phase execution order (0-based)")
    name: str = Field(..., description="Phase name from registry (research, draft, assess, etc)")
    user_inputs: dict[str, Any] = Field(
        default_factory=dict, description="User-provided input values that override defaults"
    )
    model_overrides: str | None = Field(
        None, description="Optional override of model/agent for this phase"
    )
    input_mapping: dict[str, str] = Field(
        default_factory=dict,
        description="Map previous phase output field to this phase input field: {target_key: source_phase.source_field}",
    )
    skip: bool = Field(False, description="Skip this phase in execution")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate phase name is alphanumeric with underscores"""
        if not v.strip():
            raise ValueError("Phase name cannot be empty")
        if not all(c.isalnum() or c == "_" for c in v):
            raise ValueError("Phase name must be alphanumeric with underscores only")
        return v


class InputTrace(BaseModel):
    """Trace of where an input value came from"""

    source_phase: str | None = Field(
        None, description="Previous phase name that produced this input"
    )
    source_field: str | None = Field(None, description="Output field from source phase")
    user_provided: bool = Field(False, description="Whether user explicitly provided this value")
    auto_mapped: bool = Field(False, description="Whether this was auto-mapped by the system")


class PhaseResult(BaseModel):
    """Result from executing a single phase"""

    status: str = Field(..., description="Phase status: completed, failed, skipped")
    output: dict[str, Any] = Field(default_factory=dict, description="Output data from phase")
    error: str | None = Field(None, description="Error message if phase failed")
    execution_time_ms: float = Field(0.0, description="Phase execution time in milliseconds")
    model_used: str | None = Field(None, description="Model/agent that executed the phase")
    tokens_used: int | None = Field(None, description="Number of tokens used (if applicable)")
    input_trace: dict[str, InputTrace] = Field(
        default_factory=dict,
        description="Trace of where each input came from: {input_key: InputTrace}",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional phase metadata")


class CustomWorkflow(BaseModel):
    """Complete custom workflow definition"""

    id: str | UUID | None = Field(None, description="Workflow UUID (auto-generated)")
    name: str = Field(..., description="Workflow name (e.g., 'My Blog Pipeline')")
    description: str = Field(..., description="Workflow description and purpose")

    # Support both old (phases: List[PhaseConfig]) and new (phases: List[WorkflowPhase]) formats
    phases: list[Any] = Field(..., description="Ordered list of phases (flexible format)")

    owner_id: str | None = Field(None, description="User ID of workflow owner")
    created_at: datetime | None = Field(None, description="Creation timestamp")
    updated_at: datetime | None = Field(None, description="Last update timestamp")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    is_template: bool = Field(False, description="Whether this is a saved template")

    # Store phase definitions at save time for self-documenting workflows
    phase_definitions: dict[str, dict[str, Any]] | None = Field(
        None, description="Snapshot of phase definitions at save time (from PhaseRegistry)"
    )

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
    def validate_phases(cls, v: list[Any]) -> list[Any]:
        """Validate that at least one phase is defined"""
        if not v or len(v) == 0:
            raise ValueError("Workflow must have at least one phase")

        # Check phase names are unique (if they have 'name' field)
        phase_names = []
        for p in v:
            if hasattr(p, "name"):
                phase_names.append(p.name)
            elif isinstance(p, dict) and "name" in p:
                phase_names.append(p["name"])

        if phase_names and len(phase_names) != len(set(phase_names)):
            raise ValueError("Duplicate phase names in workflow")
        return v


class WorkflowExecutionRequest(BaseModel):
    """Parameters for executing a workflow"""

    workflow_id: str | None = Field(
        None, description="ID of workflow to execute (for saved workflows)"
    )
    phases: list[PhaseConfig] | None = Field(
        None, description="Inline phases (for ad-hoc execution)"
    )
    input_data: dict[str, Any] = Field(
        default_factory=dict, description="Input parameters for workflow"
    )
    skip_phases: list[str] | None = Field(None, description="Phase names to skip")
    quality_threshold: float | None = Field(
        None, ge=0.0, le=1.0, description="Override quality threshold"
    )
    tags: list[str] | None = Field(None, description="Tags for this execution")


class WorkflowExecutionResponse(BaseModel):
    """Response from starting a workflow execution"""

    workflow_id: str = Field(..., description="Workflow UUID")
    execution_id: str = Field(..., description="Execution tracking ID")
    status: str = Field(..., description="Current status (pending, running, completed, failed)")
    started_at: datetime = Field(..., description="Execution start time")
    phases: list[str] = Field(..., description="Phases in this workflow")
    progress_percent: int = Field(0, ge=0, le=100, description="Execution progress percentage")
    phase_results: dict[str, PhaseResult] = Field(
        default_factory=dict, description="Results from each phase execution with input tracing"
    )
    final_output: Any | None = Field(None, description="Final output from last phase")
    error_message: str | None = Field(None, description="Error message if execution failed")


class WorkflowListResponse(BaseModel):
    """Single workflow in list view"""

    id: str = Field(..., description="Workflow UUID")
    name: str = Field(..., description="Workflow name")
    description: str = Field(..., description="Workflow description")
    phase_count: int = Field(..., description="Number of phases")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    is_template: bool = Field(False, description="Whether this is a template")


class WorkflowListPageResponse(BaseModel):
    """Paginated list of workflows"""

    workflows: list[WorkflowListResponse] = Field(..., description="Workflows in page")
    total_count: int = Field(..., description="Total workflow count")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Page size")
    has_next: bool = Field(..., description="Whether more pages exist")


class WorkflowValidationResult(BaseModel):
    """Result of workflow validation"""

    valid: bool = Field(..., description="Whether workflow is valid")
    errors: list[str] = Field(default_factory=list, description="Validation errors")
    warnings: list[str] = Field(default_factory=list, description="Validation warnings")


class PhaseInputField(BaseModel):
    """UI/runtime input field definition for a workflow phase."""

    key: str = Field(..., description="Unique key for this input value")
    label: str = Field(..., description="Human-readable field label")
    input_type: str = Field(
        "text", description="Input control type: text, textarea, number, select, boolean"
    )
    required: bool = Field(False, description="Whether this input is required")
    placeholder: str | None = Field(None, description="Optional placeholder text")
    default_value: Any | None = Field(None, description="Optional default value")
    options: list[str] = Field(
        default_factory=list, description="Select options when input_type=select"
    )


class AvailablePhase(BaseModel):
    """Information about an available phase for workflow building"""

    name: str = Field(..., description="Phase name (e.g., 'research', 'draft')")
    description: str = Field(..., description="Human-readable description")
    category: str = Field(..., description="Phase category (e.g., 'content', 'analysis')")
    default_timeout_seconds: int = Field(..., description="Default timeout if not specified")
    compatible_agents: list[str] = Field(..., description="Agents that can handle this phase")
    capabilities: list[str] = Field(..., description="Capabilities provided (e.g., web_search)")
    default_retries: int = Field(..., description="Recommended retry count")
    supports_model_selection: bool = Field(
        True, description="Whether per-phase model selection is supported"
    )
    input_fields: list[PhaseInputField] = Field(
        default_factory=list,
        description="Phase-specific input fields to collect from user",
    )
    version: str = Field("1.0", description="Phase handler version")


class AvailablePhasesResponse(BaseModel):
    """List of available phases that can be used in workflows"""

    phases: list[AvailablePhase] = Field(..., description="Available phases")
    total_count: int = Field(..., description="Total number of phases")
