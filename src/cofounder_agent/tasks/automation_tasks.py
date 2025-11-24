"""Automation and utility tasks."""

from typing import Dict, Any
from src.cofounder_agent.tasks.base import PureTask, ExecutionContext, TaskStatus, TaskResult
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class EmailGenerateTask(PureTask):
    """
    Email generation: Create email campaigns.
    
    Input:
        - topic: str - Email topic/subject
        - audience: str - Target audience (subscribers, leads, customers)
        - style: str - Email style (promotional, informational, announcement)
        - include_cta: bool - Include call-to-action (default: True)
    
    Output:
        - subject: str - Email subject
        - preview: str - Email preview text
        - body: str - Email body HTML
        - cta_text: str - Call-to-action text
    """

    def __init__(self):
        super().__init__(
            name="email_generate",
            description="Generate email campaign content",
            required_inputs=["topic"],
            timeout_seconds=90,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute email generation task."""
        from src.cofounder_agent.services.model_router import model_router
        
        topic = input_data["topic"]
        audience = input_data.get("audience", "general")
        style = input_data.get("style", "informational")
        include_cta = input_data.get("include_cta", True)
        
        prompt = f"""Generate a professional email campaign:

Topic: {topic}
Target Audience: {audience}
Style: {style}
Include CTA: {include_cta}

Create:
1. Compelling subject line (50 chars max)
2. Preview text (100 chars max)
3. HTML email body (well-formatted, professional)
4. Strong call-to-action (if requested)
5. Unsubscribe note

Format as JSON with keys: subject, preview, body, cta_text, cta_url"""
        
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.6,
            max_tokens=1500,
        )
        
        try:
            import json
            email_data = json.loads(response)
        except:
            email_data = {
                "subject": topic,
                "preview": topic[:100],
                "body": response,
                "cta_text": "Learn More",
            }
        
        return {
            "topic": topic,
            "audience": audience,
            "subject": email_data.get("subject", ""),
            "preview": email_data.get("preview", ""),
            "body": email_data.get("body", ""),
            "cta_text": email_data.get("cta_text", ""),
            "cta_url": email_data.get("cta_url", ""),
            "style": style,
        }


class EmailSendTask(PureTask):
    """
    Email sending: Send email campaigns.
    
    Input:
        - subject: str - Email subject
        - body: str - Email body
        - recipients: list - Email addresses or audience segment
        - send_time: str - When to send (now, scheduled time)
    
    Output:
        - sent: bool - Send success status
        - recipient_count: int - Number of recipients
        - campaign_id: str - Email campaign ID
    """

    def __init__(self):
        super().__init__(
            name="email_send",
            description="Send email campaigns",
            required_inputs=["subject", "body", "recipients"],
            timeout_seconds=30,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute email sending task."""
        
        subject = input_data["subject"]
        body = input_data["body"]
        recipients = input_data.get("recipients", [])
        send_time = input_data.get("send_time", "now")
        
        try:
            # In production, would integrate with SendGrid, Mailchimp, etc.
            campaign_id = f"email-{context.workflow_id}"
            
            logger.info(
                f"Email campaign scheduled",
                extra={
                    "workflow_id": context.workflow_id,
                    "campaign_id": campaign_id,
                    "recipient_count": len(recipients),
                }
            )
            
            return {
                "sent": True,
                "subject": subject,
                "campaign_id": campaign_id,
                "recipient_count": len(recipients),
                "send_time": send_time,
                "status": "scheduled" if send_time != "now" else "sent",
            }
        except Exception as e:
            logger.error(f"Email sending failed: {str(e)}")
            return {
                "sent": False,
                "error": str(e),
            }


class SummarizeTask(PureTask):
    """
    Summarization: Condense content into summary.
    
    Input:
        - content: str - Content to summarize
        - length: str - Summary length (short, medium, long)
    
    Output:
        - summary: str - Summarized content
        - key_points: list - Main points
    """

    def __init__(self):
        super().__init__(
            name="summarize",
            description="Summarize content into key points",
            required_inputs=["content"],
            timeout_seconds=60,
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute summarization task."""
        from src.cofounder_agent.services.model_router import model_router
        
        content = input_data["content"]
        length = input_data.get("length", "medium")
        
        length_config = {
            "short": {"sentences": 2, "words": 50},
            "medium": {"sentences": 5, "words": 150},
            "long": {"sentences": 10, "words": 300},
        }
        
        config = length_config.get(length, length_config["medium"])
        
        prompt = f"""Summarize this content in {length} format:

Content:
{content}

Create:
1. Summary ({config['sentences']} sentences, ~{config['words']} words)
2. 5-7 key points
3. Main takeaway

Format as JSON with keys: summary, key_points, main_takeaway"""
        
        response = await model_router.query_with_fallback(
            prompt=prompt,
            temperature=0.3,
            max_tokens=500,
        )
        
        try:
            import json
            summary_data = json.loads(response)
        except:
            summary_data = {
                "summary": content[:200],
                "key_points": [],
                "main_takeaway": "",
            }
        
        return {
            "summary": summary_data.get("summary", ""),
            "key_points": summary_data.get("key_points", []),
            "main_takeaway": summary_data.get("main_takeaway", ""),
            "length": length,
            "original_length": len(content),
        }


class ApprovalGateTask(PureTask):
    """
    Approval gate: Pause workflow for user approval.
    
    Input:
        - content: dict - Content for approval (preview)
        - approval_timeout: int - Timeout in seconds (default: 3600)
    
    Output:
        - approved: bool - Whether approved
        - approval_message: str - Approval status message
        - timestamp: str - Approval timestamp
    """

    def __init__(self):
        super().__init__(
            name="approval_gate",
            description="Pause workflow and wait for user approval",
            required_inputs=["content"],
            timeout_seconds=7200,  # 2 hour timeout
        )

    async def _execute_internal(
        self,
        input_data: Dict[str, Any],
        context: ExecutionContext,
    ) -> Dict[str, Any]:
        """Execute approval gate task."""
        
        content = input_data.get("content", {})
        approval_timeout = input_data.get("approval_timeout", 3600)
        
        # In production, would:
        # 1. Store checkpoint to database
        # 2. Send notification to user
        # 3. Wait for webhook/API call with approval
        # 4. Resume pipeline after approval
        
        logger.info(
            "Workflow awaiting approval",
            extra={
                "workflow_id": context.workflow_id,
                "timeout_seconds": approval_timeout,
            }
        )
        
        # For now, return awaiting status
        # Production would store state and poll for approval
        return {
            "status": "awaiting_approval",
            "content_preview": {
                "title": content.get("title", ""),
                "excerpt": content.get("excerpt", "")[:200],
                "type": content.get("type", "content"),
            },
            "approval_requested_at": datetime.now().isoformat(),
            "approval_timeout_seconds": approval_timeout,
            "approval_url": f"/oversight-hub/approvals/{context.workflow_id}",
            "next_step": "waiting_for_approval",
        }
