"""
Unit tests for ``publish_service.drain_background_tasks`` and the
``DatabaseService.close()`` ordering that lets publish-tail fire-and-forget
side effects (newsletter send → ``newsletter_campaign_sent`` audit row)
survive short-lived pool teardown.

GlitchTip #863 root cause B — the Prefect flow subprocess (and the CLI
publish paths) build+close their own ``DatabaseService`` per run.
``publish_post_from_task`` spawns ``_send_post_newsletter_bg`` fire-and-
forget moments before the owner's ``finally`` calls
``database_service.close()``. ``close()`` drained *already-scheduled*
audit writes (root cause A, ``drain_pending_writes``) but not the
publish-side background task that hasn't reached its ``audit_log_bg``
call yet — the send dies mid-flight on a closed pool and the audit row
is never written.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import services.audit_log as audit_mod
import services.publish_service as publish_service
from services.site_config import SiteConfig

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal asyncpg-pool stand-in that rejects writes once closed."""

    def __init__(self, name: str):
        self.name = name
        self.closed = False
        self.executed: list[tuple] = []

    async def execute(self, sql, *args, **kwargs):
        if self.closed:
            raise RuntimeError("pool is closed")
        self.executed.append((sql, args))

    async def fetch(self, sql, *args, **kwargs):
        if self.closed:
            raise RuntimeError("pool is closed")
        return []

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_background_state():
    """Isolate the module-level task registries across tests."""
    original_logger = audit_mod._global_audit_logger
    yield
    audit_mod._global_audit_logger = original_logger
    for t in list(publish_service._background_tasks):
        t.cancel()
    publish_service._background_tasks.clear()
    for t in list(audit_mod._pending_writes):
        t.cancel()
    audit_mod._pending_writes.clear()


# ---------------------------------------------------------------------------
# drain_background_tasks semantics (mirrors audit_log.drain_pending_writes)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDrainBackgroundTasks:
    @pytest.mark.asyncio
    async def test_drain_awaits_inflight_task_before_returning(self):
        ran: list[str] = []

        async def side_effect():
            await asyncio.sleep(0.01)
            ran.append("done")

        publish_service._spawn_background(side_effect(), name="t")
        # Scheduled, not run — this is the window where a pool owner used
        # to tear down underneath the task.
        assert ran == []

        await publish_service.drain_background_tasks()

        assert ran == ["done"]

    @pytest.mark.asyncio
    async def test_drain_is_noop_when_nothing_pending(self):
        publish_service._background_tasks.clear()
        # Must return promptly and never raise when there is nothing to flush.
        await publish_service.drain_background_tasks()

    @pytest.mark.asyncio
    async def test_drain_is_bounded_and_cancels_stragglers(self):
        async def hangs():
            await asyncio.sleep(3600)

        task = publish_service._spawn_background(hangs(), name="wedged")

        # A wedged send must not hang teardown: drain returns after its own
        # timeout and cancels the straggler (the outer wait_for guards
        # against a drain that ignores its timeout).
        await asyncio.wait_for(
            publish_service.drain_background_tasks(timeout=0.05), timeout=2.0
        )
        # Give the cancellation a tick to land.
        await asyncio.sleep(0)
        assert task.cancelled() or task.cancelling()


# ---------------------------------------------------------------------------
# DatabaseService.close() ordering — publish drain → audit drain → pool close
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCloseDrainsPublishBackgroundTasks:
    @pytest.mark.asyncio
    async def test_close_drains_publish_tasks_before_audit_drain_and_pool_close(self):
        from services.database_service import DatabaseService

        svc = DatabaseService(database_url="x", site_config=SiteConfig())
        cloud_pool = AsyncMock(name="cloud")
        local_pool = AsyncMock(name="local")
        svc.pool = cloud_pool
        svc.local_pool = local_pool

        order: list[str] = []
        local_pool.close.side_effect = lambda *a, **k: order.append("local_close")
        cloud_pool.close.side_effect = lambda *a, **k: order.append("cloud_close")

        async def _publish_drain(*a, **k):
            order.append("publish_drain")

        async def _audit_drain(*a, **k):
            order.append("audit_drain")

        with patch(
            "services.publish_service.drain_background_tasks",
            new=AsyncMock(side_effect=_publish_drain),
        ) as publish_drain, patch(
            "services.database_service.drain_pending_writes",
            new=AsyncMock(side_effect=_audit_drain),
        ) as audit_drain:
            await svc.close()

        publish_drain.assert_awaited_once()
        audit_drain.assert_awaited_once()
        # Publish-tail tasks schedule their audit writes as they finish, so
        # they must be flushed BEFORE the audit drain, and both before any
        # pool close.
        assert order == [
            "publish_drain",
            "audit_drain",
            "local_close",
            "cloud_close",
        ], order

    @pytest.mark.asyncio
    async def test_newsletter_audit_row_survives_close(self, monkeypatch):
        """End-to-end repro of the teardown race.

        A newsletter spawned fire-and-forget moments before
        ``DatabaseService.close()`` (exactly what the Prefect auto-publish
        flow and the CLI publish paths do) must still complete AND land its
        ``newsletter_campaign_sent`` audit row before the pools close.
        """
        from services.database_service import DatabaseService

        local_pool = _FakePool("local")
        cloud_pool = _FakePool("cloud")

        svc = DatabaseService(database_url="x", site_config=SiteConfig())
        svc.pool = cloud_pool
        svc.local_pool = local_pool

        audit_mod.init_global_audit_logger(local_pool)

        async def slow_send(pool, title, excerpt, slug, site_config=None):
            # Still "emailing" when close() begins — batches sleep between
            # sends in the real service.
            await asyncio.sleep(0.05)
            return {"sent": 1, "failed": 0, "skipped": 0, "total_subscribers": 1}

        monkeypatch.setattr(
            "services.newsletter_service.send_post_newsletter", slow_send
        )

        publish_service._spawn_background(
            publish_service._send_post_newsletter_bg(
                svc, SiteConfig(), "Title", "Excerpt", "slug-1"
            ),
            name="send_newsletter(test)",
        )
        # Let the task start and reach its in-flight await.
        await asyncio.sleep(0)

        await svc.close()

        newsletter_rows = [
            args
            for (sql, args) in local_pool.executed
            if args and args[0] == "newsletter_campaign_sent"
        ]
        assert newsletter_rows, (
            "newsletter_campaign_sent audit row was lost at pool teardown — "
            "close() must drain publish background tasks first"
        )
