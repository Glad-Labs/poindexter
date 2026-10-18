"""A YouTube backfill stops at the first quota error instead of failing every video.

2026-10-18: a caption backfill (450 units a video) spent the day's 10,000-unit
quota, and the playlist backfill that followed failed on every later video,
each with the same 403. Every call after the first quota error is wasted, and
the day's uploads (1,600 units each) need whatever is left.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services.publish_adapters.youtube import QUOTA_UNITS, is_quota_exhausted
from poindexter.services.site_config import SiteConfig

# The message Google returned on 2026-10-18, verbatim apart from truncation.
REAL_QUOTA_ERROR = (
    '<HttpError 403 when requesting https://youtube.googleapis.com/youtube/v3/playlistItems'
    '?part=snippet&alt=json returned "The request cannot be completed because you have '
    'exceeded your <a href="/youtube/v3/getting-started#quota">quota</a>.". Details: '
    "\"[{'message': 'The request cannot be completed because you have exceeded your "
    "<a href=\\\"/youtube/v3/getting-started#quota\\\">quota</a>.', 'domain': 'youtube.quota', "
    "'reason': 'quotaExceeded'}]\">"
)


@pytest.mark.parametrize("text, expected", [
    (REAL_QUOTA_ERROR, True),
    ("the request cannot be completed because you have exceeded your quota", True),
    ("reason: dailyLimitExceeded", True),
    ("<HttpError 409 ... SERVICE_UNAVAILABLE>", False),
    ("the token lacks youtube.force-ssl", False),
    (None, False),
])
def test_is_quota_exhausted(text, expected):
    assert is_quota_exhausted(text) is expected


def test_the_unit_table():
    assert QUOTA_UNITS["videos.insert"] == 1600
    assert QUOTA_UNITS["captions.insert"] + QUOTA_UNITS["captions.list"] == 450


def _pool(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()
    pool = MagicMock()

    class _Acq:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    pool.acquire = lambda: _Acq()
    return pool


async def test_captions_stop_at_the_quota(tmp_path):
    from poindexter.services import youtube_captions as yc

    srt = tmp_path / "v.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    rows = [{"id": f"00000000-0000-0000-0000-00000000000{i}", "type": "video", "storage_path": "/v.mp4",
             "url": "", "task_id": "t", "video_id": f"V{i}", "caption_status": None, "title": f"T{i}"} for i in range(3)]
    adapter = MagicMock()
    adapter.set_captions = AsyncMock(return_value=(False, REAL_QUOTA_ERROR))
    with patch.object(yc, "ensure_srt", AsyncMock(return_value=(str(srt), ""))), patch(
        "poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter,
    ):
        outs = await yc.backfill_captions(_pool(rows), SiteConfig(initial_config={}), apply=True)
    assert adapter.set_captions.await_count == 1
    assert outs[0].failed
    assert [o.action for o in outs[1:]] == [yc.QUOTA_NOT_TRIED] * 2


async def test_playlists_stop_at_the_quota():
    import json

    from poindexter.services import youtube_playlists as ypl

    rules = json.dumps([{"title": "News", "keywords": ["openai"]}])
    rows = [{"id": f"00000000-0000-0000-0000-00000000000{i}", "type": "video", "video_id": f"V{i}",
             "playlist_status": None, "title": "OpenAI", "post_title": "OpenAI", "seo_keywords": ""} for i in range(3)]
    adapter = MagicMock()
    adapter.add_to_playlists = AsyncMock(return_value=(False, REAL_QUOTA_ERROR, {}))
    with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter):
        outs = await ypl.backfill_playlists(
            _pool(rows), SiteConfig(initial_config={"youtube_playlists": rules}), apply=True,
        )
    assert adapter.add_to_playlists.await_count == 1
    assert [o.action for o in outs[1:]] == [ypl.QUOTA_NOT_TRIED] * 2


async def test_other_failures_do_not_stop_the_run():
    import json

    from poindexter.services import youtube_playlists as ypl

    rules = json.dumps([{"title": "News", "keywords": ["openai"]}])
    rows = [{"id": f"00000000-0000-0000-0000-00000000000{i}", "type": "video", "video_id": f"V{i}",
             "playlist_status": None, "title": "OpenAI", "post_title": "OpenAI", "seo_keywords": ""} for i in range(2)]
    adapter = MagicMock()
    adapter.add_to_playlists = AsyncMock(return_value=(False, "HttpError 409 SERVICE_UNAVAILABLE", {}))
    with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter):
        await ypl.backfill_playlists(_pool(rows), SiteConfig(initial_config={"youtube_playlists": rules}), apply=True)
    assert adapter.add_to_playlists.await_count == 2


def test_playlist_estimate():
    from poindexter.services.youtube_playlists import PlaylistOutcome, estimate_quota_units

    outs = [
        PlaylistOutcome("A", "long", "t", playlists=["X", "Y"], action="would add — review, then --apply"),
        PlaylistOutcome("B", "long", "t", playlists=[], action="no playlist matched"),
    ]
    assert estimate_quota_units(outs) == 1 + 2 * 51


async def test_metadata_sync_stops_at_the_quota(monkeypatch):
    from poindexter.services import youtube_metadata_sync as ys

    rows = [{"video_id": f"V{i}", "post_id": f"p{i}", "medium": "video", "title": "T", "excerpt": "",
             "content": "", "slug": "s", "seo_keywords": ""} for i in range(3)]
    monkeypatch.setattr(ys, "_load_targets", AsyncMock(return_value=rows))
    result = MagicMock(success=False, error=REAL_QUOTA_ERROR, status="")
    adapter = MagicMock()
    adapter.update_metadata = AsyncMock(return_value=result)
    with patch("poindexter.services.publish_adapters.youtube.YouTubePublishAdapter", return_value=adapter):
        outs = await ys.sync_youtube_metadata(object(), SiteConfig(initial_config={"site_url": "https://x.io"}), apply=True)
    assert adapter.update_metadata.await_count == 1
    assert [o.error for o in outs[1:]] == [ys.QUOTA_NOT_TRIED] * 2
