"""
Webhook Routes for Content Events

Handles webhook events from Strapi CMS for content creation, publishing, and deletion.
Triggers Next.js ISR revalidation on content updates to keep the public site fresh.
"""

import logging
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from schemas.webhooks_schemas import (
    ContentWebhookPayload,
    WebhookEntry,
    WebhookResponse,
)

logger = logging.getLogger(__name__)

# Router for all webhook endpoints
webhook_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


async def trigger_nextjs_revalidation(paths: list[str] = None) -> bool:
    """
    Trigger Next.js ISR revalidation on the public site.
    
    Calls the revalidation endpoint to clear cache and regenerate pages
    when content is published or updated.
    
    Args:
        paths: List of paths to revalidate. Defaults to ["/", "/archive"]
        
    Returns:
        True if revalidation succeeded, False otherwise
    """
    if paths is None:
        paths = ["/", "/archive"]
    
    # Get Next.js public site URL from environment or use default
    nextjs_url = os.getenv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:3000")
    if nextjs_url.endswith("/api"):
        # Remove /api suffix if present
        nextjs_url = nextjs_url[:-4]
    
    revalidate_url = f"{nextjs_url}/api/revalidate"
    revalidate_secret = os.getenv("REVALIDATE_SECRET", "dev-secret-key")
    
    try:
        logger.info(f"🔄 Triggering Next.js ISR revalidation...")
        logger.info(f"   URL: {revalidate_url}")
        logger.info(f"   Paths: {paths}")
        
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                revalidate_url,
                json={"paths": paths},
                headers={
                    "x-revalidate-secret": revalidate_secret,
                    "Content-Type": "application/json",
                },
            )
            
            if response.status_code == 200:
                logger.info(f"✅ ISR revalidation successful")
                return True
            else:
                logger.warning(f"⚠️ ISR revalidation returned {response.status_code}")
                logger.warning(f"   Response: {response.text[:200]}")
                return False
                
    except httpx.TimeoutException:
        logger.warning(f"⚠️ ISR revalidation timed out (10s)")
        return False
    except Exception as e:
        logger.warning(f"⚠️ Failed to trigger ISR revalidation: {type(e).__name__}: {e}")
        return False


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
            logger.info(f"✅ Entry published: {payload.entry.id} ({payload.entry.title})")
            # Trigger ISR revalidation to update the public site
            await trigger_nextjs_revalidation(["/", "/archive"])
        elif payload.event == "entry.unpublish":
            logger.info(f"❌ Entry unpublished: {payload.entry.id} ({payload.entry.title})")
            # Trigger ISR revalidation to remove from public view
            await trigger_nextjs_revalidation(["/", "/archive"])
        elif payload.event == "entry.delete":
            logger.info(f"🗑️  Entry deleted: {payload.entry.id}")
            # Trigger ISR revalidation to remove from public view
            await trigger_nextjs_revalidation(["/", "/archive"])
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
