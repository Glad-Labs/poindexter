"""
JSON parsing utilities.

Provides a single defensive parse_json_field() helper that replaces the
isinstance/json.loads copy-paste pattern scattered across 15+ route and
service files (issue #634).
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_json_field(
    value: Any,
    default: Any = None,
    field_name: str = "field",
    record_id: Any = None,
) -> Any:
    """
    Parse a database field that may be stored as a JSON string or already
    deserialized as a dict/list.

    Handles:
    - ``None`` → returns ``default``
    - Empty / whitespace-only string → returns ``default``
    - Already-parsed ``dict`` or ``list`` → returned as-is
    - JSON string → parsed and returned
    - Unparseable string → logs a warning and returns ``default``

    Args:
        value:      The raw value from the database row.
        default:    Value to return when ``value`` is absent or unparseable.
                    Defaults to ``None``; pass ``{}`` or ``[]`` as needed.
        field_name: Name used in the warning log for context (e.g. "task_metadata").
        record_id:  Identifier of the parent record, included in warning logs.

    Returns:
        The parsed value, or ``default`` on any failure.

    Examples::

        task_metadata = parse_json_field(row["task_metadata"], default={},
                                         field_name="task_metadata",
                                         record_id=task_id)

        seo_keywords = parse_json_field(row["seo_keywords"], default=[],
                                        field_name="seo_keywords",
                                        record_id=task_id)
    """
    if value is None:
        return default

    # Already deserialized — return as-is (handles both dict and list fields)
    if isinstance(value, (dict, list)):
        return value

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "[parse_json_field] %s is not valid JSON for record %s — defaulting to %r",
                field_name,
                record_id,
                default,
            )
            return default

    # Unexpected type (e.g. bytes) — fall back to default
    logger.warning(
        "[parse_json_field] %s has unexpected type %s for record %s — defaulting to %r",
        field_name,
        type(value).__name__,
        record_id,
        default,
    )
    return default
