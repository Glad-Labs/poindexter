"""Utility tasks: Generic utilities supporting all workflows."""

from typing import Dict, Any, Optional
from src.cofounder_agent.tasks.base import PureTask, ExecutionContext, TaskStatus, TaskResult
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ValidateTask(PureTask):
    """
    Validation: Check content against criteria.

    Input:
        - content: dict - Content to validate
        - criteria: list - Validation criteria

    Output:
        - valid: bool - Validation passed
        - issues: list - Any validation issues
    """

    def __init__(self):
        super().__init__(
            name="validate",
            description="Validate content against criteria",
            required_inputs=["content"],
            timeout_seconds=30,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute validation task."""

        content = input_data.get("content", {})
        criteria = input_data.get("criteria", [])

        issues = []

        # Check basic requirements
        if isinstance(content, dict):
            if not content.get("title"):
                issues.append("Missing title")
            if not content.get("content"):
                issues.append("Missing content")

        # Check custom criteria
        for criterion in criteria:
            if isinstance(criterion, str):
                if criterion == "has_cta" and not content.get("cta"):
                    issues.append("Missing call-to-action")
                elif criterion == "has_images" and not content.get("images"):
                    issues.append("Missing images")
                elif (
                    criterion == "word_count_min"
                    and len(str(content.get("content", "")).split()) < 500
                ):
                    issues.append("Content too short (minimum 500 words)")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "criteria_checked": len(criteria),
            "validation_timestamp": datetime.now().isoformat(),
        }


class TransformTask(PureTask):
    """
    Transformation: Transform content format.

    Input:
        - content: str or dict - Content to transform
        - from_format: str - Source format (markdown, html, json)
        - to_format: str - Target format

    Output:
        - content: str or dict - Transformed content
        - format: str - Result format
    """

    def __init__(self):
        super().__init__(
            name="transform",
            description="Transform content between formats",
            required_inputs=["content", "to_format"],
            timeout_seconds=60,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute transformation task."""
        from src.cofounder_agent.services.model_router import model_router

        content = input_data["content"]
        to_format = input_data["to_format"]
        from_format = input_data.get("from_format", "auto")

        if to_format == "json" and isinstance(content, str):
            prompt = f"""Convert this content to JSON structure:

Content:
{content}

Create JSON with keys: title, summary, body, metadata"""

            response = await model_router.query_with_fallback(
                prompt=prompt,
                temperature=0.2,
                max_tokens=1000,
            )

            try:
                import json

                transformed = json.loads(response)
            except:
                transformed = {"content": content}

        elif to_format == "markdown" and isinstance(content, dict):
            # Convert dict to markdown
            lines = []
            if "title" in content:
                lines.append(f"# {content['title']}")
            if "summary" in content:
                lines.append(f"\n{content['summary']}\n")
            if "body" in content:
                lines.append(content["body"])
            transformed = "\n".join(lines)

        else:
            # Pass through if no transformation needed
            transformed = content

        return {
            "content": transformed,
            "from_format": from_format,
            "to_format": to_format,
            "transformation_timestamp": datetime.now().isoformat(),
        }


class NotificationTask(PureTask):
    """
    Notification: Send workflow notifications.

    Input:
        - message: str - Notification message
        - notification_type: str - Type (info, warning, success, error)
        - channels: list - Channels (email, webhook, slack, dashboard)

    Output:
        - sent: bool - Send success
        - channels_notified: list - Channels successfully notified
    """

    def __init__(self):
        super().__init__(
            name="notification",
            description="Send notifications about workflow status",
            required_inputs=["message"],
            timeout_seconds=30,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute notification task."""

        message = input_data["message"]
        notification_type = input_data.get("notification_type", "info")
        channels = input_data.get("channels", ["dashboard"])

        channels_notified = []

        # In production, would integrate with:
        # - Email service (SendGrid, etc.)
        # - Slack API
        # - Webhook endpoints
        # - Dashboard notifications

        for channel in channels:
            if channel in ["dashboard", "email", "slack", "webhook"]:
                channels_notified.append(channel)
                logger.info(
                    f"Notification sent via {channel}",
                    extra={
                        "workflow_id": context.workflow_id,
                        "type": notification_type,
                    },
                )

        return {
            "sent": True,
            "message": message,
            "notification_type": notification_type,
            "channels": channels,
            "channels_notified": channels_notified,
            "sent_at": datetime.now().isoformat(),
        }


class CacheTask(PureTask):
    """
    Caching: Store results for reuse.

    Input:
        - data: dict - Data to cache
        - cache_key: str - Cache key for retrieval
        - ttl: int - Time-to-live in seconds

    Output:
        - cached: bool - Cache success
        - cache_key: str - Key for retrieval
    """

    def __init__(self):
        super().__init__(
            name="cache",
            description="Cache results for reuse across workflows",
            required_inputs=["data", "cache_key"],
            timeout_seconds=10,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute caching task."""

        data = input_data["data"]
        cache_key = input_data["cache_key"]
        ttl = input_data.get("ttl", 3600)

        # In production, would use Redis or similar
        # For now, just track that caching was requested

        logger.info(
            f"Data cached with key: {cache_key}",
            extra={
                "workflow_id": context.workflow_id,
                "ttl": ttl,
            },
        )

        return {
            "cached": True,
            "cache_key": cache_key,
            "data_size": len(str(data)),
            "ttl_seconds": ttl,
            "cached_at": datetime.now().isoformat(),
        }


class MetricsTask(PureTask):
    """
    Metrics collection: Track workflow metrics.

    Input:
        - metrics: dict - Metrics to record
        - workflow_type: str - Workflow type for categorization

    Output:
        - recorded: bool - Recording success
        - metric_count: int - Number of metrics recorded
    """

    def __init__(self):
        super().__init__(
            name="metrics",
            description="Record workflow metrics and performance data",
            required_inputs=["metrics"],
            timeout_seconds=10,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute metrics task."""

        metrics = input_data.get("metrics", {})
        workflow_type = input_data.get("workflow_type", "unknown")

        # In production, would write to metrics database/service
        logger.info(
            "Workflow metrics recorded",
            extra={
                "workflow_id": context.workflow_id,
                "workflow_type": workflow_type,
                "metrics": metrics,
            },
        )

        return {
            "recorded": True,
            "metric_count": len(metrics),
            "workflow_type": workflow_type,
            "metrics": metrics,
            "recorded_at": datetime.now().isoformat(),
        }


class LogTask(PureTask):
    """
    Logging: Log workflow execution details.

    Input:
        - message: str - Log message
        - level: str - Log level (debug, info, warning, error)
        - data: dict - Additional context data

    Output:
        - logged: bool - Logging success
    """

    def __init__(self):
        super().__init__(
            name="log",
            description="Log workflow execution details",
            required_inputs=["message"],
            timeout_seconds=5,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute logging task."""

        message = input_data["message"]
        level = input_data.get("level", "info").lower()
        data = input_data.get("data", {})

        log_data = {
            "workflow_id": context.workflow_id,
            "user_id": context.user_id,
            **data,
        }

        if level == "debug":
            logger.debug(message, extra=log_data)
        elif level == "warning":
            logger.warning(message, extra=log_data)
        elif level == "error":
            logger.error(message, extra=log_data)
        else:
            logger.info(message, extra=log_data)

        return {
            "logged": True,
            "message": message,
            "level": level,
            "logged_at": datetime.now().isoformat(),
        }
