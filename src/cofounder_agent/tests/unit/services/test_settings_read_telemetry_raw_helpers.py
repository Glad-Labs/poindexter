"""Raw-SQL settings readers must stamp read-telemetry (poindexter#756 follow-up).

Several worker jobs/handlers read ``app_settings`` through a copy-pasted
``_read_setting`` / ``_get_setting`` helper (raw ``pool.fetchrow``) or an inline
``SELECT value FROM app_settings`` instead of ``SiteConfig`` / ``SettingsService``.
Those reads never reached ``FlushSettingsReadTelemetryJob``, so
``ProbeZeroReaderSettingsJob`` flagged the live keys they consult
(``approval_ttl_days`` and the topic / memory / retention knobs) as orphans.

Each reader now records the key into ``services.settings_read_sink`` — the same
sink ``SettingsService.get`` and ``admin_db.get_setting`` write to — so the flush
job can stamp ``app_settings.last_read_at``. The read is recorded as an *ask*:
it fires even when the key is absent, mirroring the visible accessors.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import settings_read_sink
from services.integrations.handlers.retention_summarize_to_table import (
    _get_setting as retention_get_setting,
)
from services.jobs.check_memory_staleness import _get_setting as check_memory_get_setting
from services.jobs.reap_stale_topic_batches import _read_setting as reap_read_setting
from services.jobs.topic_auto_resolve import _read_setting as topic_read_setting

# Every reusable helper shares the shape ``async def (pool, key, default)`` and
# reads via ``pool.fetchrow``.
_FETCHROW_HELPERS = [
    check_memory_get_setting,
    retention_get_setting,
    reap_read_setting,
    topic_read_setting,
]


def _fetchrow_pool(row):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("helper", _FETCHROW_HELPERS)
async def test_raw_helper_records_read_on_hit(helper):
    settings_read_sink.drain_read_keys()  # clear cross-test state
    pool = _fetchrow_pool({"value": "some-value"})

    await helper(pool, "a_tunable_key", "default")

    assert "a_tunable_key" in settings_read_sink.drain_read_keys()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("helper", _FETCHROW_HELPERS)
async def test_raw_helper_records_read_even_when_key_absent(helper):
    """The ask is recorded regardless of resolution — an absent row still
    stamps, so a key nothing has set yet doesn't masquerade as an orphan."""
    settings_read_sink.drain_read_keys()
    pool = _fetchrow_pool(None)

    await helper(pool, "never_set_key", "default")

    assert "never_set_key" in settings_read_sink.drain_read_keys()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_expire_stale_approvals_records_legacy_key():
    """The job's legacy back-compat read of ``approval_ttl_days`` records the
    read so the key stops surfacing as a zero-reader orphan."""
    from services.jobs.expire_stale_approvals import ExpireStaleApprovalsJob

    settings_read_sink.drain_read_keys()

    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="7")
    conn.fetch = AsyncMock(return_value=[])  # UPDATE ... RETURNING → no rows

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire

    # ttl_days=0 in config forces the legacy app_settings lookup path.
    await ExpireStaleApprovalsJob().run(pool, {"ttl_days": 0})

    assert "approval_ttl_days" in settings_read_sink.drain_read_keys()
