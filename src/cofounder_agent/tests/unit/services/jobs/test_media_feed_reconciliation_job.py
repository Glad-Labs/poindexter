"""Tests for ``services.jobs.media_feed_reconciliation``.

The convergence watchdog for the podcast + video RSS feeds. Every published
feed rebuild is event-coupled and best-effort; miss one and the feed on R2 stays
wrong indefinitely. On 2026-07-18 the podcast feed held 71 episodes against 100
DB-eligible ones. This job makes the feed a function of DB *state* instead.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs import media_feed_reconciliation as job_mod
from services.media_feed_rebuild import FeedReconcileResult


def _site_config(enabled: bool = True) -> MagicMock:
    sc = MagicMock()
    sc.get_bool.side_effect = lambda k, d=False: (
        enabled if k == "media_feed_reconciliation_enabled" else d
    )
    sc.get.side_effect = lambda k, d=None: d
    return sc


def _result(
    medium: str, *, drifted: bool = False, healed: bool = False,
    refused: bool = False, rendered: int = 100, published: int = 100,
    error: str | None = None,
) -> FeedReconcileResult:
    return FeedReconcileResult(
        medium=medium, rendered_items=rendered, published_items=published,
        drifted=drifted, healed=healed, refused=refused, error=error,
    )


def _job() -> job_mod.MediaFeedReconciliationJob:
    return job_mod.MediaFeedReconciliationJob()


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_without_site_config() -> None:
    res = await _job().run(pool=MagicMock(), config={})
    assert res.ok is True
    assert res.changes_made == 0


@pytest.mark.asyncio
async def test_dormant_when_disabled() -> None:
    with patch.object(job_mod, "reconcile_feed", new=AsyncMock()) as rec:
        res = await _job().run(
            pool=MagicMock(), config={"_site_config": _site_config(enabled=False)},
        )
    assert res.ok is True
    assert "disabled" in res.detail.lower()
    rec.assert_not_awaited()


# ---------------------------------------------------------------------------
# Steady state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_walks_every_reconcilable_medium() -> None:
    """Podcast *and* video. media_distribute never rebuilt the video feed at
    all, so video is the lane most likely to have drifted."""
    with patch.object(
        job_mod, "reconcile_feed",
        new=AsyncMock(side_effect=lambda sc, m: _result(m)),
    ) as rec:
        await _job().run(
            pool=MagicMock(), config={"_site_config": _site_config()},
        )
    walked = {c.args[1] for c in rec.await_args_list}
    assert walked == {"podcast", "video"}


@pytest.mark.asyncio
async def test_in_sync_makes_no_changes_and_emits_no_finding() -> None:
    with patch.object(
        job_mod, "reconcile_feed",
        new=AsyncMock(side_effect=lambda sc, m: _result(m)),
    ), patch.object(job_mod, "emit_finding") as finding:
        res = await _job().run(
            pool=MagicMock(), config={"_site_config": _site_config()},
        )
    assert res.ok is True
    assert res.changes_made == 0
    finding.assert_not_called()


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_healed_drift_counts_and_emits_a_warn_finding() -> None:
    """Fail-loud contract, mirroring ``media_reconciliation``: even a successful
    self-heal tells the operator the upstream rebuild regressed."""
    with patch.object(
        job_mod, "reconcile_feed",
        new=AsyncMock(side_effect=lambda sc, m: (
            _result(m, drifted=True, healed=True, rendered=100, published=71)
            if m == "podcast" else _result(m)
        )),
    ), patch.object(job_mod, "emit_finding") as finding:
        res = await _job().run(
            pool=MagicMock(), config={"_site_config": _site_config()},
        )
    assert res.ok is True
    assert res.changes_made == 1
    finding.assert_called_once()
    kw = finding.call_args.kwargs
    assert kw["kind"] == "media_feed_drift"
    assert kw["severity"] == "warn"
    # The counts that make the finding actionable must be in the body.
    assert "71" in kw["body"] and "100" in kw["body"]


@pytest.mark.asyncio
async def test_refused_shrink_escalates_to_error_severity() -> None:
    """A refusal means the *renderer* is suspect (the feed route returns a
    valid empty feed when its DB query fails). That is strictly worse than
    ordinary drift and must not be reported at the same level."""
    with patch.object(
        job_mod, "reconcile_feed",
        new=AsyncMock(side_effect=lambda sc, m: (
            _result(
                m, drifted=True, healed=False, refused=True,
                rendered=0, published=100, error="render would drop 100 items",
            ) if m == "podcast" else _result(m)
        )),
    ), patch.object(job_mod, "emit_finding") as finding:
        res = await _job().run(
            pool=MagicMock(), config={"_site_config": _site_config()},
        )
    # Refusing is not a change — the published feed was deliberately untouched.
    assert res.changes_made == 0
    kw = finding.call_args.kwargs
    assert kw["kind"] == "media_feed_render_collapse"
    assert kw["severity"] == "error"


@pytest.mark.asyncio
async def test_finding_dedup_key_is_per_medium() -> None:
    """Podcast and video drift must not collapse into one dedup bucket, or the
    second lane's finding is silently dropped."""
    with patch.object(
        job_mod, "reconcile_feed",
        new=AsyncMock(side_effect=lambda sc, m: _result(
            m, drifted=True, healed=True, rendered=10, published=2,
        )),
    ), patch.object(job_mod, "emit_finding") as finding:
        await _job().run(
            pool=MagicMock(), config={"_site_config": _site_config()},
        )
    keys = {c.kwargs["dedup_key"] for c in finding.call_args_list}
    assert len(keys) == 2


# ---------------------------------------------------------------------------
# Observability + resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emits_per_medium_metrics() -> None:
    """``JobResult.metrics`` is what lands a ``job_run`` row in audit_log and
    therefore a Grafana panel. ``podcast_distribute`` returns none, which is
    exactly why its silence was invisible for weeks."""
    with patch.object(
        job_mod, "reconcile_feed",
        new=AsyncMock(side_effect=lambda sc, m: _result(
            m, drifted=True, healed=True, rendered=100, published=71,
        )),
    ), patch.object(job_mod, "emit_finding"):
        res = await _job().run(
            pool=MagicMock(), config={"_site_config": _site_config()},
        )
    assert res.metrics["podcast_rendered_items"] == 100
    assert res.metrics["podcast_published_items"] == 71
    assert res.metrics["feeds_healed"] == 2


@pytest.mark.asyncio
async def test_one_medium_failing_does_not_stop_the_other() -> None:
    async def _flaky(sc, medium):
        if medium == "podcast":
            raise RuntimeError("R2 down")
        return _result(medium, drifted=True, healed=True)

    with patch.object(job_mod, "reconcile_feed", new=AsyncMock(side_effect=_flaky)), \
            patch.object(job_mod, "emit_finding"):
        res = await _job().run(
            pool=MagicMock(), config={"_site_config": _site_config()},
        )
    assert res.ok is True
    assert res.changes_made == 1  # video still converged
