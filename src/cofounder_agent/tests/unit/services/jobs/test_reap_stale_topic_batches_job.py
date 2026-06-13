"""Tests for ``services/jobs/reap_stale_topic_batches.py``.

Roundtrips against the real Postgres test DB via the ``db_pool`` fixture
(``tests/unit/conftest.py``); skipped automatically when no live Postgres
DSN is reachable. The reaper's selection is raw SQL (active-niche join +
age filter + ``expires_at`` comparison computed in-DB), and the reap path
calls the real ``TopicBatchService.reject_batch`` UPDATE, so a real schema
is required to exercise the behaviour.

``emit_finding`` is monkeypatched to capture findings instead of writing to
``audit_log`` — the unit under test is the alert/reap decision, not the
findings persistence path (covered by ``utils.findings`` tests).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.jobs.reap_stale_topic_batches import ReapStaleTopicBatchesJob
from services.niche_service import NicheService
from services.site_config import SiteConfig

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _enable_reaper(db_pool) -> None:
    """Flip the master switch on in the test DB (default is off)."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO app_settings (key, value, category, description)
            VALUES ('topic_batch_reaper_enabled', 'true', 'testing',
                    'enabled for reap_stale_topic_batches job tests')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
        )


async def _seed_batch(
    db_pool, niche_id, *, age_hours: float, expires_in_hours: float,
):
    """Insert one ``open`` batch with an explicit ``created_at`` (so we can
    age it past the stuck threshold) and ``expires_at`` (so we can place it
    inside or outside its review window). Returns the batch id."""
    now = datetime.now(timezone.utc)
    created = now - timedelta(hours=age_hours)
    expires = now + timedelta(hours=expires_in_hours)
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO topic_batches (niche_id, status, created_at, expires_at) "
            "VALUES ($1, 'open', $2, $3) RETURNING id",
            niche_id, created, expires,
        )


async def _batch_status(db_pool, batch_id) -> str:
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT status FROM topic_batches WHERE id = $1", batch_id,
        )


@pytest.fixture
def _capture_findings(monkeypatch):
    """Capture ``emit_finding`` calls (kwargs dicts) instead of writing to
    audit_log. ``emit_finding`` is imported into the job module's namespace,
    so we patch it there."""
    calls: list[dict] = []

    def _capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "services.jobs.reap_stale_topic_batches.emit_finding", _capture,
    )
    return calls


@pytest.mark.unit
class TestMetadata:
    def test_name(self):
        assert ReapStaleTopicBatchesJob.name == "reap_stale_topic_batches"

    def test_schedule_hourly(self):
        assert ReapStaleTopicBatchesJob.schedule == "every 60 minutes"

    def test_idempotent(self):
        assert ReapStaleTopicBatchesJob.idempotent is True

    def test_registered_in_core_samples(self):
        # _SAMPLES is the sole load path in production (the worker
        # bind-mounts source rather than pip-installing), so a job missing
        # here never runs — see finding #189 / test_registry_completeness.
        from plugins.registry import get_core_samples

        jobs = get_core_samples().get("jobs", [])
        assert any(
            getattr(j, "name", None) == "reap_stale_topic_batches" for j in jobs
        )


async def test_fresh_batch_is_not_flagged(db_pool, _capture_findings):
    """A batch younger than ``topic_batch_stuck_hours`` (default 24) is
    healthy — no finding, no reap, no change."""
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="reaper-fresh", name="Fresh", batch_size=5)
    batch_id = await _seed_batch(db_pool, n.id, age_hours=1, expires_in_hours=168)

    result = await ReapStaleTopicBatchesJob().run(
        db_pool, {"_site_config": SiteConfig()},
    )

    assert result.changes_made == 0
    assert _capture_findings == []
    assert await _batch_status(db_pool, batch_id) == "open"


async def test_stuck_batch_alerts_but_does_not_reap_when_disabled(
    db_pool, _capture_findings,
):
    """Default path: reaper disabled. A stuck batch is alerted (warn, so it
    pages) but left ``open`` — zero mutation on merge."""
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="reaper-stuck-off", name="StuckOff", batch_size=5)
    # Expired AND stuck — but reaper is OFF, so nothing is reaped.
    batch_id = await _seed_batch(db_pool, n.id, age_hours=48, expires_in_hours=-1)

    result = await ReapStaleTopicBatchesJob().run(
        db_pool, {"_site_config": SiteConfig()},
    )

    assert result.changes_made == 0
    assert await _batch_status(db_pool, batch_id) == "open"
    assert len(_capture_findings) == 1
    f = _capture_findings[0]
    assert f["kind"] == "topic_batch_stuck"
    assert f["severity"] == "warn"  # pages — the niche is still wedged
    assert f["dedup_key"] == f"topic_batch_stuck:{batch_id}"
    assert f["extra"]["reaped"] is False
    assert f["extra"]["is_expired"] is True


async def test_expired_stuck_batch_is_reaped_when_enabled(
    db_pool, _capture_findings,
):
    """Self-heal: reaper enabled + batch past ``expires_at`` → flipped to
    ``expired`` (frees the one-open-batch-per-niche slot) and the finding
    drops to ``info`` (no page — it self-healed)."""
    await _enable_reaper(db_pool)
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="reaper-expired", name="Expired", batch_size=5)
    batch_id = await _seed_batch(db_pool, n.id, age_hours=200, expires_in_hours=-2)

    result = await ReapStaleTopicBatchesJob().run(
        db_pool, {"_site_config": SiteConfig()},
    )

    assert result.changes_made == 1
    assert await _batch_status(db_pool, batch_id) == "expired"
    assert len(_capture_findings) == 1
    f = _capture_findings[0]
    assert f["severity"] == "info"  # self-healed → dashboard-only, no page
    assert f["extra"]["reaped"] is True


async def test_non_expired_stuck_batch_is_not_reaped_even_when_enabled(
    db_pool, _capture_findings,
):
    """Conservative rule: a stuck but NON-expired batch (still within its
    review window — could be a manual-review queue or a throttle-deferred
    auto-resolve) is alerted (warn) but never reaped, so we don't discard a
    batch that may still legitimately resolve."""
    await _enable_reaper(db_pool)
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="reaper-live", name="Live", batch_size=5)
    # Old enough to be "stuck", but expires_at is still in the future.
    batch_id = await _seed_batch(db_pool, n.id, age_hours=48, expires_in_hours=120)

    result = await ReapStaleTopicBatchesJob().run(
        db_pool, {"_site_config": SiteConfig()},
    )

    assert result.changes_made == 0
    assert await _batch_status(db_pool, batch_id) == "open"
    assert len(_capture_findings) == 1
    f = _capture_findings[0]
    assert f["severity"] == "warn"
    assert f["extra"]["reaped"] is False
    assert f["extra"]["is_expired"] is False


async def test_inactive_niche_batch_is_ignored(db_pool, _capture_findings):
    """Scope is structural, not slug-based: a stuck (even expired) batch on
    an INACTIVE niche is ignored entirely — inactive niches aren't swept, so
    their batches wedge nothing. Reaper enabled to prove the skip is the
    active-niche filter, not the master switch."""
    await _enable_reaper(db_pool)
    nsvc = NicheService(db_pool)
    n = await nsvc.create(slug="reaper-inactive", name="Inactive", batch_size=5)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE niches SET active = FALSE WHERE id = $1", n.id,
        )
    batch_id = await _seed_batch(db_pool, n.id, age_hours=200, expires_in_hours=-2)

    result = await ReapStaleTopicBatchesJob().run(
        db_pool, {"_site_config": SiteConfig()},
    )

    assert result.changes_made == 0
    assert _capture_findings == []
    assert await _batch_status(db_pool, batch_id) == "open"
