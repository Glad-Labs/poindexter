"""Catch-all inbound webhook route.

Every declarative webhook endpoint is served by this single route.
Lookup, signature verification, and handler dispatch live in
:mod:`services.integrations.webhook_dispatcher`.

Legacy routes in ``routes/external_webhooks.py`` and
``routes/alertmanager_webhook_routes.py`` remain registered during
the migration window — they forward to the same dispatcher by name,
so once the seed rows are flipped to ``enabled=true`` an operator can
swap Alertmanager/Lemon Squeezy/Resend over to the new URL
(``/api/webhooks/<name>``) and the legacy routes can be removed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from services.database_service import DatabaseService
from services.integrations.webhook_dispatcher import dispatch_inbound
from utils.route_utils import get_database_dependency, get_site_config_dependency

webhooks_router = APIRouter(prefix="/api/webhooks", tags=["Integrations (webhooks)"])


@webhooks_router.post("/{name}")
async def inbound_webhook(
    name: str,
    request: Request,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: Any = Depends(get_site_config_dependency),
):
    return await dispatch_inbound(
        name,
        request,
        db_service=db_service,
        site_config=site_config,
    )
