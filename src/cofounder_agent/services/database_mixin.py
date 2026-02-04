"""
Database Service Base Mixin

Shared utilities and conversion methods for all database modules.
Provides common functionality like row-to-dict conversion and error handling.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict
from uuid import UUID

logger = logging.getLogger(__name__)


class DatabaseServiceMixin:
    """Shared methods and utilities for database service modules."""

    @staticmethod
    def _convert_row_to_dict(row: Any) -> Dict[str, Any]:
        """
        Convert asyncpg Record to dict with proper type handling.

        Handles:
        - UUID → string
        - Decimal → float
        - JSONB → parsed dict/list
        - Timestamps → ISO format strings
        """
        import json
        from decimal import Decimal

        if hasattr(row, "keys"):
            data = dict(row)
        else:
            data = row if isinstance(row, dict) else {}

        # Convert UUID to string
        if "id" in data and data["id"]:
            if isinstance(data["id"], UUID):
                data["id"] = str(data["id"])

        # Convert Decimal to float (for cost/price fields)
        for key, value in list(data.items()):
            if isinstance(value, Decimal):
                data[key] = float(value)

        # Handle JSONB fields
        for key in [
            "tags",
            "task_metadata",
            "result",
            "progress",
            "metadata",
            "context",
            "provider_data",
            "business_state",
        ]:
            if key in data:
                if isinstance(data[key], str):
                    try:
                        data[key] = json.loads(data[key])
                    except (json.JSONDecodeError, TypeError):
                        data[key] = {} if key != "tags" else []

        # Convert timestamps to ISO strings
        for key in [
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "last_used",
            "evaluation_timestamp",
            "refinement_timestamp",
            "modified_at",
        ]:
            if key in data and data[key]:
                if hasattr(data[key], "isoformat"):
                    data[key] = data[key].isoformat()

        return data
