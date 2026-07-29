"""Live-path behavior tests for the image atoms.

These pin behaviors that used to be tested through ``ReplaceInlineImagesStage``
(a zombie stage deleted 2026-07 once its helper web moved into
``atoms/_image_helpers.py``). The behaviors are LIVE on the graph_def path via
the ``content.plan_image_markers`` / ``content.generate_images`` atoms, so the
coverage moves onto the atoms that actually run:

* VRAM guard — ``content.plan_image_markers`` unloads the writer LLM before the
  image-gen phase (2026-05-19 jank-audit #4). The ordering guarantee (unload
  before any ``try_image_gen``) is now structural: ``plan_image_markers`` is a
  distinct graph node that precedes ``generate_images``, so it can't race the
  render within one method the way the monolithic stage could.
* media_assets producer hook — ``content.generate_images`` records a
  ``media_assets`` row for every successful inline image (GH#161), on both the
  image-gen and Pexels branches.
* task_id attribution — the originating pipeline ``task_id`` threads from the
  atom state into ``try_image_gen`` so ``gpu_task_sessions`` / cost_logs roll up
  per task (Glad-Labs/poindexter#157).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from modules.content.atoms import content_generate_images, content_plan_image_markers


def _site_config(unload_enabled: bool = True) -> Any:
    return SimpleNamespace(
        get=lambda key, default="": default,
        get_int=lambda key, default=0: default,
        get_float=lambda key, default=0.0: default,
        get_bool=lambda key, default=False: (
            unload_enabled
            if key == "pipeline_writer_unload_before_image_gen"
            # Stock fallback is opt-in as of 2026-07-28 (default OFF). The
            # fallback tests in this file assert the opted-IN behaviour, so
            # they enable it explicitly rather than inheriting the default.
            else True
            if key == "image_stock_fallback_enabled"
            else default
        ),
    )


# ---------------------------------------------------------------------------
# VRAM guard — content.plan_image_markers
# ---------------------------------------------------------------------------


class TestPlanImageMarkersVramGuard:
    @pytest.mark.asyncio
    async def test_unloads_writer_with_site_config_and_atom_label(self):
        """The atom unloads the writer once, threading the DI site_config and
        its own node label (so the log marker points at the atom, not the
        deleted stage)."""
        sc = _site_config()
        unload_mock = AsyncMock(return_value=["gemma3:27b"])

        with patch(
            "services.llm_providers.ollama_unload.maybe_unload_writer_before_image_gen",
            new=unload_mock,
        ):
            result = await content_plan_image_markers.run({
                "content": "Intro\n\n[IMAGE-1: cat]\n\nOutro",
                "topic": "Cats",
                "site_config": sc,
            })

        unload_mock.assert_awaited_once()
        kwargs = unload_mock.await_args.kwargs
        assert kwargs["site_config"] is sc
        assert kwargs["stage_label"] == "content.plan_image_markers"
        # Existing marker was parsed into an image_plan (no decision-agent call).
        assert result["image_plans"] == [{"num": "1", "desc": "cat"}]

    @pytest.mark.asyncio
    async def test_skips_unload_when_no_content(self):
        """Empty content short-circuits before the unload sweep — the ~3-5 s
        unload tax is wasted if there's no image-gen work coming."""
        unload_mock = AsyncMock(return_value=[])

        with patch(
            "services.llm_providers.ollama_unload.maybe_unload_writer_before_image_gen",
            new=unload_mock,
        ):
            result = await content_plan_image_markers.run({
                "content": "",
                "topic": "Cats",
                "site_config": _site_config(),
            })

        assert result == {}
        unload_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_hands_gate_off_site_config_through_to_helper(self):
        """The atom never second-guesses the gate — the per-operator decision
        lives in app_settings; the atom always hands the SiteConfig through and
        lets ``maybe_unload_writer_before_image_gen`` no-op internally."""
        sc = _site_config(unload_enabled=False)
        unload_mock = AsyncMock(return_value=[])

        with patch(
            "services.llm_providers.ollama_unload.maybe_unload_writer_before_image_gen",
            new=unload_mock,
        ):
            await content_plan_image_markers.run({
                "content": "Intro\n\n[IMAGE-1: cat]\n\nOutro",
                "topic": "Cats",
                "site_config": sc,
            })

        unload_mock.assert_awaited_once()
        assert unload_mock.await_args.kwargs["site_config"] is sc


# ---------------------------------------------------------------------------
# media_assets producer hook + task_id — content.generate_images
# ---------------------------------------------------------------------------


class TestGenerateImagesProducerHook:
    def _state(self, **over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "image_plans": [{"num": "1", "desc": "a cat on a sofa"}],
            "topic": "Cats",
            "task_id": "task-abc-123",
            "post_id": "post-1",
            "site_config": _site_config(),
            "image_service": SimpleNamespace(),
        }
        base.update(over)
        return base

    @pytest.mark.asyncio
    async def test_image_gen_success_records_media_asset(self):
        """A successful image-gen render records a media_assets row tagged with
        the image.image_gen provider + the placeholder number + task_id."""
        record_mock = AsyncMock(return_value=None)

        with patch(
            "modules.content.atoms._image_helpers.batch_generate_inline_image_urls",
            new=AsyncMock(return_value=["https://r2.example/gen-1.png"]),
        ), patch(
            "modules.content.atoms._image_helpers.record_inline_image_asset",
            new=record_mock,
        ):
            result = await content_generate_images.run(self._state())

        record_mock.assert_awaited_once()
        kwargs = record_mock.await_args.kwargs
        assert kwargs["public_url"] == "https://r2.example/gen-1.png"
        assert kwargs["provider_plugin"] == "image.image_gen"
        assert kwargs["metadata"]["placeholder_num"] == "1"
        assert kwargs["metadata"]["task_id"] == "task-abc-123"
        assert result["image_results"][0]["source"] == "image_gen"

    @pytest.mark.asyncio
    async def test_pexels_fallback_records_media_asset(self):
        """When image-gen yields nothing, the Pexels fallback still records a
        media_assets row (image.pexels provider)."""
        record_mock = AsyncMock(return_value=None)

        with patch(
            "modules.content.atoms._image_helpers.batch_generate_inline_image_urls",
            new=AsyncMock(return_value=[None]),
        ), patch(
            "modules.content.atoms._image_helpers.try_pexels",
            new=AsyncMock(return_value=("https://pexels.example/cat.jpg", "Jane")),
        ), patch(
            "modules.content.atoms._image_helpers.record_inline_image_asset",
            new=record_mock,
        ):
            result = await content_generate_images.run(self._state())

        record_mock.assert_awaited_once()
        assert record_mock.await_args.kwargs["provider_plugin"] == "image.pexels"
        assert result["image_results"][0]["source"] == "pexels"

    @pytest.mark.asyncio
    async def test_threads_task_id_into_batch_helper(self):
        """The originating task_id reaches ``batch_generate_inline_image_urls``
        so the GPU session rows attribute to the pipeline task
        (Glad-Labs/poindexter#157), preserved across the #841 batching move."""
        captured: dict[str, Any] = {}

        async def fake_batch(placeholders, *, site_config, task_id, platform=None):
            captured["task_id"] = task_id
            captured["placeholders"] = placeholders
            return [None] * len(placeholders)  # force Pexels fallback so the rest of the atom runs

        with patch(
            "modules.content.atoms._image_helpers.batch_generate_inline_image_urls",
            new=AsyncMock(side_effect=fake_batch),
        ), patch(
            "modules.content.atoms._image_helpers.try_pexels",
            new=AsyncMock(return_value=None),
        ):
            await content_generate_images.run(self._state(task_id="task-xyz-789"))

        assert captured["task_id"] == "task-xyz-789"
        assert captured["placeholders"] == [("1", "a cat on a sofa")]


# ---------------------------------------------------------------------------
# alt-text leak guard — content.generate_images (Glad-Labs/poindexter#469)
# ---------------------------------------------------------------------------
#
# Migrated here from the deleted ``_resolve_one_placeholder`` helper tests in
# test_inline_image_helpers.py — that helper (and the ReplaceInlineImagesStage
# it served) no longer runs in prod. The #469 guard lives on the LIVE atom
# path: ``content.generate_images.run() -> _build_alt_text -> sanitize_alt_text``.
# These pin that an image-gen-prompt-shaped descriptor lands a topic-derived
# alt (never the raw imperative prompt) in image_results[i]["alt_text"], and
# that a genuine human descriptor still flows through unchanged. The pure
# looks_like_image_gen_prompt / sanitize_alt_text logic is unit-tested in
# services/test_alt_text.py; this is the atom-path integration.


class TestGenerateImagesAltTextLeakGuard:
    def _state(self, desc: str, topic: str) -> dict[str, Any]:
        return {
            "image_plans": [{"num": "1", "desc": desc}],
            "topic": topic,
            "task_id": "task-469",
            "post_id": None,
            "site_config": _site_config(),
            "image_service": SimpleNamespace(),
        }

    @pytest.mark.asyncio
    async def test_image_gen_prompt_descriptor_does_not_leak_to_alt(self):
        """An image-gen-prompt-shaped descriptor → topic fallback alt, never the
        raw imperative prompt. The exact poisoned shape from issue #469."""
        poisoned_desc = (
            "An isometric diagram of a simplified SDXL architecture. "
            "Show the key components (encoder, decoder, refiner) with arrows, "
            "no text, no faces"
        )
        topic = "Stable Diffusion XL on a Single RTX 5090"

        with patch(
            "modules.content.atoms._image_helpers.batch_generate_inline_image_urls",
            new=AsyncMock(return_value=["https://r2.example/inline-1.png"]),
        ), patch(
            "modules.content.atoms._image_helpers.record_inline_image_asset",
            new=AsyncMock(return_value=None),
        ):
            result = await content_generate_images.run(self._state(poisoned_desc, topic))

        alt = result["image_results"][0]["alt_text"]
        # The raw image-gen prompt must NOT reach the alt attribute.
        assert "Show the key components" not in alt
        assert "no text" not in alt
        assert "no faces" not in alt
        # The topic-derived fallback must.
        assert topic in alt
        # And the image still rendered on the image-gen branch.
        assert result["image_results"][0]["source"] == "image_gen"
        assert result["image_results"][0]["url"] == "https://r2.example/inline-1.png"

    @pytest.mark.asyncio
    async def test_real_human_descriptor_passes_through_to_alt(self):
        """Negative case — a legitimate descriptor flows into alt unchanged.
        "macro" appears (an INLINE_STYLES word) but as natural prose, so the
        #469 heuristic must NOT drop it."""
        clean_desc = "A close-up macro photo of a circuit board with red LEDs"

        with patch(
            "modules.content.atoms._image_helpers.batch_generate_inline_image_urls",
            new=AsyncMock(return_value=["https://r2.example/inline-1.png"]),
        ), patch(
            "modules.content.atoms._image_helpers.record_inline_image_asset",
            new=AsyncMock(return_value=None),
        ):
            result = await content_generate_images.run(self._state(clean_desc, "Electronics"))

        assert result["image_results"][0]["alt_text"] == clean_desc


# ---------------------------------------------------------------------------
# media_assets recorder contract — record_inline_image_asset (GH#161)
# ---------------------------------------------------------------------------
#
# These pin the recorder call shape + the no-post_id skip directly on the shared
# helper (its proper home), migrated off the deleted ReplaceInlineImagesStage
# that used to drive them. Both image atoms call this helper, so the contract is
# covered once here rather than per-caller.


class TestRecordInlineImageAssetContract:
    @pytest.mark.asyncio
    async def test_records_inline_image_row_with_provider_and_type(self):
        from modules.content.atoms._image_helpers import record_inline_image_asset

        recorder = AsyncMock(return_value="asset-uuid")
        with patch("services.media_asset_recorder.record_media_asset", recorder):
            await record_inline_image_asset(
                site_config=SimpleNamespace(_pool=object()),
                post_id="post-uuid-1",
                public_url="https://r2.example/inline-1.png",
                provider_plugin="image.image_gen",
                width=1024, height=1024, mime_type="image/webp",
                metadata={"placeholder_num": "1"},
            )

        recorder.assert_awaited_once()
        kwargs = recorder.await_args.kwargs
        assert kwargs["asset_type"] == "inline_image"
        assert kwargs["post_id"] == "post-uuid-1"
        assert kwargs["public_url"] == "https://r2.example/inline-1.png"
        assert kwargs["provider_plugin"] == "image.image_gen"

    @pytest.mark.asyncio
    async def test_no_post_id_skips_recording(self):
        """Early-pipeline runs before the post row exists skip the insert."""
        from modules.content.atoms._image_helpers import record_inline_image_asset

        recorder = AsyncMock()
        with patch("services.media_asset_recorder.record_media_asset", recorder):
            await record_inline_image_asset(
                site_config=SimpleNamespace(_pool=object()),
                post_id=None,
                public_url="https://r2.example/x.png",
                provider_plugin="image.image_gen",
                width=1024, height=1024, mime_type="image/webp",
                metadata={},
            )

        recorder.assert_not_awaited()
