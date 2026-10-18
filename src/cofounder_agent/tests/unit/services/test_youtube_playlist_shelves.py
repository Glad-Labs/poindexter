"""Playlist shelves: each configured playlist shows as a section on the channel page.

Pins: the adapter adds a singlePlaylist section only for a playlist that exists
and has none, reports without writing on a dry run, and sync_shelves follows
the setting's order and its off switch; a placement that added a video asks for
shelves, one that added nothing does not.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

from poindexter.services.site_config import SiteConfig

RULES = json.dumps([
    {"title": "Local AI", "keywords": ["vram"]},
    {"title": "News", "keywords": ["openai"]},
    {"title": "Unused", "keywords": ["zzz"]},
])


def _youtube(playlists, shown):
    yt = MagicMock()
    yt.playlists.return_value.list.return_value.execute.return_value = {
        "items": [{"id": pid, "snippet": {"title": t}} for t, pid in playlists.items()],
    }
    yt.channelSections.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"playlists": shown}}] if shown else [],
    }
    return yt


def _run(yt, titles, apply):
    from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

    pkg = types.ModuleType("googleapiclient")
    discovery = types.ModuleType("googleapiclient.discovery")
    discovery.build = MagicMock(return_value=yt)  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"googleapiclient": pkg, "googleapiclient.discovery": discovery}):
        return YouTubePublishAdapter._ensure_shelves_blocking(credentials=object(), titles=titles, apply=apply)


def test_adds_only_missing_shelves_for_existing_playlists():
    yt = _youtube({"Local AI": "PL1", "News": "PL2"}, shown=["PL1"])
    out = _run(yt, ["Local AI", "News", "Unused"], apply=True)
    assert out == {"Local AI": "already shown", "News": "added", "Unused": "no playlist yet"}
    body = yt.channelSections.return_value.insert.call_args.kwargs["body"]
    assert body == {"snippet": {"type": "singlePlaylist"}, "contentDetails": {"playlists": ["PL2"]}}
    assert yt.channelSections.return_value.insert.call_count == 1


def test_dry_run_writes_nothing():
    yt = _youtube({"Local AI": "PL1"}, shown=[])
    assert _run(yt, ["Local AI"], apply=False) == {"Local AI": "would add"}
    yt.channelSections.return_value.insert.assert_not_called()


async def test_sync_shelves_follows_the_setting():
    from poindexter.services import youtube_playlists as ypl

    adapter = MagicMock()
    adapter.ensure_playlist_shelves = AsyncMock(return_value=(True, "ok", {}))
    with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter):
        await ypl.sync_shelves(SiteConfig(initial_config={"youtube_playlists": RULES}), apply=False)
    kw = adapter.ensure_playlist_shelves.await_args.kwargs
    assert kw["titles"] == ["Local AI", "News", "Unused"] and kw["apply"] is False


async def test_sync_shelves_off_switch():
    from poindexter.services import youtube_playlists as ypl

    with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter") as cls:
        ok, detail, _ = await ypl.sync_shelves(SiteConfig(initial_config={
            "youtube_playlists": RULES, "youtube_playlist_shelves_enabled": "false",
        }))
    assert ok and "off" in detail
    cls.assert_not_called()


def _pool():
    conn = MagicMock()
    conn.execute = AsyncMock()
    pool = MagicMock()

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    pool.acquire = lambda: _Acq()
    return pool


async def test_an_added_video_asks_for_shelves_and_a_no_op_does_not():
    from poindexter.services import youtube_playlists as ypl

    for outcome, expect_call in (({"News": "added"}, True), ({"News": "already in"}, False)):
        adapter = MagicMock()
        adapter.add_to_playlists = AsyncMock(return_value=(True, "ok", outcome))
        with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter), \
             patch.object(ypl, "sync_shelves", AsyncMock(return_value=(True, "ok", {}))) as shelves:
            await ypl.playlists_after_upload(
                _pool(), SiteConfig(initial_config={"youtube_playlists": RULES}),
                asset_id="00000000-0000-0000-0000-000000000001", video_id="V", text="openai news",
            )
        assert shelves.await_count == (1 if expect_call else 0), outcome
