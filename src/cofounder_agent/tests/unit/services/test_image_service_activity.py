"""image_service.generate_image is bracketed by a best-effort kind='media'
live_activity row so Z-Image renders show in the console pulse. The ledger is
observability — it must never change the render's own bool result, and a
ledger failure must not break the render."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services import image_service as image_service_mod
from services.image_service import ImageGenOutcome, ImageService
from services.site_config import SiteConfig

pytestmark = pytest.mark.asyncio


def _svc():
    # SiteConfig with no real pool → track()'s begin swallows → aid None →
    # the bracket is a silent no-op, but the wrapper still delegates + returns.
    return ImageService(SiteConfig(initial_config={}))


def _fake_ctx(seen):
    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(
                update=AsyncMock(),
                fail=lambda: seen.__setitem__("failed", True),
            )

        async def __aexit__(self, *exc):
            return False

    return _FakeCtx()


async def test_generate_image_opens_media_row_and_finishes_ok():
    svc = _svc()
    seen = {}

    def fake_track(pool, **kw):
        seen["kw"] = kw
        return _fake_ctx(seen)

    with patch.object(image_service_mod.live_activity, "track", fake_track), patch.object(
        ImageService, "_generate_image_impl", AsyncMock(return_value=ImageGenOutcome(True))
    ):
        ok = await svc.generate_image("a cat", "/tmp/x.png", task_id="t9")
    assert ok is True
    assert seen["kw"]["kind"] == "media"
    assert seen["kw"]["ref_id"] == "t9"
    assert "cat" in seen["kw"]["title"]
    assert "failed" not in seen  # a success does NOT call handle.fail()


async def test_generate_image_marks_fail_when_impl_returns_false():
    svc = _svc()
    seen = {}
    with patch.object(image_service_mod.live_activity, "track", lambda pool, **kw: _fake_ctx(seen)), patch.object(
        ImageService, "_generate_image_impl", AsyncMock(return_value=ImageGenOutcome(False, "server_error", "boom"))
    ):
        ok = await svc.generate_image("a cat", "/tmp/x.png")
    assert ok is False
    assert seen.get("failed") is True


async def test_generate_image_best_effort_when_ledger_cannot_open():
    """With no DB pool the real track()/begin swallows (aid None) and the whole
    bracket is a silent no-op — the render still runs and returns its own bool.
    The ledger is never load-bearing."""
    svc = _svc()  # pool-less SiteConfig → real begin() swallows → None id
    with patch.object(ImageService, "_generate_image_impl", AsyncMock(return_value=ImageGenOutcome(True))) as impl:
        ok = await svc.generate_image("a cat", "/tmp/x.png", task_id="t9")
    assert ok is True
    impl.assert_awaited_once()
