"""
Intervention Handler Service

Handles scenarios requiring human intervention or oversight.
Replaces the separate cloud-functions/intervene-trigger with integrated solution.

Features:
- Automatic detection of tasks requiring human review
- Configurable intervention thresholds
- Multi-channel notifications (Pub/Sub, logs, future: email/Slack)
- Cost-effective: No separate cloud function billing
"""

import structlog
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

logger = structlog.get_logger(__name__)


class InterventionReason(str, Enum):
    """Reasons for triggering intervention."""
    LOW_CONFIDENCE = "low_confidence"
    HIGH_IMPACT = "high_impact"
    CRITICAL_PRIORITY = "critical_priority"
    ERROR_THRESHOLD = "error_threshold"
    BUDGET_EXCEEDED = "budget_exceeded"
    MANUAL_REVIEW = "manual_review"
    COMPLIANCE_CHECK = "compliance_check"


class InterventionLevel(str, Enum):
    """Severity levels for interventions."""
    INFO = "info"          # FYI only, no action required
    WARNING = "warning"    # Review recommended
    URGENT = "urgent"      # Immediate review needed
    CRITICAL = "critical"  # System paused, action required


class InterventionHandler:
    """
    Handles detection and processing of scenarios requiring human intervention.
    
    This service monitors tasks, system events, and agent actions to determine
    when human oversight is needed. It provides a cost-effective alternative to
    separate cloud functions by integrating intervention logic directly into
    the main application.
    """
    
    def __init__(
        self,
        pubsub_client=None,
        confidence_threshold: float = 0.75,
        error_threshold: int = 3,
        budget_threshold: float = 100.0,
        enable_notifications: bool = True
    ):
        """
        Initialize intervention handler.
        
        Args:
            pubsub_client: Optional Pub/Sub client for notifications
            confidence_threshold: Minimum confidence score (0-1) before intervention
            error_threshold: Number of errors before triggering intervention
            budget_threshold: Monthly dollar amount threshold for budget alerts ($100/month)
            enable_notifications: Whether to send Pub/Sub notifications
        """
        self.pubsub_client = pubsub_client
        self.confidence_threshold = confidence_threshold
        self.error_threshold = error_threshold
        self.budget_threshold = budget_threshold
        self.enable_notifications = enable_notifications
        
        # Track error counts per task/agent
        self.error_counts: Dict[str, int] = {}
        
        logger.info(
            "Intervention handler initialized",
            confidence_threshold=confidence_threshold,
            error_threshold=error_threshold,
            budget_threshold=budget_threshold
        )
    
    async def check_intervention_needed(
        self,
        task: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, Optional[InterventionReason], Optional[InterventionLevel]]:
        """
        Check if a task requires human intervention.
        
        Args:
            task: Task data to evaluate
            context: Additional context (errors, budget, etc.)
            
        Returns:
            Tuple of (needs_intervention, reason, level)
        """
        context = context or {}
        
        # Check 1: Low confidence score
        confidence = task.get('confidence', 1.0)
        if confidence < self.confidence_threshold:
            logger.warning(
                "Low confidence task detected",
                task_id=task.get('id'),
                confidence=confidence,
                threshold=self.confidence_threshold
            )
            return True, InterventionReason.LOW_CONFIDENCE, InterventionLevel.WARNING
        
        # Check 2: Critical priority
        if task.get('priority') == 'critical':
            logger.info(
                "Critical priority task requires review",
                task_id=task.get('id')
            )
            return True, InterventionReason.CRITICAL_PRIORITY, InterventionLevel.URGENT
        
        # Check 3: Error threshold exceeded
        task_id = task.get('id')
        if task_id and self.error_counts.get(task_id, 0) >= self.error_threshold:
            logger.error(
                "Error threshold exceeded for task",
                task_id=task_id,
                error_count=self.error_counts[task_id],
                threshold=self.error_threshold
            )
            return True, InterventionReason.ERROR_THRESHOLD, InterventionLevel.URGENT
        
        # Check 4: Budget threshold exceeded
        budget_used = context.get('budget_used', 0)
        if budget_used > self.budget_threshold:
            logger.warning(
                "Budget threshold exceeded",
                task_id=task_id,
                budget_used=budget_used,
                threshold=self.budget_threshold
            )
            return True, InterventionReason.BUDGET_EXCEEDED, InterventionLevel.WARNING
        
        # Check 5: High-impact operations
        if task.get('impact', 'low') == 'high':
            logger.info(
                "High-impact task requires review",
                task_id=task_id,
                operation=task.get('operation')
            )
            return True, InterventionReason.HIGH_IMPACT, InterventionLevel.WARNING
        
        # Check 6: Manual review flag
        if task.get('requires_review', False):
            logger.info(
                "Manual review requested",
                task_id=task_id
            )
            return True, InterventionReason.MANUAL_REVIEW, InterventionLevel.INFO
        
        # Check 7: Compliance-sensitive operations
        compliance_keywords = ['financial', 'legal', 'contract', 'compliance', 'regulatory']
        task_text = task.get('text', '').lower()
        if any(keyword in task_text for keyword in compliance_keywords):
            logger.info(
                "Compliance-sensitive task detected",
                task_id=task_id
            )
            return True, InterventionReason.COMPLIANCE_CHECK, InterventionLevel.WARNING
        
        return False, None, None
    
    async def trigger_intervention(
        self,
        task: Dict[str, Any],
        reason: InterventionReason,
        level: InterventionLevel,
        additional_context: Optional[Dict[str, Any]] = None
    ):
        """
        Trigger intervention workflow.
        
        Args:
            task: Task requiring intervention
            reason: Reason for intervention
            level: Severity level
            additional_context: Additional context data
        """
        task_id = task.get('id', 'unknown')
        
        intervention_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'task_id': task_id,
            'reason': reason.value,
            'level': level.value,
            'task_summary': {
                'text': task.get('text', '')[:200],  # Truncate for logging
                'priority': task.get('priority'),
                'confidence': task.get('confidence'),
                'agent': task.get('agent')
            },
            'context': additional_context or {}
        }
        
        logger.bind(**intervention_data).log(
            self._get_log_level(level),
            "Human intervention triggered",
        )
        
        # Publish to Pub/Sub for external systems (dashboards, alerts, etc.)
        if self.enable_notifications and self.pubsub_client:
            try:
                await self.pubsub_client.publish(
                    topic='intervention-alerts',
                    message=intervention_data
                )
                logger.info(
                    "Intervention notification published",
                    task_id=task_id,
                    topic='intervention-alerts'
                )
            except Exception as e:
                logger.error(
                    "Failed to publish intervention notification",
                    error=str(e),
                    task_id=task_id
                )
        
        # Send notifications through additional channels
        await self._send_email_alert(task_id, intervention_data, level)
        await self._send_slack_notification(task_id, intervention_data, level)
        await self._send_sms_alert(task_id, intervention_data, level)
        await self._send_dashboard_update(task_id, intervention_data, level)
        await self._send_push_notification(task_id, intervention_data, level)
        
        return intervention_data
    
    async def _send_email_alert(
        self,
        task_id: str,
        intervention_data: dict,
        level: InterventionLevel
    ):
        """
        Send email notification for URGENT and CRITICAL interventions.
        
        Args:
            task_id: Task identifier
            intervention_data: Intervention details
            level: Intervention severity level
        """
        try:
            # Only send emails for higher severity levels
            if level not in [InterventionLevel.URGENT, InterventionLevel.CRITICAL]:
                return

            # Email configuration would typically come from environment or config
            # For now, we'll just log the action
            email_subject = f"[{level.upper()}] Intervention Required for Task {task_id}"
            email_body = f"""
Task ID: {task_id}
Reason: {intervention_data.get('reason')}
Level: {level}
Timestamp: {intervention_data.get('timestamp')}
Details: {intervention_data.get('context')}
            """

            logger.info(
                "Email alert would be sent",
                task_id=task_id,
                level=level,
                subject=email_subject
            )
            # In production, send via SMTP service here
        except Exception as e:
            logger.error(
                "Failed to prepare email alert",
                error=str(e),
                task_id=task_id
            )
    
    async def _send_slack_notification(
        self,
        task_id: str,
        intervention_data: dict,
        level: InterventionLevel
    ):
        """
        Send Slack notification for all intervention levels.
        
        Args:
            task_id: Task identifier
            intervention_data: Intervention details
            level: Intervention severity level
        """
        try:
            # Slack webhook URL would come from environment/config
            # For now, we'll just log the action
            color_map = {
                InterventionLevel.INFO: "#36a64f",
                InterventionLevel.WARNING: "#ff9900",
                InterventionLevel.URGENT: "#ff6600",
                InterventionLevel.CRITICAL: "#ff0000",
            }
            
            slack_message = {
                "attachments": [
                    {
                        "color": color_map.get(level, "#999999"),
                        "title": f"Intervention Required: {task_id}",
                        "text": f"Reason: {intervention_data.get('reason')}",
                        "fields": [
                            {
                                "title": "Level",
                                "value": level.upper(),
                                "short": True
                            },
                            {
                                "title": "Timestamp",
                                "value": intervention_data.get('timestamp'),
                                "short": True
                            }
                        ]
                    }
                ]
            }

            logger.info(
                "Slack notification prepared",
                task_id=task_id,
                level=level,
                message=slack_message
            )
            # In production, send to Slack webhook here
        except Exception as e:
            logger.error(
                "Failed to prepare Slack notification",
                error=str(e),
                task_id=task_id
            )
    
    async def _send_sms_alert(
        self,
        task_id: str,
        intervention_data: dict,
        level: InterventionLevel
    ):
        """
        Send SMS alert for CRITICAL interventions only.
        
        Args:
            task_id: Task identifier
            intervention_data: Intervention details
            level: Intervention severity level
        """
        try:
            # Only send SMS for critical level
            if level != InterventionLevel.CRITICAL:
                return

            # SMS message would be built here
            reason = intervention_data.get('reason', 'Unknown')
            reason_short = reason[:50] if reason else 'Unknown'
            sms_message = f"CRITICAL: Task {task_id} requires immediate intervention. Reason: {reason_short}..."

            logger.warning(
                "SMS alert would be sent",
                task_id=task_id,
                level=level,
                message=sms_message
            )
            # In production, send via Twilio or similar service here
        except Exception as e:
            logger.error(
                "Failed to prepare SMS alert",
                error=str(e),
                task_id=task_id
            )
    
    async def _send_dashboard_update(
        self,
        task_id: str,
        intervention_data: dict,
        level: InterventionLevel
    ):
        """
        Update real-time dashboard with intervention alert.
        
        Args:
            task_id: Task identifier
            intervention_data: Intervention details
            level: Intervention severity level
        """
        try:
            dashboard_event = {
                "event_type": "intervention_alert",
                "task_id": task_id,
                "level": level,
                "timestamp": intervention_data.get('timestamp'),
                "reason": intervention_data.get('reason'),
                "context": intervention_data.get('context')
            }

            logger.info(
                "Dashboard update event created",
                task_id=task_id,
                level=level,
                event=dashboard_event
            )
            # In production, emit WebSocket event to connected dashboards here
        except Exception as e:
            logger.error(
                "Failed to prepare dashboard update",
                error=str(e),
                task_id=task_id
            )
    
    async def _send_push_notification(
        self,
        task_id: str,
        intervention_data: dict,
        level: InterventionLevel
    ):
        """
        Send push notification to mobile devices and browsers.
        
        Args:
            task_id: Task identifier
            intervention_data: Intervention details
            level: Intervention severity level
        """
        try:
            push_notification = {
                "title": f"Intervention Required",
                "body": f"Task {task_id}: {(intervention_data.get('reason') or 'Review required')[:100]}",
                "badge": 1,
                "data": {
                    "task_id": task_id,
                    "level": level,
                    "timestamp": intervention_data.get('timestamp')
                },
                "action": f"/tasks/{task_id}/review"
            }

            logger.info(
                "Push notification prepared",
                task_id=task_id,
                level=level,
                notification=push_notification
            )
            # In production, send via Firebase Cloud Messaging or similar service here
        except Exception as e:
            logger.error(
                "Failed to prepare push notification",
                error=str(e),
                task_id=task_id
            )
    
    async def record_error(self, task_id: str, error: Exception):
        """
        Record an error for a task and check if intervention needed.
        
        Args:
            task_id: Task identifier
            error: The error that occurred
        """
        self.error_counts[task_id] = self.error_counts.get(task_id, 0) + 1
        error_count = self.error_counts[task_id]
        
        logger.warning(
            "Error recorded for task",
            task_id=task_id,
            error=str(error),
            error_count=error_count,
            threshold=self.error_threshold
        )
        
        # Auto-trigger intervention if threshold exceeded
        if error_count >= self.error_threshold:
            await self.trigger_intervention(
                task={'id': task_id},
                reason=InterventionReason.ERROR_THRESHOLD,
                level=InterventionLevel.URGENT,
                additional_context={
                    'error_count': error_count,
                    'latest_error': str(error)
                }
            )
    
    def reset_error_count(self, task_id: str):
        """Reset error count for a task (after resolution)."""
        if task_id in self.error_counts:
            del self.error_counts[task_id]
            logger.info("Error count reset", task_id=task_id)
    
    def get_pending_interventions(self) -> List[str]:
        """Get list of task IDs with pending interventions (high error counts)."""
        return [
            task_id for task_id, count in self.error_counts.items()
            if count >= self.error_threshold
        ]
    
    def update_thresholds(
        self,
        confidence: Optional[float] = None,
        errors: Optional[int] = None,
        budget: Optional[float] = None
    ):
        """Update intervention thresholds dynamically."""
        if confidence is not None:
            self.confidence_threshold = confidence
        if errors is not None:
            self.error_threshold = errors
        if budget is not None:
            self.budget_threshold = budget
        
        logger.info(
            "Intervention thresholds updated",
            confidence_threshold=self.confidence_threshold,
            error_threshold=self.error_threshold,
            budget_threshold=self.budget_threshold
        )
    
    @staticmethod
    def _get_log_level(level: InterventionLevel) -> str:
        """Map intervention level to log level."""
        mapping = {
            InterventionLevel.INFO: "info",
            InterventionLevel.WARNING: "warning",
            InterventionLevel.URGENT: "warning",
            InterventionLevel.CRITICAL: "error"
        }
        return mapping.get(level, "info")

    # ========================================================================
    # ADDITIONAL NOTIFICATION CHANNELS
    # ========================================================================

    async def _send_discord_notification(
        self,
        webhook_url: str,
        title: str,
        message: str,
        level: InterventionLevel,
    ) -> bool:
        """
        Send notification to Discord via webhook.

        Args:
            webhook_url: Discord webhook URL
            title: Notification title
            message: Notification message
            level: Intervention level (used for color coding)

        Returns:
            True if successful, False otherwise
        """
        try:
            import aiohttp
            import json

            # Color mapping for Discord embeds
            color_map = {
                InterventionLevel.INFO: 3447003,        # Blue
                InterventionLevel.WARNING: 15105570,    # Orange
                InterventionLevel.URGENT: 16711680,     # Red
                InterventionLevel.CRITICAL: 10038562,   # Dark Red
            }

            payload = {
                "embeds": [
                    {
                        "title": title,
                        "description": message,
                        "color": color_map.get(level, 3447003),
                        "footer": {
                            "text": f"Intervention Level: {level.value.upper()}",
                            "icon_url": "https://platform.slack-edge.com/img/default_application_icon.png",
                        },
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                ]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status in (200, 204):
                        logger.info(f"Discord notification sent successfully", webhook_status=response.status)
                        return True
                    else:
                        logger.warning(
                            f"Discord webhook returned status {response.status}",
                            webhook_status=response.status,
                        )
                        return False

        except Exception as e:
            logger.error(f"Error sending Discord notification: {str(e)}", error=str(e))
            return False

    async def _send_enhanced_sms_notification(
        self,
        phone_number: str,
        message: str,
        twilio_account_sid: Optional[str] = None,
        twilio_auth_token: Optional[str] = None,
        from_number: Optional[str] = None,
    ) -> bool:
        """
        Send SMS notification via Twilio with enhanced features.

        Args:
            phone_number: Recipient phone number (E.164 format)
            message: SMS message content (max 160 characters)
            twilio_account_sid: Twilio account SID (uses env var if not provided)
            twilio_auth_token: Twilio auth token (uses env var if not provided)
            from_number: Sender phone number (uses env var if not provided)

        Returns:
            True if successful, False otherwise
        """
        try:
            import os
            from twilio.rest import Client

            # Use provided credentials or fall back to environment variables
            account_sid = twilio_account_sid or os.getenv("TWILIO_ACCOUNT_SID")
            auth_token = twilio_auth_token or os.getenv("TWILIO_AUTH_TOKEN")
            from_num = from_number or os.getenv("TWILIO_PHONE_NUMBER")

            if not all([account_sid, auth_token, from_num]):
                logger.warning(
                    "Twilio credentials not configured",
                    has_account_sid=bool(account_sid),
                    has_auth_token=bool(auth_token),
                    has_from_number=bool(from_num),
                )
                return False

            client = Client(account_sid, auth_token)

            # Truncate message to SMS limits if needed
            sms_message = message[:160] if len(message) > 160 else message

            message_obj = client.messages.create(
                body=sms_message,
                from_=from_num,
                to=phone_number,
            )

            logger.info(
                f"SMS notification sent successfully",
                message_sid=message_obj.sid,
                to_number=phone_number,
            )
            return True

        except ImportError:
            logger.warning("Twilio library not installed. Install with: pip install twilio")
            return False
        except Exception as e:
            logger.error(f"Error sending SMS notification: {str(e)}", error=str(e))
            return False

    async def _send_inapp_notification(
        self,
        user_id: str,
        title: str,
        message: str,
        level: InterventionLevel,
        action_url: Optional[str] = None,
    ) -> bool:
        """
        Store in-app notification in database for user to see.

        Args:
            user_id: User ID to receive notification
            title: Notification title
            message: Notification message
            level: Intervention level
            action_url: Optional URL for action button

        Returns:
            True if successful, False otherwise
        """
        try:
            from database import get_session
            from models import Notification

            db = get_session()
            timestamp = datetime.now()

            # Create in-app notification record
            notification = Notification(
                user_id=user_id,
                title=title,
                message=message,
                notification_type=f"intervention_{level.value}",
                is_read=False,
                created_at=timestamp,
                action_url=action_url,
                metadata={
                    "intervention_level": level.value,
                    "created_at": timestamp.isoformat(),
                },
            )

            db.add(notification)
            db.commit()
            db.close()

            logger.info(
                f"In-app notification created",
                user_id=user_id,
                notification_type=f"intervention_{level.value}",
            )
            return True

        except ImportError:
            logger.warning("Database models not available for in-app notifications")
            return False
        except Exception as e:
            logger.error(f"Error creating in-app notification: {str(e)}", error=str(e))
            return False


# Singleton instance for easy access
_intervention_handler: Optional[InterventionHandler] = None


def get_intervention_handler() -> Optional[InterventionHandler]:
    """Get the global intervention handler instance."""
    return _intervention_handler


def initialize_intervention_handler(
    pubsub_client=None,
    **kwargs
) -> InterventionHandler:
    """
    Initialize the global intervention handler.
    
    Args:
        pubsub_client: Pub/Sub client for notifications
        **kwargs: Additional configuration options
        
    Returns:
        Initialized InterventionHandler instance
    """
    global _intervention_handler
    _intervention_handler = InterventionHandler(pubsub_client=pubsub_client, **kwargs)
    return _intervention_handler
