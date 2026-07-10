"""live_activity writes swallow their own errors — never break the caller."""
import pytest

from services import live_activity

pytestmark = pytest.mark.asyncio


class _BoomPool:
    def acquire(self):  # not even an async ctx — any use raises
        raise RuntimeError("db down")


async def test_begin_swallows_and_returns_none():
    assert await live_activity.begin(_BoomPool(), kind="job", ref_id="j", title="t") is None


async def test_update_finish_swallow():
    await live_activity.update(_BoomPool(), 1, step="s")  # must not raise
    await live_activity.finish(_BoomPool(), 1)  # must not raise
