"""Unit tests for EvaluateEarnedAutonomyGate2Job (#531).

The job queries pending Gate-2 rows grouped by (niche_slug, medium), re-checks
earned-autonomy eligibility for each combo, and bulk-promotes rows that now
meet the threshold. Tests use patched ``_earned_autonomy_check`` so the
eligibility logic is tested separately in test_media_approval_service.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.evaluate_earned_autonomy_gate2 import EvaluateEarnedAutonomyGate2Job
from services.site_config import SiteConfig


def _sc(**overrides):
    base = {"media_pipeline_trigger_enabled": "false"}
    base.update(overrides)
    return SiteConfig(initial_config=base)


def _make_pool(combos=None, promoted_rows=None):
    """Return a pool mock. ``fetch`` returns the pending-combos list; the
    acquire() context manager yields a ``conn`` whose ``fetch`` returns the
    PROMOTE_SQL result rows."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=list(promoted_rows or []))

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=list(combos or []))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)
    return pool, conn


@pytest.mark.asyncio
async def test_dormant_when_flag_off():
    """Job is a no-op when media_pipeline_trigger_enabled is false."""
    job = EvaluateEarnedAutonomyGate2Job()
    pool, _ = _make_pool()
    out = await job.run(pool, {"_site_config": _sc()})
    assert out.ok
    assert out.changes_made == 0
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_no_site_config_skips():
    job = EvaluateEarnedAutonomyGate2Job()
    pool, _ = _make_pool()
    out = await job.run(pool, {})
    assert out.ok
    assert out.changes_made == 0


@pytest.mark.asyncio
async def test_no_pending_rows_returns_zero():
    """No pending combos → no promotions."""
    job = EvaluateEarnedAutonomyGate2Job()
    pool, _ = _make_pool(combos=[])
    out = await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})
    assert out.ok
    assert out.changes_made == 0
    assert "no pending" in out.detail


@pytest.mark.asyncio
async def test_promotes_eligible_combo():
    """A combo that passes _earned_autonomy_check gets its pending row promoted."""
    job = EvaluateEarnedAutonomyGate2Job()
    pool, conn = _make_pool(
        combos=[{"niche_slug": "glad-labs", "medium": "video"}],
        promoted_rows=[{"id": "ap-1", "post_id": "post-1"}],
    )
    with patch(
        "services.jobs.evaluate_earned_autonomy_gate2._earned_autonomy_check",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "services.jobs.evaluate_earned_autonomy_gate2.emit_finding",
        return_value=None,
    ) as mock_emit:
        out = await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})

    assert out.ok
    assert out.changes_made == 1
    # Confirm PROMOTE_SQL was executed on the conn
    conn.fetch.assert_awaited_once()
    # Confirm a finding was emitted for the promoted row
    mock_emit.assert_called_once()
    call_kwargs = mock_emit.call_args.kwargs
    assert call_kwargs["kind"] == "media_earned_autonomy_granted"
    assert "glad-labs" in call_kwargs["title"]


@pytest.mark.asyncio
async def test_skips_ineligible_combo():
    """A combo that fails _earned_autonomy_check stays pending — no UPDATE."""
    job = EvaluateEarnedAutonomyGate2Job()
    pool, conn = _make_pool(
        combos=[{"niche_slug": "glad-labs", "medium": "video"}],
    )
    with patch(
        "services.jobs.evaluate_earned_autonomy_gate2._earned_autonomy_check",
        new_callable=AsyncMock,
        return_value=False,
    ):
        out = await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})

    assert out.ok
    assert out.changes_made == 0
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_combos_only_eligible_promoted():
    """Two combos: one eligible, one not. Only the eligible one is promoted."""
    job = EvaluateEarnedAutonomyGate2Job()
    combos = [
        {"niche_slug": "glad-labs", "medium": "video"},
        {"niche_slug": "glad-labs", "medium": "podcast"},
    ]
    pool, conn = _make_pool(
        combos=combos,
        promoted_rows=[{"id": "ap-2", "post_id": "post-2"}],
    )
    # eligible for video only
    eligibility = {"video": True, "podcast": False}

    async def _check(_db, _slug, medium):
        return eligibility[medium]

    with patch(
        "services.jobs.evaluate_earned_autonomy_gate2._earned_autonomy_check",
        side_effect=_check,
    ), patch(
        "services.jobs.evaluate_earned_autonomy_gate2.emit_finding",
        return_value=None,
    ):
        out = await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})

    assert out.ok
    assert out.changes_made == 1
    # Only one fetch call to conn (the video promotion), not two
    assert conn.fetch.await_count == 1


@pytest.mark.asyncio
async def test_eligibility_check_exception_continues():
    """If _earned_autonomy_check raises for one combo, the job logs and continues."""
    job = EvaluateEarnedAutonomyGate2Job()
    combos = [
        {"niche_slug": "broken", "medium": "video"},
        {"niche_slug": "glad-labs", "medium": "podcast"},
    ]
    pool, conn = _make_pool(
        combos=combos,
        promoted_rows=[{"id": "ap-3", "post_id": "post-3"}],
    )

    async def _check(_db, slug, _medium):
        if slug == "broken":
            raise RuntimeError("db error")
        return True

    with patch(
        "services.jobs.evaluate_earned_autonomy_gate2._earned_autonomy_check",
        side_effect=_check,
    ), patch(
        "services.jobs.evaluate_earned_autonomy_gate2.emit_finding",
        return_value=None,
    ):
        out = await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})

    # Job should succeed overall and promote the non-broken combo
    assert out.ok
    assert out.changes_made == 1


@pytest.mark.asyncio
async def test_combos_query_failure_returns_not_ok():
    """If the pending-combos query raises, job returns ok=False."""
    job = EvaluateEarnedAutonomyGate2Job()
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("connection lost"))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=cm)

    out = await job.run(pool, {"_site_config": _sc(media_pipeline_trigger_enabled="true")})
    assert not out.ok
    assert "query failed" in out.detail
