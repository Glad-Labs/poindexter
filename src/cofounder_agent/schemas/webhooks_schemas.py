"""Webhook Event Models

Consolidated schemas for content webhook processing from CMS.
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class WebhookEntry(BaseModel):
    """Webhook entry (content item) data"""

    id: int = Field(..., description="Entry ID")
    title: Optional[str] = Field(None, description="Entry title")


class ContentWebhookPayload(BaseModel):
    """Payload for content webhook events"""

    event: str = Field(
        ..., description="Event type (entry.create, entry.publish, entry.unpublish, entry.delete)"
    )
    model: str = Field(..., description="Model name (e.g., article, page)")
    entry: WebhookEntry = Field(..., description="Entry data")


class WebhookResponse(BaseModel):
    """Response model for webhook endpoints"""

    status: str = Field(default="received", description="Processing status")
    event: str = Field(..., description="Event type that was processed")
    entry_id: int = Field(..., description="Entry ID from the webhook")
    message: Optional[str] = Field(None, description="Additional message")
