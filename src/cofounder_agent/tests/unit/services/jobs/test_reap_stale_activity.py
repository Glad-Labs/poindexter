"""ReapStaleActivityJob — silent, reads the reaper window, delegates to reap_stale."""
import pytest

from services.jobs.reap_stale_activity import ReapStaleActivityJob

pytestmark = pytest.mark.asyncio


class _Cfg:
    def get(self, k, d=None):
        return {"live_activity_reaper_seconds": "300"}.get(k, d)


async def test_reaper_is_silent_and_calls_reap(monkeypatch):
    seen = {}

    async def fake_reap(pool, *, reaper_seconds):
        seen["s"] = reaper_seconds
        return 2

    monkeypatch.setattr("services.jobs.reap_stale_activity.reap_stale", fake_reap)
    job = ReapStaleActivityJob()
    assert job.activity_silent is True  # the reaper must not log itself every minute
    res = await job.run(pool=None, config={"_site_config": _Cfg()})
    assert res.ok and res.changes_made == 2 and seen["s"] == 300


async def test_reaper_defaults_window_without_site_config(monkeypatch):
    seen = {}

    async def fake_reap(pool, *, reaper_seconds):
        seen["s"] = reaper_seconds
        return 0

    monkeypatch.setattr("services.jobs.reap_stale_activity.reap_stale", fake_reap)
    res = await ReapStaleActivityJob().run(pool=None, config={})
    assert res.ok and seen["s"] == 300  # falls back to 300 when no _site_config
