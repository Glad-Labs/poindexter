"""Tests for poindexter#733 — batched GPU lock acquisition in replace_inline_images.

Before this fix, ``replace_inline_images`` acquired the GPU lock once per
image placeholder for prompt generation (Ollama) and once for rendering
(SDXL).  3 images = 6 lock acquisitions = 5 model swaps = ~95 s avg stage.

The fix batches the work into two phases:
  Phase 1: ONE Ollama lock → generate ALL SDXL prompts sequentially.
  Phase 2: ONE SDXL lock → render ALL images sequentially.

These tests verify:
1. ``_batch_generate_all_sdxl_images`` makes exactly 2 gpu.lock calls (one
   Ollama, one SDXL) regardless of how many placeholders are processed.
2. Per-placeholder failures don't abort the batch — the caller gets None for
   that slot and falls through to Pexels.
3. ``ReplaceInlineImagesStage.execute`` uses the batch path when going through
   the normal execute() flow (not the legacy _try_sdxl per-call path).
4. When no DB pool is available (tests without DB), the batch function
   gracefully returns [None]*n so _resolve_one_placeholder can use Pexels.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.content.stages.replace_inline_images import (
    ReplaceInlineImagesStage,
    _batch_generate_all_sdxl_images,
)
from plugins.fake_platform import FakePlatform

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _site_config_with_pool() -> Any:
    """Minimal SiteConfig-alike with a non-None _pool."""
    return SimpleNamespace(
        get=lambda key, default=None: default if default is not None else "",
        get_int=lambda key, default=0: default,
        get_float=lambda key, default=0.0: default,
        get_bool=lambda key, default=False: default,
        _pool=MagicMock(),
    )


def _site_config_no_pool() -> Any:
    """SiteConfig without a DB pool — used for the no-pool guard test."""
    return SimpleNamespace(
        get=lambda key, default=None: default if default is not None else "",
        get_int=lambda key, default=0: default,
        get_float=lambda key, default=0.0: default,
        get_bool=lambda key, default=False: default,
        _pool=None,
    )


class _LockRecorder:
    """Records gpu.lock calls without actually acquiring GPU resources."""

    def __init__(self, *, fail_on_owner: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_on_owner = fail_on_owner

    @asynccontextmanager
    async def lock(self, owner: str, **kwargs: Any):
        if self._fail_on_owner and owner == self._fail_on_owner:
            raise RuntimeError(f"Simulated lock failure for {owner}")
        self.calls.append({"owner": owner, **kwargs})
        yield


# ---------------------------------------------------------------------------
# _batch_generate_all_sdxl_images: lock-count contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_makes_exactly_two_gpu_locks_for_three_images():
    """3 placeholders → 1 Ollama lock + 1 SDXL lock (not 6)."""
    recorder = _LockRecorder()

    completion = MagicMock()
    completion.text = "a dramatic server room with cyan lighting and fog"
    platform = FakePlatform(dispatch_response=completion)

    sdxl_resp = MagicMock()
    sdxl_resp.status_code = 200
    # Phase 2 iterates inside a single AsyncClient context — mock at the
    # client.post level rather than wrapping AsyncClient.
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=sdxl_resp)

    placeholders = [
        ("1", "server racks"),
        ("2", "circuit board"),
        ("3", "GPU chip close-up"),
    ]

    with patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "modules.content.stages.replace_inline_images.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "modules.content.stages.replace_inline_images._resolve_sdxl_response",
        new=AsyncMock(return_value="/tmp/glad-labs-generated-images/test.png"),
    ), patch(
        "modules.content.stages.replace_inline_images._upload_to_r2_with_fallback",
        new=AsyncMock(return_value="https://r2.example/img.png"),
    ):
        urls = await _batch_generate_all_sdxl_images(
            placeholders=placeholders,
            topic="GPU Computing",
            site_config=_site_config_with_pool(),
            task_id="task-733",
            platform=platform,
        )

    # The core contract: exactly 2 lock acquisitions for any number of images.
    assert len(recorder.calls) == 2, (
        f"Expected 2 gpu.lock calls; got {len(recorder.calls)}: {recorder.calls}"
    )
    assert recorder.calls[0]["owner"] == "ollama"
    assert recorder.calls[0]["phase"] == "inline_image_prompt_batch"
    assert recorder.calls[1]["owner"] == "sdxl"
    assert recorder.calls[1]["phase"] == "inline_image_batch"

    # Should have produced 3 URLs (one per placeholder).
    assert len(urls) == 3
    assert all(u == "https://r2.example/img.png" for u in urls)


@pytest.mark.asyncio
async def test_batch_task_id_forwarded_to_both_locks():
    """task_id reaches both the Ollama and SDXL lock calls for cost attribution."""
    recorder = _LockRecorder()

    completion = MagicMock()
    completion.text = "a glowing RTX 5090 graphics card on a workbench"
    platform = FakePlatform(dispatch_response=completion)

    sdxl_resp = MagicMock()
    sdxl_resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=sdxl_resp)

    with patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "modules.content.stages.replace_inline_images.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "modules.content.stages.replace_inline_images._resolve_sdxl_response",
        new=AsyncMock(return_value="/tmp/test.png"),
    ), patch(
        "modules.content.stages.replace_inline_images._upload_to_r2_with_fallback",
        new=AsyncMock(return_value="https://r2.example/img.png"),
    ):
        await _batch_generate_all_sdxl_images(
            placeholders=[("1", "graphics card")],
            topic="GPU Benchmarks",
            site_config=_site_config_with_pool(),
            task_id="task-733-cost",
            platform=platform,
        )

    assert len(recorder.calls) == 2
    assert recorder.calls[0]["task_id"] == "task-733-cost"
    assert recorder.calls[1]["task_id"] == "task-733-cost"


# ---------------------------------------------------------------------------
# _batch_generate_all_sdxl_images: per-placeholder failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_one_prompt_failure_does_not_abort_batch():
    """A single prompt-generation failure yields None for that slot only."""
    recorder = _LockRecorder()

    call_count = 0

    class _FailOnSecondCall:
        """Dispatch that succeeds on 1st and 3rd calls, raises on the 2nd."""

        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("simulated LLM timeout")
            r = MagicMock()
            r.text = "a valid SDXL prompt for testing purposes"
            return r

    platform = SimpleNamespace(dispatch=_FailOnSecondCall())

    sdxl_resp = MagicMock()
    sdxl_resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=sdxl_resp)

    placeholders = [("1", "topic A"), ("2", "topic B"), ("3", "topic C")]

    with patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "modules.content.stages.replace_inline_images.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "modules.content.stages.replace_inline_images._resolve_sdxl_response",
        new=AsyncMock(return_value="/tmp/test.png"),
    ), patch(
        "modules.content.stages.replace_inline_images._upload_to_r2_with_fallback",
        new=AsyncMock(return_value="https://r2.example/ok.png"),
    ):
        urls = await _batch_generate_all_sdxl_images(
            placeholders=placeholders,
            topic="Test",
            site_config=_site_config_with_pool(),
            task_id="task-fail-test",
            platform=platform,
        )

    # Slot 2 (index 1) should be None; slots 0 and 2 should be URLs.
    assert urls[0] == "https://r2.example/ok.png"
    assert urls[1] is None   # failed prompt → Pexels fallback
    assert urls[2] == "https://r2.example/ok.png"
    # Only 2 lock acquisitions even with a failure.
    assert len(recorder.calls) == 2


@pytest.mark.asyncio
async def test_batch_one_sdxl_render_failure_does_not_abort_batch():
    """A single SDXL render failure yields None for that slot, others succeed."""
    recorder = _LockRecorder()

    completion = MagicMock()
    completion.text = "a valid SDXL prompt that is long enough to pass the check"
    platform = FakePlatform(dispatch_response=completion)

    # First and third render 200, second returns 500.
    render_call_count = 0

    async def _varying_post(*_a: Any, **_kw: Any) -> MagicMock:
        nonlocal render_call_count
        render_call_count += 1
        resp = MagicMock()
        resp.status_code = 500 if render_call_count == 2 else 200
        return resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=_varying_post)

    placeholders = [("1", "alpha"), ("2", "beta"), ("3", "gamma")]

    with patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "modules.content.stages.replace_inline_images.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "modules.content.stages.replace_inline_images._resolve_sdxl_response",
        new=AsyncMock(return_value="/tmp/test.png"),
    ), patch(
        "modules.content.stages.replace_inline_images._upload_to_r2_with_fallback",
        new=AsyncMock(return_value="https://r2.example/img.png"),
    ):
        urls = await _batch_generate_all_sdxl_images(
            placeholders=placeholders,
            topic="SDXL",
            site_config=_site_config_with_pool(),
            task_id="task-render-fail",
            platform=platform,
        )

    assert urls[0] == "https://r2.example/img.png"
    assert urls[1] is None   # 500 → Pexels fallback
    assert urls[2] == "https://r2.example/img.png"
    assert len(recorder.calls) == 2


# ---------------------------------------------------------------------------
# _batch_generate_all_sdxl_images: guard cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_returns_none_list_when_no_pool():
    """No DB pool → return [None]*n immediately without touching the GPU lock."""
    recorder = _LockRecorder()
    platform = FakePlatform()

    with patch(
        "services.gpu_scheduler.gpu", recorder,
    ):
        urls = await _batch_generate_all_sdxl_images(
            placeholders=[("1", "cat"), ("2", "dog")],
            topic="Pets",
            site_config=_site_config_no_pool(),
            task_id="task-no-pool",
            platform=platform,
        )

    assert urls == [None, None]
    # No GPU lock acquired — the DB is required.
    assert len(recorder.calls) == 0


@pytest.mark.asyncio
async def test_batch_returns_empty_list_for_empty_placeholders():
    """Zero placeholders → empty return, no GPU activity."""
    recorder = _LockRecorder()
    with patch(
        "services.gpu_scheduler.gpu", recorder,
    ):
        urls = await _batch_generate_all_sdxl_images(
            placeholders=[],
            topic="Anything",
            site_config=_site_config_with_pool(),
            task_id="task-empty",
            platform=FakePlatform(),
        )

    assert urls == []
    assert len(recorder.calls) == 0


@pytest.mark.asyncio
async def test_batch_returns_none_list_when_no_platform():
    """No platform handle → return [None]*n immediately."""
    recorder = _LockRecorder()

    with patch(
        "services.gpu_scheduler.gpu", recorder,
    ):
        urls = await _batch_generate_all_sdxl_images(
            placeholders=[("1", "anything")],
            topic="Test",
            site_config=_site_config_with_pool(),
            task_id="task-no-platform",
            platform=None,
        )

    assert urls == [None]
    assert len(recorder.calls) == 0


@pytest.mark.asyncio
async def test_batch_returns_none_list_on_ollama_lock_failure():
    """If the Ollama lock can't be acquired, return [None]*n (no SDXL lock)."""
    recorder = _LockRecorder(fail_on_owner="ollama")
    platform = FakePlatform()

    with patch(
        "services.gpu_scheduler.gpu", recorder,
    ):
        urls = await _batch_generate_all_sdxl_images(
            placeholders=[("1", "alpha"), ("2", "beta")],
            topic="Lock test",
            site_config=_site_config_with_pool(),
            task_id="task-lock-fail",
            platform=platform,
        )

    assert urls == [None, None]
    # Only the failed Ollama lock attempt — no SDXL lock should have fired.
    assert all(c["owner"] != "sdxl" for c in recorder.calls)


# ---------------------------------------------------------------------------
# ReplaceInlineImagesStage.execute: batch path is taken
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stage_execute_uses_batch_path():
    """execute() goes through _batch_generate_all_sdxl_images, not per-call _try_sdxl.

    Pins that the stage calls the batch helper rather than the per-image
    ``_try_sdxl`` when there are existing placeholders.  We spy on both
    to confirm exactly which one runs.
    """
    batch_calls: list[Any] = []
    try_sdxl_calls: list[Any] = []

    async def fake_batch(**kwargs: Any) -> list[str | None]:
        batch_calls.append(kwargs)
        # Return a URL for the one placeholder in the test content.
        return ["https://r2.example/batched.png"]

    async def fake_try_sdxl(*_a: Any, **_kw: Any) -> str | None:
        try_sdxl_calls.append(True)
        return None

    db = MagicMock()
    db.update_task = AsyncMock()

    ctx: dict[str, Any] = {
        "task_id": "task-stage-batch",
        "topic": "GPU Computing",
        "content": "Intro\n\n[IMAGE-1: server racks]\n\nOutro",
        "database_service": db,
        "image_service": SimpleNamespace(
            search_featured_image=AsyncMock(return_value=None),
        ),
        "site_config": SimpleNamespace(
            get=lambda k, d=None: d if d is not None else "",
            get_int=lambda k, d=0: d,
            get_float=lambda k, d=0.0: d,
            get_bool=lambda k, d=False: d,
        ),
    }

    with patch(
        "modules.content.stages.replace_inline_images._batch_generate_all_sdxl_images",
        new=AsyncMock(side_effect=fake_batch),
    ), patch(
        "modules.content.stages.replace_inline_images._try_sdxl",
        new=AsyncMock(side_effect=fake_try_sdxl),
    ), patch(
        "modules.content.stages.replace_inline_images._record_inline_image_asset",
        new=AsyncMock(return_value=None),
    ), patch(
        "services.text_utils.normalize_text", side_effect=lambda x: x,
    ), patch(
        "services.alt_text.sanitize_alt_text", side_effect=lambda t, **_: t,
    ):
        result = await ReplaceInlineImagesStage().execute(ctx, {})

    assert result.ok is True
    # Batch path was invoked.
    assert len(batch_calls) == 1
    # Per-image _try_sdxl was NOT invoked (batch path pre-generates all URLs).
    assert len(try_sdxl_calls) == 0
    # The batched URL ended up in the content (read from context_updates, not ctx).
    updated_content = (result.context_updates or {}).get("content", "")
    assert "https://r2.example/batched.png" in updated_content


@pytest.mark.asyncio
async def test_stage_falls_back_to_pexels_when_batch_returns_none():
    """When batch returns None for a placeholder, Pexels is used as fallback.

    Validates that pregenerated_sdxl_url=None correctly triggers Strategy 2
    (Pexels) in _resolve_one_placeholder.
    """
    async def fake_batch(**kwargs: Any) -> list[str | None]:
        # Batch fails for all placeholders.
        count = len(kwargs.get("placeholders", []))
        return [None] * count

    pexels_img = SimpleNamespace(url="https://pexels.example/img.jpg", photographer="Alice")

    db = MagicMock()
    db.update_task = AsyncMock()

    ctx: dict[str, Any] = {
        "task_id": "task-pexels-fallback",
        "topic": "Machine Learning",
        "content": "Intro\n\n[IMAGE-1: neural network]\n\nOutro",
        "database_service": db,
        "image_service": SimpleNamespace(
            search_featured_image=AsyncMock(return_value=pexels_img),
        ),
        "site_config": SimpleNamespace(
            get=lambda k, d=None: d if d is not None else "",
            get_int=lambda k, d=0: d,
            get_float=lambda k, d=0.0: d,
            get_bool=lambda k, d=False: d,
        ),
    }

    with patch(
        "modules.content.stages.replace_inline_images._batch_generate_all_sdxl_images",
        new=AsyncMock(side_effect=fake_batch),
    ), patch(
        "modules.content.stages.replace_inline_images._record_inline_image_asset",
        new=AsyncMock(return_value=None),
    ), patch(
        "services.text_utils.normalize_text", side_effect=lambda x: x,
    ), patch(
        "services.alt_text.sanitize_alt_text", side_effect=lambda t, **_: t,
    ):
        result = await ReplaceInlineImagesStage().execute(ctx, {})

    assert result.ok is True
    content = (result.context_updates or {}).get("content", "")
    # Placeholder was replaced with the Pexels URL.
    assert "https://pexels.example/img.jpg" in content
    # No raw placeholder survived.
    assert "[IMAGE-1" not in content
