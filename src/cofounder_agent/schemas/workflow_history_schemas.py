"""Workflow History and Performance Models

Consolidated schemas for workflow execution tracking and analytics.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowExecutionDetail(BaseModel):
    """Detailed workflow execution information."""

    id: str
    workflow_id: str
    workflow_type: str
    user_id: str
    status: str
    input_data: Dict[str, Any]
    output_data: Optional[Dict[str, Any]] = None
    task_results: List[Dict[str, Any]]
    error_message: Optional[str] = None
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: Optional[float] = None
    execution_metadata: Dict[str, Any]
    created_at: str
    updated_at: str


class WorkflowHistoryResponse(BaseModel):
    """Response containing workflow execution history."""

    executions: List[WorkflowExecutionDetail]
    total: int
    limit: int
    offset: int
    status_filter: Optional[str] = None


class WorkflowStatistics(BaseModel):
    """Workflow execution statistics."""

    user_id: str
    period_days: int
    total_executions: int
    completed_executions: int
    failed_executions: int
    success_rate_percent: float
    average_duration_seconds: float
    first_execution: Optional[str]
    last_execution: Optional[str]
    workflows: List[Dict[str, Any]]
    most_common_workflow: Optional[str]


class PerformanceMetrics(BaseModel):
    """Performance metrics and optimization suggestions."""

    user_id: str
    workflow_type: Optional[str]
    period_days: int
    execution_time_distribution: List[Dict[str, Any]]
    error_patterns: List[Dict[str, Any]]
    optimization_tips: List[str]
