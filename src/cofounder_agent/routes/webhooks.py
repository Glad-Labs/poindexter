"""
DEPRECATED: Webhook Routes for Strapi CMS

This file is deprecated. Strapi CMS is no longer used.
The FastAPI backend serves as the CMS directly.

Cache revalidation is now handled by:
- revalidate_routes.py - For direct publish operations
- Direct integration in cms_routes.py - For CMS CRUD operations

Note: webhooks_router is still registered in route_registration.py but all endpoints
are disabled/no-op. This can be removed in a future cleanup.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Router for all webhook endpoints
webhook_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

logger.info("⚠️  Webhooks router loaded (deprecated - Strapi CMS no longer used)")

