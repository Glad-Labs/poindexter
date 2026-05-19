"""VRAM-guard tests for ``ReplaceInlineImagesStage``.

Pins the 2026-05-19 jank-audit finding #4 fix: the writer LLM (~20 GB
for ``gemma3:27b``) must be explicitly unloaded before the SDXL phase
begins, so 24 GB cards don't OOM at the stage-5→stage-7 transition.

The unload utility itself lives in
``services.llm_providers.ollama_unload`` and is tested independently —
these tests pin only the stage-level wiring:

* ``maybe_unload_writer_before_sdxl`` is called once at stage entry.
* The current ``site_config`` (DI seam) is passed through.
* The unload runs BEFORE any SDXL or Pexels work begins (so the VRAM
  is freed by the time ``_try_sdxl`` is called).
* When the gate is off, the rest of the stage still runs normally
  (we only assert the gate flag reached the helper — the helper's own
  branching is tested in ``test_ollama_unload.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.stages.replace_inline_images import ReplaceInlineImagesStage


def _site_config(unload_enabled: bool = True) -> Any:
    return SimpleNamespace(
        get=lambda key, default="": default,
        get_int=lambda key, default=0: default,
        get_float=lambda key, default=0.0: default,
        get_bool=lambda key, default=False: (
            unload_enabled
            if key == "pipeline_explicit_writer_unload_before_sdxl"
            else default
        ),
    )


def _stage_context(
    *,
    site_config: Any,
    content: str = "Intro\n\n[IMAGE-1: cat]\n\nOutro",
) -> dict[str, Any]:
    db = MagicMock()
    db.update_task = AsyncMock()
    return {
        "task_id": "task-vram-guard",
        "topic": "Cats",
        "content": content,
        "database_service": db,
        "image_service": SimpleNamespace(
            search_featured_image=AsyncMock(return_value=None),
        ),
        "site_config": site_config,
    }


@pytest.mark.asyncio
async def test_stage_invokes_writer_unload_at_entry():
    """``maybe_unload_writer_before_sdxl`` runs once per stage execution."""
    sc = _site_config()
    ctx = _stage_context(site_config=sc)

    unload_mock = AsyncMock(return_value=["gemma3:27b"])

    with patch(
        "services.llm_providers.ollama_unload.maybe_unload_writer_before_sdxl",
        new=unload_mock,
    ), patch(
        "services.stages.replace_inline_images._try_sdxl",
        new=AsyncMock(return_value=None),
    ), patch(
        "services.text_utils.normalize_text", side_effect=lambda x: x,
    ):
        result = await ReplaceInlineImagesStage().execute(ctx, {})

    assert result.ok is True
    unload_mock.assert_awaited_once()
    # Threaded the injected site_config + the stage's name so the log
    # marker shows ``[REPLACE_INLINE_IMAGES]``.
    kwargs = unload_mock.await_args.kwargs
    assert kwargs["site_config"] is sc
    assert kwargs["stage_label"] == "replace_inline_images"


@pytest.mark.asyncio
async def test_stage_unloads_before_calling_try_sdxl():
    """The writer unload happens BEFORE the first ``_try_sdxl`` call.

    Order matters — if SDXL fires before Ollama has had its grace
    seconds to release VRAM, the 32 GB card hits 98% (and the 24 GB
    card OOMs). Use a call recorder to pin the order.
    """
    sc = _site_config()
    ctx = _stage_context(site_config=sc)
    call_order: list[str] = []

    async def record_unload(**_kwargs):
        call_order.append("unload")
        return []

    async def record_sdxl(*_args, **_kwargs):
        call_order.append("try_sdxl")
        return None

    with patch(
        "services.llm_providers.ollama_unload.maybe_unload_writer_before_sdxl",
        new=AsyncMock(side_effect=record_unload),
    ), patch(
        "services.stages.replace_inline_images._try_sdxl",
        new=AsyncMock(side_effect=record_sdxl),
    ), patch(
        "services.text_utils.normalize_text", side_effect=lambda x: x,
    ):
        await ReplaceInlineImagesStage().execute(ctx, {})

    assert call_order[0] == "unload", (
        f"unload must precede any SDXL work; saw {call_order}"
    )
    assert "try_sdxl" in call_order


@pytest.mark.asyncio
async def test_stage_skips_unload_when_no_content():
    """Empty content → stage short-circuits before the unload sweep.

    The unload tax (~3-5 s) is wasted if there's no SDXL work coming.
    """
    sc = _site_config()
    ctx = _stage_context(site_config=sc, content="")
    unload_mock = AsyncMock(return_value=[])

    with patch(
        "services.llm_providers.ollama_unload.maybe_unload_writer_before_sdxl",
        new=unload_mock,
    ):
        result = await ReplaceInlineImagesStage().execute(ctx, {})

    assert result.ok is True
    unload_mock.assert_not_called()


@pytest.mark.asyncio
async def test_stage_threads_site_config_with_gate_off():
    """Gate-off site_config still reaches the helper.

    Pins that ``ReplaceInlineImagesStage`` doesn't second-guess the
    gate — the per-operator decision lives in ``app_settings``, the
    stage just hands the SiteConfig through.
    """
    sc = _site_config(unload_enabled=False)
    ctx = _stage_context(site_config=sc)
    unload_mock = AsyncMock(return_value=[])

    with patch(
        "services.llm_providers.ollama_unload.maybe_unload_writer_before_sdxl",
        new=unload_mock,
    ), patch(
        "services.stages.replace_inline_images._try_sdxl",
        new=AsyncMock(return_value=None),
    ), patch(
        "services.text_utils.normalize_text", side_effect=lambda x: x,
    ):
        await ReplaceInlineImagesStage().execute(ctx, {})

    unload_mock.assert_awaited_once()
    # The helper still got called — it'll detect get_bool=False
    # internally and no-op. The stage's job is to ALWAYS hand it the
    # SiteConfig and let the helper decide.
    assert unload_mock.await_args.kwargs["site_config"] is sc
