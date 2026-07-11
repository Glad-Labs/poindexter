"""_narration_render.render_narration brackets TTS synth with a best-effort
kind='media' live_activity row (podcast + long/short video narration all share
this path). Best-effort — a ledger failure never changes the returned path, and
a synth failure still fail-softs to ''."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.atoms import _narration_render

pytestmark = pytest.mark.asyncio


class _Cfg:
    """SiteConfig stand-in: .get for CTA + heartbeat lookups, ._pool for the
    ledger seam. _pool is a bare object() with no .acquire, so the real
    begin() swallows -> None id -> the bracket is a silent no-op."""

    _pool = object()

    def get(self, k, d=None):
        return d


async def test_narration_opens_media_row_and_synthesizes():
    seen = {}

    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(
                update=AsyncMock(), fail=lambda: seen.__setitem__("failed", True)
            )

        async def __aexit__(self, *exc):
            return False

    def fake_track(pool, **kw):
        seen["kw"] = kw
        return _FakeCtx()

    fake_svc = SimpleNamespace(synthesize=AsyncMock(return_value=("/tmp/n.mp3", 12.0)))
    with patch.object(_narration_render.live_activity, "track", fake_track), patch(
        "services.podcast_service.PodcastService", return_value=fake_svc
    ):
        out = await _narration_render.render_narration(
            script="hello world",
            cta_key="media.cta.podcast",
            site_config=_Cfg(),
            task_id="t6",
            key="t6",
        )
    assert out == "/tmp/n.mp3"
    assert seen["kw"]["kind"] == "media"
    assert seen["kw"]["ref_id"] == "t6"
    assert "Narration" in seen["kw"]["title"]


async def test_empty_script_skips_the_row():
    """An empty script is a fail-soft no-op BEFORE any render — it must not
    open a media row (nothing is rendering)."""
    opened = {}
    with patch.object(
        _narration_render.live_activity,
        "track",
        lambda pool, **kw: opened.setdefault("x", True),
    ):
        out = await _narration_render.render_narration(
            script="   ",
            cta_key="media.cta.podcast",
            site_config=_Cfg(),
            task_id="t6",
            key="t6",
        )
    assert out == ""
    assert opened == {}


async def test_synth_failure_fail_softs_to_empty():
    """A TTS failure fail-softs to '' (never raises). Uses the REAL track with a
    pool-less config so begin() swallows to a no-op — proving the ledger is
    never load-bearing on the failure path."""
    fake_svc = SimpleNamespace(synthesize=AsyncMock(side_effect=RuntimeError("tts down")))
    with patch("services.podcast_service.PodcastService", return_value=fake_svc):
        out = await _narration_render.render_narration(
            script="hello",
            cta_key="media.cta.podcast",
            site_config=_Cfg(),
            task_id="t6",
            key="t6",
        )
    assert out == ""


async def test_best_effort_when_ledger_cannot_open():
    """With the real track()/begin swallowing (pool-less), a successful synth
    still returns its path — the ledger no-op never breaks the render."""
    fake_svc = SimpleNamespace(synthesize=AsyncMock(return_value=("/tmp/n.mp3", 3.0)))
    with patch("services.podcast_service.PodcastService", return_value=fake_svc):
        out = await _narration_render.render_narration(
            script="hello",
            cta_key="media.cta.podcast",
            site_config=_Cfg(),
            task_id="t6",
            key="t6",
        )
    assert out == "/tmp/n.mp3"
