"""Unit tests for the Stage-2 render atoms + their shared helper (Plan 4).

Covers ``media.render_long_video`` / ``media.render_short_video`` (the two
thin atoms) and the shared ``modules.content.atoms._media_render.render_from_state``
helper they delegate to.

The helper wires the EXISTING ``render_shot_list`` engine into graph state:
it rehydrates the shot-list dict, resolves the aspect-profile dims, threads
narration + ambient bed, emits a partial-render finding when a degraded video
would ship, and never raises (a render failure must not halt the graph).

We patch ``render_shot_list`` and ``emit_finding`` where they are imported in
``_media_render`` (the call-site module), per the standard mocking discipline.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.atoms import _media_render
from modules.content.atoms.media_render_long_video import run as run_long
from modules.content.atoms.media_render_short_video import run as run_short

# A minimal valid 16:9 long-form shot-list dict (rehydratable by
# VideoShotList.model_validate). One sdxl shot keeps it trivial.
_LONG_SHOT_LIST = {
    "version": 1,
    "aspect": "16:9",
    "total_duration_s": 3.0,
    "shots": [
        {
            "idx": 0,
            "duration_s": 3.0,
            "intent": "opening still",
            "source": "sdxl",
            "prompt": "a clean abstract gradient backdrop",
            "narration_offset_s": 0.0,
        },
    ],
    "director_model": "ollama/test-model",
    "director_prompt_version": "v1",
    "director_decided_at": "2026-06-08T00:00:00+00:00",
}

# Same but 9:16 short-form.
_SHORT_SHOT_LIST = {**_LONG_SHOT_LIST, "aspect": "9:16"}


def _ok_result(*, shots_rendered=1, shots_total=1, output_path="/tmp/out.mp4"):
    """A successful ShotListRenderResult-shaped object."""
    return SimpleNamespace(
        success=True,
        output_path=output_path,
        file_size_bytes=123,
        duration_s=3.0,
        shots_rendered=shots_rendered,
        shots_total=shots_total,
        error=None,
    )


def _fail_result():
    return SimpleNamespace(
        success=False,
        output_path=None,
        file_size_bytes=0,
        duration_s=0.0,
        shots_rendered=0,
        shots_total=1,
        error="no shots rendered",
    )


class TestRenderFromStateHelper:
    """The shared helper that both atoms delegate to."""

    @pytest.mark.asyncio
    async def test_success_returns_output_path_in_named_key(self):
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result(output_path="/tmp/long.mp4"))
        with patch.object(_media_render, "render_shot_list", mock_render):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": "/tmp/long.mp4"}
        assert mock_render.await_count == 1

    @pytest.mark.asyncio
    async def test_16_9_aspect_passes_long_dims(self):
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        kwargs = mock_render.await_args.kwargs
        assert kwargs["width"] == 1920
        assert kwargs["height"] == 1080

    @pytest.mark.asyncio
    async def test_9_16_aspect_passes_short_dims(self):
        state = {"task_id": "t-1", "short_shot_list": _SHORT_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="short_shot_list", output_key="short_video_path"
            )
        kwargs = mock_render.await_args.kwargs
        assert kwargs["width"] == 1080
        assert kwargs["height"] == 1920

    @pytest.mark.asyncio
    async def test_narration_and_ambient_threaded(self):
        state = {
            "task_id": "t-1",
            "video_shot_list": _LONG_SHOT_LIST,
            "podcast_audio_path": "/tmp/narration.mp3",
            "video_ambient_audio_path": "/tmp/amb.wav",
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        kwargs = mock_render.await_args.kwargs
        assert kwargs["audio_path"] == "/tmp/narration.mp3"
        assert kwargs["ambient_path"] == "/tmp/amb.wav"

    @pytest.mark.asyncio
    async def test_missing_shot_list_is_graceful_noop(self):
        """No shot-list in state → empty output, render_shot_list NOT called."""
        state = {"task_id": "t-1"}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": ""}
        mock_render.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_shot_list_is_graceful_noop(self):
        state = {"task_id": "t-1", "video_shot_list": None}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": ""}
        mock_render.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_shot_list_dict_returns_empty(self):
        """A malformed shot-list dict (ValidationError) → empty, no raise."""
        state = {"task_id": "t-1", "video_shot_list": {"not": "a shot list"}}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": ""}
        mock_render.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_render_emits_finding_and_still_returns_path(self):
        """shots_rendered < shots_total (but >=1) → emit a finding (a
        degraded video would otherwise ship silently), and still return
        the path. Redesign §9 / Gap C."""
        state = {"task_id": "t-99", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(
            return_value=_ok_result(
                shots_rendered=3, shots_total=8, output_path="/tmp/partial.mp4"
            )
        )
        mock_emit = MagicMock()
        with patch.object(_media_render, "render_shot_list", mock_render), patch.object(
            _media_render, "emit_finding", mock_emit
        ):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": "/tmp/partial.mp4"}
        assert mock_emit.call_count == 1
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "partial_render"
        assert kwargs["severity"] == "warn"
        assert kwargs["extra"]["shots_rendered"] == 3
        assert kwargs["extra"]["shots_total"] == 8

    @pytest.mark.asyncio
    async def test_full_render_emits_no_finding(self):
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(
            return_value=_ok_result(shots_rendered=1, shots_total=1)
        )
        mock_emit = MagicMock()
        with patch.object(_media_render, "render_shot_list", mock_render), patch.object(
            _media_render, "emit_finding", mock_emit
        ):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        mock_emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_render_failure_returns_empty_and_emits_finding_no_raise(self):
        """result.success=False → empty output, a render_failed finding,
        and NO exception (a failure must not halt the graph)."""
        state = {"task_id": "t-7", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_fail_result())
        mock_emit = MagicMock()
        with patch.object(_media_render, "render_shot_list", mock_render), patch.object(
            _media_render, "emit_finding", mock_emit
        ):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": ""}
        assert mock_emit.call_count == 1
        assert mock_emit.call_args.kwargs["kind"] == "render_failed"

    @pytest.mark.asyncio
    async def test_pool_resolved_from_database_service(self):
        pool_obj = object()
        db = SimpleNamespace(pool=pool_obj)
        state = {
            "task_id": "t-1",
            "video_shot_list": _LONG_SHOT_LIST,
            "database_service": db,
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert mock_render.await_args.kwargs["pool"] is pool_obj

    @pytest.mark.asyncio
    async def test_sdxl_url_from_site_config(self):
        site_config = MagicMock()
        site_config.get = MagicMock(return_value="http://sdxl-host:9836")
        state = {
            "task_id": "t-1",
            "video_shot_list": _LONG_SHOT_LIST,
            "site_config": site_config,
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert mock_render.await_args.kwargs["sdxl_url"] == "http://sdxl-host:9836"

    @pytest.mark.asyncio
    async def test_sdxl_url_default_when_no_site_config(self):
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert (
            mock_render.await_args.kwargs["sdxl_url"]
            == "http://host.docker.internal:9836"
        )


class TestRenderLongVideoAtom:
    """media.render_long_video — thin wrapper over render_from_state."""

    @pytest.mark.asyncio
    async def test_run_returns_long_video_path(self):
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result(output_path="/tmp/long.mp4"))
        with patch.object(_media_render, "render_shot_list", mock_render):
            out = await run_long(state)
        assert out == {"long_video_path": "/tmp/long.mp4"}

    @pytest.mark.asyncio
    async def test_run_uses_16_9_dims(self):
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await run_long(state)
        kwargs = mock_render.await_args.kwargs
        assert (kwargs["width"], kwargs["height"]) == (1920, 1080)

    @pytest.mark.asyncio
    async def test_run_missing_shot_list_noop(self):
        state = {"task_id": "t-1"}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            out = await run_long(state)
        assert out == {"long_video_path": ""}
        mock_render.assert_not_called()

    def test_atom_meta_shape(self):
        from modules.content.atoms.media_render_long_video import ATOM_META

        assert ATOM_META.name == "media.render_long_video"
        assert ATOM_META.requires == ("task_id",)
        assert ATOM_META.produces == ("long_video_path",)


class TestRenderShortVideoAtom:
    """media.render_short_video — reads short_shot_list, 9:16 dims."""

    @pytest.mark.asyncio
    async def test_run_returns_short_video_path(self):
        state = {"task_id": "t-1", "short_shot_list": _SHORT_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result(output_path="/tmp/short.mp4"))
        with patch.object(_media_render, "render_shot_list", mock_render):
            out = await run_short(state)
        assert out == {"short_video_path": "/tmp/short.mp4"}

    @pytest.mark.asyncio
    async def test_run_uses_9_16_dims(self):
        state = {"task_id": "t-1", "short_shot_list": _SHORT_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await run_short(state)
        kwargs = mock_render.await_args.kwargs
        assert (kwargs["width"], kwargs["height"]) == (1080, 1920)

    @pytest.mark.asyncio
    async def test_run_missing_short_shot_list_noop(self):
        state = {"task_id": "t-1"}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            out = await run_short(state)
        assert out == {"short_video_path": ""}
        mock_render.assert_not_called()

    def test_atom_meta_shape(self):
        from modules.content.atoms.media_render_short_video import ATOM_META

        assert ATOM_META.name == "media.render_short_video"
        assert ATOM_META.requires == ("task_id",)
        assert ATOM_META.produces == ("short_video_path",)
