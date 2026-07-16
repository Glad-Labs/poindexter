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
from services.site_config import SiteConfig


class _FakeLock:
    """No-op async CM standing in for gpu.lock during unit tests."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _patch_gpu_lock():
    """The render now runs inside ``gpu.lock("video")`` (validation findings
    4b/7 — evict Ollama + hold the cross-process pg_advisory_lock for the render
    duration). Patch it to a no-op CM so these unit tests never reach the real
    scheduler (Ollama /api/ps unload HTTP + pg_advisory_lock connect). Yields
    the mock so a test can assert the lock owner."""
    lock_mock = MagicMock(return_value=_FakeLock())
    with patch.object(_media_render.gpu, "lock", lock_mock):
        yield lock_mock

# A minimal valid 16:9 long-form shot-list dict (rehydratable by
# VideoShotList.model_validate). One image_gen shot keeps it trivial.
_LONG_SHOT_LIST = {
    "version": 1,
    "aspect": "16:9",
    "total_duration_s": 3.0,
    "shots": [
        {
            "idx": 0,
            "duration_s": 3.0,
            "intent": "opening still",
            "source": "image_gen",
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


def _ok_result(
    *, shots_rendered=1, shots_total=1, output_path="/tmp/out.mp4",
    shots_carded=0, shots_substituted=0,
):
    """A successful ShotListRenderResult-shaped object."""
    return SimpleNamespace(
        success=True,
        output_path=output_path,
        file_size_bytes=123,
        duration_s=3.0,
        shots_rendered=shots_rendered,
        shots_total=shots_total,
        shots_substituted=shots_substituted,
        shots_carded=shots_carded,
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
        shots_substituted=0,
        shots_carded=0,
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
    async def test_render_held_under_gpu_video_lock(self, _patch_gpu_lock):
        """The render runs inside ``gpu.lock("video")`` so the scheduler evicts
        Ollama + holds the cross-process pg_advisory_lock for the render's
        duration (validation findings 4b/7 — the render path used to bypass the
        scheduler entirely, so the resident writer/director starved wan+image-gen →
        'inference server unreachable' → render failures)."""
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert _patch_gpu_lock.call_count == 1
        assert _patch_gpu_lock.call_args.args[0] == "video"
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
    async def test_caption_srt_path_threaded_to_renderer(self):
        """A caption_srt_path in state is threaded to render_shot_list's
        caption_path kwarg so the render burns the captions in (#676 Plan 5)."""
        state = {
            "task_id": "t-1",
            "video_shot_list": _LONG_SHOT_LIST,
            "caption_srt_path": "/tmp/captions_t-1.srt",
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert mock_render.await_args.kwargs["caption_path"] == "/tmp/captions_t-1.srt"

    @pytest.mark.asyncio
    async def test_no_caption_srt_path_passes_none(self):
        """No caption_srt_path in state → caption_path=None (render without
        burned-in captions; the empty-string channel default also maps to None)."""
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert mock_render.await_args.kwargs["caption_path"] is None

    @pytest.mark.asyncio
    async def test_empty_caption_srt_path_passes_none(self):
        """An empty-string caption_srt_path (the transcribe atom's no-op
        sentinel) maps to None, not "" — so the compositor doesn't try to
        burn a non-existent track."""
        state = {
            "task_id": "t-1",
            "video_shot_list": _LONG_SHOT_LIST,
            "caption_srt_path": "",
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert mock_render.await_args.kwargs["caption_path"] is None

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
    async def test_partial_render_above_ratio_emits_finding_and_ships(self):
        """Real-source ratio AT/above video_render_min_real_source_ratio
        (default 0.5 — 5 real of 8 here, card disabled so 3 dropped) → the
        video still ships, with a partial_render finding so the degrade is
        never silent. Redesign §9 / Gap C."""
        state = {"task_id": "t-99", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(
            return_value=_ok_result(
                shots_rendered=5, shots_total=8, output_path="/tmp/partial.mp4"
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
        assert kwargs["extra"]["shots_rendered"] == 5
        assert kwargs["extra"]["shots_total"] == 8

    @pytest.mark.asyncio
    async def test_partial_render_below_ratio_is_treated_as_failed(self):
        """Below video_render_min_real_source_ratio (default 0.5 — 2 real of 7
        here, the shipped-degraded incident shape) → the render is REJECTED:
        empty output key (so no video persists and the media_reconciliation
        watchdog re-dispatches it) + a partial_render_rejected finding."""
        state = {"task_id": "t-99", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(
            return_value=_ok_result(
                shots_rendered=2, shots_total=7, output_path="/tmp/degraded.mp4"
            )
        )
        mock_emit = MagicMock()
        with patch.object(_media_render, "render_shot_list", mock_render), patch.object(
            _media_render, "emit_finding", mock_emit
        ):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": ""}
        assert mock_emit.call_count == 1
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "partial_render_rejected"
        assert kwargs["severity"] == "warn"
        assert kwargs["extra"]["shots_rendered"] == 2
        assert kwargs["extra"]["shots_total"] == 7
        assert kwargs["extra"]["min_real_ratio"] == 0.5

    @pytest.mark.asyncio
    async def test_mostly_cards_is_treated_as_failed(self):
        """The ladder's failure mode: every planned shot 'rendered' but 8 of 10
        are branded cards (image-gen outage). shots_rendered == shots_total, so
        the old drop-based gate would ship it silently; the real-source ratio
        (2 real of 10 = 0.2 < 0.5) catches it and REJECTS for re-dispatch."""
        state = {"task_id": "t-99", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(
            return_value=_ok_result(
                shots_rendered=10,
                shots_total=10,
                shots_carded=8,
                output_path="/tmp/allcards.mp4",
            )
        )
        mock_emit = MagicMock()
        with patch.object(_media_render, "render_shot_list", mock_render), patch.object(
            _media_render, "emit_finding", mock_emit
        ):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": ""}
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "partial_render_rejected"
        assert kwargs["extra"]["shots_carded"] == 8

    @pytest.mark.asyncio
    async def test_card_and_substitute_fill_above_ratio_ships(self):
        """A healthy ladder result: all 10 shots rendered, 1 branded card + 2
        cross-family substitutes (substitutes count as real footage). Real
        ratio (10 - 1 card) / 10 = 0.9 ≥ 0.5 → ships, with a partial_render
        finding carrying the card/substitute breakdown."""
        state = {"task_id": "t-99", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(
            return_value=_ok_result(
                shots_rendered=10,
                shots_total=10,
                shots_carded=1,
                shots_substituted=2,
                output_path="/tmp/laddered.mp4",
            )
        )
        mock_emit = MagicMock()
        with patch.object(_media_render, "render_shot_list", mock_render), patch.object(
            _media_render, "emit_finding", mock_emit
        ):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": "/tmp/laddered.mp4"}
        assert mock_emit.call_count == 1
        kwargs = mock_emit.call_args.kwargs
        assert kwargs["kind"] == "partial_render"
        assert kwargs["extra"]["shots_carded"] == 1
        assert kwargs["extra"]["shots_substituted"] == 2

    @pytest.mark.asyncio
    async def test_min_real_source_ratio_is_site_config_tunable(self):
        """An operator-raised ratio (0.9) rejects a 5-real-of-8 partial that the
        default would ship — the knob is DB-first per feedback_db_first_config."""
        site_config = SiteConfig(
            initial_config={"video_render_min_real_source_ratio": "0.9"},
        )
        state = {
            "task_id": "t-99",
            "video_shot_list": _LONG_SHOT_LIST,
            "site_config": site_config,
        }
        mock_render = AsyncMock(
            return_value=_ok_result(shots_rendered=5, shots_total=8)
        )
        mock_emit = MagicMock()
        with patch.object(_media_render, "render_shot_list", mock_render), patch.object(
            _media_render, "emit_finding", mock_emit
        ):
            out = await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert out == {"long_video_path": ""}
        assert mock_emit.call_args.kwargs["kind"] == "partial_render_rejected"

    @pytest.mark.asyncio
    async def test_min_real_source_ratio_zero_disables_the_gate(self):
        """'0' disables the reject gate: every partial ships (prior behaviour),
        still flagged by the partial_render finding."""
        site_config = SiteConfig(
            initial_config={"video_render_min_real_source_ratio": "0"},
        )
        state = {
            "task_id": "t-99",
            "video_shot_list": _LONG_SHOT_LIST,
            "site_config": site_config,
        }
        mock_render = AsyncMock(
            return_value=_ok_result(
                shots_rendered=1, shots_total=8, output_path="/tmp/partial.mp4"
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
        assert mock_emit.call_args.kwargs["kind"] == "partial_render"

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
    async def test_image_gen_url_from_site_config(self):
        site_config = MagicMock()
        site_config.get = MagicMock(return_value="http://image-gen-host:9836")
        # Real SiteConfig.get_float always coerces to float; the real-source
        # gate now runs on every render, so the mock must too.
        site_config.get_float = MagicMock(return_value=0.5)
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
        assert mock_render.await_args.kwargs["image_gen_url"] == "http://image-gen-host:9836"

    @pytest.mark.asyncio
    async def test_image_gen_url_default_when_no_site_config(self):
        state = {"task_id": "t-1", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state, shot_list_key="video_shot_list", output_key="long_video_path"
            )
        assert (
            mock_render.await_args.kwargs["image_gen_url"]
            == "http://image-gen-server:9836"
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

    @pytest.mark.asyncio
    async def test_long_render_uses_long_narration_channel(self):
        """The long atom narrates long_narration_audio_path + burns
        long_caption_srt_path (its OWN lane), not the shared podcast audio (#689)."""
        state = {
            "task_id": "t1",
            "video_shot_list": _LONG_SHOT_LIST,
            "long_narration_audio_path": "/tmp/long.mp3",
            "short_narration_audio_path": "/tmp/short.mp3",
            "long_caption_srt_path": "/tmp/long.srt",
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await run_long(state)
        kwargs = mock_render.await_args.kwargs
        assert kwargs["audio_path"] == "/tmp/long.mp3"
        assert kwargs["caption_path"] == "/tmp/long.srt"

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

    @pytest.mark.asyncio
    async def test_short_render_uses_short_narration_channel(self):
        """The short atom narrates short_narration_audio_path + burns
        short_caption_srt_path (its OWN lane) (#689)."""
        state = {
            "task_id": "t1",
            "short_shot_list": _SHORT_SHOT_LIST,
            "long_narration_audio_path": "/tmp/long.mp3",
            "short_narration_audio_path": "/tmp/short.mp3",
            "short_caption_srt_path": "/tmp/short.srt",
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await run_short(state)
        kwargs = mock_render.await_args.kwargs
        assert kwargs["audio_path"] == "/tmp/short.mp3"
        assert kwargs["caption_path"] == "/tmp/short.srt"

    def test_atom_meta_shape(self):
        from modules.content.atoms.media_render_short_video import ATOM_META

        assert ATOM_META.name == "media.render_short_video"
        assert ATOM_META.requires == ("task_id",)
        assert ATOM_META.produces == ("short_video_path",)


class TestNarrationFitPlumbing:
    """Part 1 (#867): the short atom opts into narration-fit; the long atom does
    not; the master setting can force it off. render_from_state resolves the
    per-shot ceiling from site_config."""

    @pytest.mark.asyncio
    async def test_short_lane_passes_narration_fit_to_renderer(self):
        site_config = SiteConfig(initial_config={
            "video_narration_fit_enabled": "true",
            "video_short_max_shot_seconds": "9",
        })
        state = {
            "task_id": "t",
            "short_shot_list": _SHORT_SHOT_LIST,
            "site_config": site_config,
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await run_short(state)
        kw = mock_render.await_args.kwargs
        assert kw["narration_fit"] is True
        assert kw["narration_fit_max_shot_s"] == 9.0

    @pytest.mark.asyncio
    async def test_long_lane_does_not_enable_narration_fit(self):
        state = {"task_id": "t", "video_shot_list": _LONG_SHOT_LIST}
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await run_long(state)
        assert mock_render.await_args.kwargs["narration_fit"] is False

    @pytest.mark.asyncio
    async def test_narration_fit_disabled_via_setting(self):
        site_config = SiteConfig(initial_config={"video_narration_fit_enabled": "false"})
        state = {
            "task_id": "t",
            "short_shot_list": _SHORT_SHOT_LIST,
            "site_config": site_config,
        }
        mock_render = AsyncMock(return_value=_ok_result())
        with patch.object(_media_render, "render_shot_list", mock_render):
            await _media_render.render_from_state(
                state,
                shot_list_key="short_shot_list",
                output_key="short_video_path",
                narration_fit=True,
            )
        assert mock_render.await_args.kwargs["narration_fit"] is False
