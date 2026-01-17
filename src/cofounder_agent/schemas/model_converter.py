"""
Database Model Converters

Utility functions to convert asyncpg Row objects to Pydantic models.
Provides type-safe conversion with automatic timestamp handling.
"""

import json
from typing import Any, Optional, Dict, Type, TypeVar, List
from datetime import datetime
from uuid import UUID

from schemas.database_response_models import (
    UserResponse,
    OAuthAccountResponse,
    TaskResponse,
    TaskCountsResponse,
    PostResponse,
    CategoryResponse,
    TagResponse,
    AuthorResponse,
    LogResponse,
    MetricsResponse,
    FinancialEntryResponse,
    FinancialSummaryResponse,
    CostLogResponse,
    TaskCostBreakdownResponse,
    QualityEvaluationResponse,
    QualityImprovementLogResponse,
    AgentStatusResponse,
    OrchestratorTrainingDataResponse,
    SettingResponse,
    ErrorResponse,
)

T = TypeVar("T")


class ModelConverter:
    """Converts asyncpg Row objects to Pydantic models."""

    @staticmethod
    def _normalize_row_data(row: Any) -> Dict[str, Any]:
        """Convert asyncpg Row to dict with proper type handling."""
        if hasattr(row, "keys"):
            data = dict(row)
        else:
            data = row if isinstance(row, dict) else {}

        # Convert UUID to string
        for key in list(data.keys()):
            if isinstance(data[key], UUID):
                data[key] = str(data[key])

        # Handle JSONB fields
        json_fields = [
            "metadata",
            "task_metadata",
            "result",
            "progress",
            "context",
            "provider_data",
            "business_state",
            "suggestions",
            "cost_breakdown",
        ]
        for key in json_fields:
            if key in data and data[key] is not None:
                if isinstance(data[key], str):
                    try:
                        data[key] = json.loads(data[key])
                    except (json.JSONDecodeError, TypeError):
                        pass  # Keep as string if not valid JSON

        # Handle list/array fields
        array_fields = ["tag_ids", "tags"]
        for key in array_fields:
            if key in data and data[key] is not None:
                if isinstance(data[key], str):
                    try:
                        data[key] = json.loads(data[key])
                    except (json.JSONDecodeError, TypeError):
                        data[key] = [data[key]] if data[key] else None
                # Convert UUID objects within arrays to strings
                elif isinstance(data[key], (list, tuple)):
                    data[key] = [
                        str(item) if isinstance(item, UUID) else item for item in data[key]
                    ]

        return data

    @staticmethod
    def to_user_response(row: Any) -> UserResponse:
        """Convert row to UserResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return UserResponse(**data)

    @staticmethod
    def to_oauth_account_response(row: Any) -> OAuthAccountResponse:
        """Convert row to OAuthAccountResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return OAuthAccountResponse(**data)

    @staticmethod
    def to_task_response(row: Any) -> TaskResponse:
        """Convert row to TaskResponse model."""
        data = ModelConverter._normalize_row_data(row)

        # CRITICAL: Convert seo_keywords back to JSON string if it was parsed as list
        # TaskResponse expects seo_keywords as Optional[str], not List[str]
        if "seo_keywords" in data and isinstance(data["seo_keywords"], list):
            data["seo_keywords"] = json.dumps(data["seo_keywords"])

        # CRITICAL: Map task_id (UUID string) to id for API response
        # The database has an integer 'id' column (SERIAL PK) but we use task_id (UUID string) as the logical identifier
        # The TaskResponse schema expects 'id' to be a string (the UUID)
        if "task_id" in data and data["task_id"]:
            data["id"] = data["task_id"]  # Always use task_id as the public-facing id

        # Handle task_name mapping to title
        if "task_name" in data:
            if "title" not in data or data["title"] is None:
                data["title"] = data["task_name"]
            # Keep task_name for backward compatibility
            data["task_name"] = data["task_name"]
        elif "title" in data:
            data["task_name"] = data["title"]

        # Fallback: Check task_metadata for task_name if still missing
        if (
            ("task_name" not in data or data["task_name"] is None)
            and "task_metadata" in data
            and isinstance(data["task_metadata"], dict)
        ):
            if "task_name" in data["task_metadata"]:
                data["task_name"] = data["task_metadata"]["task_name"]
                if "title" not in data or data["title"] is None:
                    data["title"] = data["task_name"]

        # IMPORTANT: Merge normalized columns back into task_metadata for UI compatibility
        # The frontend expects task_metadata to contain all content fields
        if "task_metadata" not in data or data["task_metadata"] is None:
            data["task_metadata"] = {}

        normalized_fields = [
            "content",
            "excerpt",
            "featured_image_url",
            "featured_image_data",
            "qa_feedback",
            "quality_score",
            "seo_title",
            "seo_description",
            "seo_keywords",
            "stage",
            "percentage",
            "message",
        ]

        for field in normalized_fields:
            if field in data and data[field] is not None:
                # Merge normalized column into task_metadata for UI
                data["task_metadata"][field] = data[field]

        # Extract constraint_compliance from task_metadata to top level for API response
        if "constraint_compliance" not in data or data.get("constraint_compliance") is None:
            if data.get("task_metadata") and isinstance(data["task_metadata"], dict):
                if "constraint_compliance" in data["task_metadata"]:
                    data["constraint_compliance"] = data["task_metadata"]["constraint_compliance"]

        return TaskResponse(**data)

    @staticmethod
    def to_post_response(row: Any) -> PostResponse:
        """Convert row to PostResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return PostResponse(**data)

    @staticmethod
    def to_category_response(row: Any) -> CategoryResponse:
        """Convert row to CategoryResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return CategoryResponse(**data)

    @staticmethod
    def to_tag_response(row: Any) -> TagResponse:
        """Convert row to TagResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return TagResponse(**data)

    @staticmethod
    def to_author_response(row: Any) -> AuthorResponse:
        """Convert row to AuthorResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return AuthorResponse(**data)

    @staticmethod
    def to_log_response(row: Any) -> LogResponse:
        """Convert row to LogResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return LogResponse(**data)

    @staticmethod
    def to_financial_entry_response(row: Any) -> FinancialEntryResponse:
        """Convert row to FinancialEntryResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return FinancialEntryResponse(**data)

    @staticmethod
    def to_cost_log_response(row: Any) -> CostLogResponse:
        """Convert row to CostLogResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return CostLogResponse(**data)

    @staticmethod
    def to_quality_evaluation_response(row: Any) -> QualityEvaluationResponse:
        """Convert row to QualityEvaluationResponse model."""
        data = ModelConverter._normalize_row_data(row)
        # Convert id to string if it's an integer
        if "id" in data and isinstance(data["id"], int):
            data["id"] = str(data["id"])
        # Convert content_id to string if it's an integer
        if "content_id" in data and isinstance(data["content_id"], int):
            data["content_id"] = str(data["content_id"])
        # Convert task_id to string if it's an integer
        if "task_id" in data and isinstance(data["task_id"], int):
            data["task_id"] = str(data["task_id"])
        return QualityEvaluationResponse(**data)

    @staticmethod
    def to_quality_improvement_log_response(row: Any) -> QualityImprovementLogResponse:
        """Convert row to QualityImprovementLogResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return QualityImprovementLogResponse(**data)

    @staticmethod
    def to_agent_status_response(row: Any) -> AgentStatusResponse:
        """Convert row to AgentStatusResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return AgentStatusResponse(**data)

    @staticmethod
    def to_orchestrator_training_data_response(row: Any) -> OrchestratorTrainingDataResponse:
        """Convert row to OrchestratorTrainingDataResponse model."""
        data = ModelConverter._normalize_row_data(row)
        # Convert id to string if it's an integer (database returns int, schema expects str)
        if "id" in data and isinstance(data["id"], int):
            data["id"] = str(data["id"])
        # Convert execution_id to string if it's an integer
        if "execution_id" in data and isinstance(data["execution_id"], int):
            data["execution_id"] = str(data["execution_id"])
        return OrchestratorTrainingDataResponse(**data)

    @staticmethod
    def to_setting_response(row: Any) -> SettingResponse:
        """Convert row to SettingResponse model."""
        data = ModelConverter._normalize_row_data(row)
        return SettingResponse(**data)

    @staticmethod
    def to_task_counts_response(counts_dict: Dict[str, int]) -> TaskCountsResponse:
        """Convert counts dict to TaskCountsResponse model."""
        return TaskCountsResponse(**counts_dict)

    @staticmethod
    def to_metrics_response(metrics_dict: Dict[str, Any]) -> MetricsResponse:
        """Convert metrics dict to MetricsResponse model."""
        return MetricsResponse(**metrics_dict)

    @staticmethod
    def to_financial_summary_response(summary_dict: Dict[str, Any]) -> FinancialSummaryResponse:
        """Convert summary dict to FinancialSummaryResponse model."""
        return FinancialSummaryResponse(**summary_dict)

    @staticmethod
    def to_list(rows: List[Any], model_class: Type[T]) -> List[T]:
        """Convert list of rows to list of models."""
        if not rows:
            return []

        converter_map = {
            UserResponse: ModelConverter.to_user_response,
            OAuthAccountResponse: ModelConverter.to_oauth_account_response,
            TaskResponse: ModelConverter.to_task_response,
            PostResponse: ModelConverter.to_post_response,
            CategoryResponse: ModelConverter.to_category_response,
            TagResponse: ModelConverter.to_tag_response,
            AuthorResponse: ModelConverter.to_author_response,
            LogResponse: ModelConverter.to_log_response,
            FinancialEntryResponse: ModelConverter.to_financial_entry_response,
            CostLogResponse: ModelConverter.to_cost_log_response,
            QualityEvaluationResponse: ModelConverter.to_quality_evaluation_response,
            QualityImprovementLogResponse: ModelConverter.to_quality_improvement_log_response,
            AgentStatusResponse: ModelConverter.to_agent_status_response,
            OrchestratorTrainingDataResponse: ModelConverter.to_orchestrator_training_data_response,
            SettingResponse: ModelConverter.to_setting_response,
        }

        converter = converter_map.get(model_class)
        if not converter:
            raise ValueError(f"No converter found for {model_class}")

        return [converter(row) for row in rows]

    @staticmethod
    def to_dict(model: Any) -> Dict[str, Any]:
        """Convert Pydantic model to dict."""
        if hasattr(model, "model_dump"):
            # Pydantic v2
            return model.model_dump()
        elif hasattr(model, "dict"):
            # Pydantic v1
            return model.dict()
        else:
            return dict(model) if isinstance(model, dict) else {}

    @staticmethod
    def task_response_to_unified(task_response: TaskResponse) -> Dict[str, Any]:
        """Convert TaskResponse to UnifiedTaskResponse-compatible dict.

        Handles conversion of seo_keywords from JSON string to list.
        """
        data = task_response.model_dump()

        # Convert seo_keywords from JSON string to list for UnifiedTaskResponse
        if "seo_keywords" in data and isinstance(data["seo_keywords"], str):
            try:
                data["seo_keywords"] = json.loads(data["seo_keywords"])
            except (json.JSONDecodeError, TypeError):
                # If not valid JSON, wrap in list
                data["seo_keywords"] = [data["seo_keywords"]] if data["seo_keywords"] else None

        return data


# ============================================================================
# TYPE ALIASES FOR COMMON PATTERNS
# ============================================================================

UsersResponseList = List[UserResponse]
TasksResponseList = List[TaskResponse]
PostsResponseList = List[PostResponse]
LogsResponseList = List[LogResponse]
CostLogsResponseList = List[CostLogResponse]
SettingsResponseList = List[SettingResponse]
