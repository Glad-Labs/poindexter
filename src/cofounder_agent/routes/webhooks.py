"""
Webhook Routes for Content Events

Handles webhook events from Strapi CMS for content creation,  publishing, and deletion.
"""

from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, status, Request
import logging

from schemas.webhooks_schemas import (
    WebhookEntry,
    ContentWebhookPayload,
    WebhookResponse,
)

logger = logging.getLogger(__name__)

# Router for all webhook endpoints
webhook_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


# ============================================================================
# WEBHOOK ENDPOINTS
# ============================================================================


@webhook_router.post(
    "/content-created",
    response_model=WebhookResponse,
    status_code=status.HTTP_200_OK,
    summary="Handle Strapi content webhook events",
    responses={
        200: {"description": "Webhook processed successfully"},
        400: {"description": "Invalid webhook payload"},
        500: {"description": "Server error processing webhook"},
    },
)
async def handle_content_webhook(request: Request) -> WebhookResponse:
    """
    Handle webhook events from Strapi CMS.

    Supported events:
    - `entry.create`: New content created in CMS
    - `entry.publish`: Content published to live
    - `entry.unpublish`: Content unpublished from live
    - `entry.delete`: Content deleted from CMS

    **Request Body:**
    - `event`: Type of event that occurred
    - `model`: Content model name
    - `entry`: Entry data including ID and title

    **Response:**
    - `status`: Always "received" on success
    - `event`: Echo back the event type
    - `entry_id`: The entry ID from the webhook
    """
    try:
        # Parse JSON body
        body = await request.json()

        # Validate using model
        try:
            payload = ContentWebhookPayload(**body)
        except ValidationError as ve:
            # Extract field errors and return as 400
            errors = ve.errors()
            first_error = errors[0] if errors else {}
            field_path = first_error.get("loc", ["unknown"])[0]
            msg = first_error.get("msg", "Invalid webhook payload")

            # Customize message for specific field errors
            if field_path == "entry" and "required" in msg:
                detail = "Entry ID missing from webhook payload"
            elif str(field_path).startswith("entry") and "id" in str(field_path):
                detail = "Entry ID missing from webhook payload"
            else:
                detail = f"Invalid webhook payload: {field_path} - {msg}"
            raise HTTPException(status_code=400, detail=detail)

        # Validate entry ID exists
        if not payload.entry or not payload.entry.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Entry ID missing from webhook payload",
            )

        # Log webhook receipt
        logger.info(
            f"Received webhook event",
            extra={
                "event": payload.event,
                "model": payload.model,
                "entry_id": payload.entry.id,
                "title": payload.entry.title,
            },
        )

        # List of handled events
        handled_events = ["entry.create", "entry.publish", "entry.unpublish", "entry.delete"]

        # Process based on event type
        if payload.event == "entry.create":
            logger.info(f"New entry created: {payload.entry.id}")
        elif payload.event == "entry.publish":
            logger.info(f"Entry published: {payload.entry.id}")
        elif payload.event == "entry.unpublish":
            logger.info(f"Entry unpublished: {payload.entry.id}")
        elif payload.event == "entry.delete":
            logger.info(f"Entry deleted: {payload.entry.id}")
        else:
            # Unknown event - log but still return success with "ignored" status
            logger.info(
                f"Webhook for unknown event '{payload.event}' on entry {payload.entry.id} - not handled"
            )
            return WebhookResponse(
                status="ignored",
                event=payload.event,
                entry_id=payload.entry.id,
                message=f"Event type '{payload.event}' is not handled by this webhook",
            )

        # Return success response for handled events
        return WebhookResponse(
            status="received",
            event=payload.event,
            entry_id=payload.entry.id,
            message=f"Webhook for {payload.event} on entry {payload.entry.id} received",
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid webhook payload: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error processing webhook"
        )
