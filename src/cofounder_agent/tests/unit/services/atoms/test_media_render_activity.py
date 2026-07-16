"""_media_render.render_from_state brackets the video render with a best-effort
kind='media' live_activity row and threads a progress_cb into render_shot_list
so the console pulse shows live per-shot progress. Best-effort — a ledger
failure never changes the render's returned output."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.atoms import _media_render

pytestmark = pytest.mark.asyncio

_LONG = {
    "version": 1,
    "aspect": "16:9",
    "total_duration_s": 3.0,
    "shots": [
        {
            "idx": 0,
            "duration_s": 3.0,
            "intent": "x",
            "source": "image_gen",
            "prompt": "p",
            "narration_offset_s": 0.0,
        }
    ],
    "director_model": "ollama/test",
    "director_prompt_version": "v1",
    "director_decided_at": "2026-06-08T00:00:00+00:00",
}


class _FakeLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _patch_gpu_lock():
    with patch.object(_media_render.gpu, "lock", MagicMock(return_value=_FakeLock())):
        yield


def _ok_result(**kw):
    base = dict(
        success=True,
        output_path="/tmp/out.mp4",
        file_size_bytes=1,
        duration_s=3.0,
        shots_rendered=1,
        shots_total=1,
        shots_substituted=0,
        shots_carded=0,
        error=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


async def test_render_opens_media_row_and_passes_progress_cb():
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

    mock_render = AsyncMock(return_value=_ok_result())
    with patch.object(_media_render.live_activity, "track", fake_track), patch.object(
        _media_render, "render_shot_list", mock_render
    ):
        out = await _media_render.render_from_state(
            {"task_id": "t5", "video_shot_list": _LONG},
            shot_list_key="video_shot_list",
            output_key="long_video_path",
        )
    assert out == {"long_video_path": "/tmp/out.mp4"}
    assert seen["kw"]["kind"] == "media"
    assert seen["kw"]["ref_id"] == "t5"
    assert "Video" in seen["kw"]["title"]
    assert callable(mock_render.await_args.kwargs["progress_cb"])
    assert "failed" not in seen  # a full render does not call fail()


async def test_render_failure_marks_media_row_fail():
    failed = {}

    class _FakeCtx:
        async def __aenter__(self):
            return SimpleNamespace(
                update=AsyncMock(), fail=lambda: failed.__setitem__("x", True)
            )

        async def __aexit__(self, *exc):
            return False

    mock_render = AsyncMock(
        return_value=_ok_result(success=False, output_path=None, shots_rendered=0, error="none")
    )
    with patch.object(
        _media_render.live_activity, "track", lambda pool, **kw: _FakeCtx()
    ), patch.object(_media_render, "render_shot_list", mock_render), patch.object(
        _media_render, "emit_finding", MagicMock()
    ):
        out = await _media_render.render_from_state(
            {"task_id": "t5", "video_shot_list": _LONG},
            shot_list_key="video_shot_list",
            output_key="long_video_path",
        )
    assert out == {"long_video_path": ""}
    assert failed == {"x": True}


async def test_progress_cb_updates_the_row():
    """The threaded progress_cb delegates to the handle's update — so a
    render_shot_list that fires it bumps the media row."""
    updates = []

    class _FakeCtx:
        async def __aenter__(self):
            async def _upd(*, step=None, pct=None):
                updates.append((step, pct))

            return SimpleNamespace(update=_upd, fail=lambda: None)

        async def __aexit__(self, *exc):
            return False

    async def render_and_report(*, progress_cb, **kw):
        await progress_cb("shot 1/1", 99)
        return _ok_result()

    with patch.object(
        _media_render.live_activity, "track", lambda pool, **kw: _FakeCtx()
    ), patch.object(_media_render, "render_shot_list", render_and_report):
        await _media_render.render_from_state(
            {"task_id": "t5", "video_shot_list": _LONG},
            shot_list_key="video_shot_list",
            output_key="long_video_path",
        )
    assert updates == [("shot 1/1", 99)]
