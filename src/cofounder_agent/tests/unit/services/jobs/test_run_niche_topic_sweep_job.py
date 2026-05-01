"""Unit tests for ``services/jobs/run_niche_topic_sweep.py``.

The job iterates active niches and calls ``TopicBatchService.run_sweep``
on each. NicheService and TopicBatchService are mocked — the test
focus is on the loop semantics (skip / new / error / notify) rather
than re-validating the underlying services.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.jobs.run_niche_topic_sweep import RunNicheTopicSweepJob


def _niche(slug: str = "glad-labs"):
    return SimpleNamespace(id=uuid4(), slug=slug)


def _snapshot(candidate_count: int = 5):
    return SimpleNamespace(
        id=uuid4(),
        niche_id=uuid4(),
        status="open",
        candidate_count=candidate_count,
        expires_at=None,
    )


@pytest.mark.unit
class TestMetadata:
    def test_name(self):
        assert RunNicheTopicSweepJob.name == "run_niche_topic_sweep"

    def test_schedule_30m(self):
        assert "30 minutes" in RunNicheTopicSweepJob.schedule

    def test_idempotent(self):
        assert RunNicheTopicSweepJob.idempotent is True


@pytest.mark.unit
@pytest.mark.asyncio
class TestRun:
    async def test_no_active_niches_returns_ok_zero_changes(self):
        job = RunNicheTopicSweepJob()
        ns_cls = MagicMock()
        ns_cls.return_value.list_active = AsyncMock(return_value=[])
        with patch("services.niche_service.NicheService", ns_cls):
            result = await job.run(MagicMock(), {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no active niches" in result.detail

    async def test_skips_when_run_sweep_returns_none(self):
        """run_sweep returns None when cadence floor not elapsed or batch already open."""
        job = RunNicheTopicSweepJob()
        n = _niche()
        ns_cls = MagicMock()
        ns_cls.return_value.list_active = AsyncMock(return_value=[n])
        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(return_value=None)
        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
        ):
            result = await job.run(MagicMock(), {"notify_on_new_batch": False})
        assert result.ok is True
        assert result.changes_made == 0
        assert "skipped=1" in result.detail
        assert "new_batches=0" in result.detail

    async def test_new_batch_increments_changes_and_notifies(self):
        job = RunNicheTopicSweepJob()
        n = _niche()
        snap = _snapshot(candidate_count=5)
        ns_cls = MagicMock()
        ns_cls.return_value.list_active = AsyncMock(return_value=[n])

        notify_calls: list = []

        async def fake_notify(pool, niche, snapshot):
            notify_calls.append((niche.slug, snapshot.id))

        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(return_value=snap)
        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
            patch("services.jobs.run_niche_topic_sweep._notify_new_batch", fake_notify),
        ):
            result = await job.run(MagicMock(), {"notify_on_new_batch": True})

        assert result.ok is True
        assert result.changes_made == 1
        assert "new_batches=1" in result.detail
        assert notify_calls == [(n.slug, snap.id)]

    async def test_notify_disabled_via_config_skips_notify_call(self):
        job = RunNicheTopicSweepJob()
        n = _niche()
        snap = _snapshot()
        ns_cls = MagicMock()
        ns_cls.return_value.list_active = AsyncMock(return_value=[n])
        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(return_value=snap)

        notify_calls: list = []

        async def fake_notify(pool, niche, snapshot):
            notify_calls.append("called")

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
            patch("services.jobs.run_niche_topic_sweep._notify_new_batch", fake_notify),
        ):
            result = await job.run(MagicMock(), {"notify_on_new_batch": False})

        assert result.changes_made == 1
        assert notify_calls == []  # notify was suppressed

    async def test_per_niche_exception_continues_loop(self):
        """A failing niche shouldn't abort the loop — other niches still run."""
        job = RunNicheTopicSweepJob()
        bad = _niche(slug="broken")
        good = _niche(slug="ok")
        snap = _snapshot()

        ns_cls = MagicMock()
        ns_cls.return_value.list_active = AsyncMock(return_value=[bad, good])

        async def _run_sweep(*, niche_id):
            if niche_id == bad.id:
                raise RuntimeError("synthetic failure")
            return snap

        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(side_effect=_run_sweep)

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
            patch(
                "services.jobs.run_niche_topic_sweep._notify_new_batch",
                new_callable=AsyncMock,
            ),
        ):
            result = await job.run(MagicMock(), {})

        assert result.ok is False  # one niche errored
        assert "errors=1" in result.detail
        assert "new_batches=1" in result.detail  # the other niche still ran

    async def test_notify_failure_does_not_break_sweep(self):
        """Notification is best-effort — a Telegram outage must not break
        the discovery cycle."""
        job = RunNicheTopicSweepJob()
        n = _niche()
        snap = _snapshot()
        ns_cls = MagicMock()
        ns_cls.return_value.list_active = AsyncMock(return_value=[n])
        svc_cls = MagicMock()
        svc_cls.return_value.run_sweep = AsyncMock(return_value=snap)

        async def boom(pool, niche, snapshot):
            raise RuntimeError("telegram down")

        with (
            patch("services.niche_service.NicheService", ns_cls),
            patch("services.topic_batch_service.TopicBatchService", svc_cls),
            patch("services.jobs.run_niche_topic_sweep._notify_new_batch", boom),
        ):
            result = await job.run(MagicMock(), {"notify_on_new_batch": True})

        # changes_made still records the new batch even though notify failed.
        assert result.ok is True
        assert result.changes_made == 1
