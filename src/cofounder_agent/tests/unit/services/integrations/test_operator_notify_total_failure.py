"""notify_operator must escalate a TOTAL delivery failure to logger.error.

Audit finding H4: the operator-alert last mile degrades through logger.warning.
If BOTH the framework dispatcher path and the legacy Discord fallback fail
(e.g. DB down AND the webhook unreachable — a correlated outage, exactly when
you most need a page), the only evidence is a Loki warning. The GlitchTip sink
only captures logger.error+, so a total alert-channel outage is invisible to
alerting — the alerting system cannot alert that it is down.

This pins the fix: when the final fallback also fails (message delivered to NO
channel), escalate to logger.error(exc_info=True) so the outage becomes a
GlitchTip event.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_total_delivery_failure_escalates_to_error(caplog):
    """Framework path unavailable + legacy webhook raises → logger.error fires."""
    from services.integrations import operator_notify

    with patch(
        "services.integrations.shared_context.get_database_service",
        return_value=None,  # dispatcher path skipped → fall through to legacy
    ), patch(
        "services.integrations.operator_notify._legacy_discord_webhook",
        new=AsyncMock(side_effect=RuntimeError("discord webhook 500")),
    ):
        with caplog.at_level(logging.WARNING):
            await operator_notify.notify_operator("urgent page", critical=True)

    total_failure_errors = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "notify_operator" in r.getMessage()
    ]
    assert total_failure_errors, (
        "a total operator-alert delivery failure must be logged at ERROR "
        "(GlitchTip-visible), not warning (Loki-only)"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_delivery_does_not_log_error(caplog):
    """Happy path: dispatcher delivers → no error record (guards against a
    fix that screams on every call)."""
    from unittest.mock import MagicMock

    from services.integrations import operator_notify

    db_service = MagicMock()
    db_service.pool = MagicMock()

    with patch(
        "services.integrations.shared_context.get_database_service",
        return_value=db_service,
    ), patch(
        "services.integrations.operator_notify._resolve_site_config",
        return_value=MagicMock(),
    ), patch(
        "services.integrations.outbound_dispatcher.deliver",
        new=AsyncMock(),
    ):
        with caplog.at_level(logging.ERROR):
            await operator_notify.notify_operator("hello", critical=True)

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
