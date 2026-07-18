"""Unit tests — retention summarize-to-table setting-read visibility.

Silent-excepts OLD-debt burn-down (batch 27). ``_get_setting`` reads one
``app_settings`` value with a code-default fallback and swallowed a failed
read at ``logger.debug`` — which the worker does not ship to Loki, so the
failure was invisible in production.

The verdict here is settled by **precedent, not judgement**. This handler's
own docstring claims it mirrors
``services.jobs.collapse_old_embeddings._get_setting`` — a module that no
longer exists (the reference is stale and is corrected alongside this fix).
The surviving twin, ``services/jobs/check_memory_staleness.py::_get_setting``,
was already de-silenced by poindexter#455 for exactly this reason:

    "used to be silent. A DB blip used to look identical to 'operator never
    set this key', which made stale-threshold tuning impossible to diagnose."

This handler is the straggler that missed that sweep. It matters more here
than in most places because retention **deletes data**: falling back to a
built-in window instead of the operator's tuned one can prune earlier than
they configured, and nothing said so.

The fallback behaviour is unchanged — a failed read still returns the default
and never crashes the retention pass. It just has to be visible.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.integrations.handlers import retention_summarize_to_table as rstt

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_LOGGER = "services.integrations.handlers.retention_summarize_to_table"


async def test_setting_read_failure_is_visible(caplog):
    """A failed app_settings read must WARN — the caller silently proceeds
    on a built-in default that may prune more aggressively than configured."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("app_settings read exploded"))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        value = await rstt._get_setting(pool, "some_retention_key", "default-value")

    # Contract preserved: never crashes the retention pass, returns the default.
    assert value == "default-value"

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, (
        "a swallowed app_settings read is indistinguishable from 'operator "
        "never set this key' — it must be visible at WARNING (debug is not "
        "shipped to Loki)"
    )
    combined = " ".join(r.getMessage() for r in warnings)
    assert "some_retention_key" in combined, "the warning must name the key"
    assert "app_settings read exploded" in combined, (
        "the underlying exception must reach the operator"
    )


async def test_healthy_setting_read_emits_no_warning(caplog):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"value": "tuned-value"})

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        value = await rstt._get_setting(pool, "some_retention_key", "default-value")

    assert value == "tuned-value"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
