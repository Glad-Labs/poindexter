"""Regression tests for Glad-Labs/poindexter#157.

The inline-image phase (``ReplaceInlineImagesStage``) takes the GPU
lock twice — once for the Ollama prompt build, once for the SDXL
render. Pre-#157 the lock calls didn't carry ``task_id`` / ``phase``,
so ``gpu_task_sessions`` (and downstream ``cost_logs`` rows) ended up
unattributed — the kWh + electricity-cost metrics couldn't be rolled
up per pipeline task.

These tests pin the contract: when ``_try_sdxl`` runs, both
``gpu.lock`` calls receive the originating ``task_id`` plus a phase
label, mirroring the featured-image flow in
``source_featured_image.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.stages.replace_inline_images import (
    ReplaceInlineImagesStage,
    _try_sdxl,
)

# Mirrors the fake used in test_remaining_stages_smoke.py.
_FAKE_SITE_CONFIG = SimpleNamespace(
    get=lambda _k, _d=None: _d if _d is not None else "",
    get_int=lambda _k, _d=0: _d,
    get_float=lambda _k, _d=0.0: _d,
    get_bool=lambda _k, _d=False: _d,
)


class _LockRecorder:
    """Minimal stand-in for ``services.gpu_scheduler.gpu``.

    Records each ``lock(...)`` call's kwargs so tests can assert that
    ``task_id`` + ``phase`` reach the scheduler. The underlying async
    context manager is a no-op so ``_try_sdxl`` can run end-to-end.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @asynccontextmanager
    async def lock(self, owner: str, **kwargs: Any):
        self.calls.append({"owner": owner, **kwargs})
        yield


@pytest.mark.asyncio
async def test_try_sdxl_threads_task_id_to_both_gpu_locks():
    """Both ``gpu.lock`` calls inside ``_try_sdxl`` carry task_id+phase.

    The function takes the GPU twice (Ollama prompt → SDXL render).
    Cost attribution in ``gpu_task_sessions`` / cost_logs needs both
    sessions tagged with the pipeline task UUID.
    """
    recorder = _LockRecorder()

    # The Ollama POST returns a usable SDXL prompt (>20 chars). The
    # SDXL POST returns 200 so the path proceeds to R2 upload, which
    # we short-circuit with a stub ``_resolve_sdxl_response``.
    ollama_resp = MagicMock()
    ollama_resp.status_code = 200
    ollama_resp.raise_for_status = MagicMock()
    ollama_resp.json = MagicMock(
        return_value={"response": "a serene server room with cyan accents"},
    )

    sdxl_resp = MagicMock()
    sdxl_resp.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    # First .post(...) is Ollama, second is SDXL.
    mock_client.post = AsyncMock(side_effect=[ollama_resp, sdxl_resp])

    with patch(
        "services.stages.replace_inline_images.gpu", recorder, create=True,
    ), patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "services.stages.replace_inline_images.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "services.stages.replace_inline_images._resolve_sdxl_response",
        new=AsyncMock(return_value="/tmp/glad-labs-generated-images/x.png"),
    ), patch(
        "services.stages.replace_inline_images._upload_to_r2_with_fallback",
        new=AsyncMock(return_value="https://r2.example/x.png"),
    ):
        result = await _try_sdxl(
            num="1",
            search_query="server racks",
            topic="Database performance",
            site_config=_FAKE_SITE_CONFIG,
            task_id="task-abc-123",
        )

    assert result == "https://r2.example/x.png"
    assert len(recorder.calls) == 2

    ollama_call, sdxl_call = recorder.calls
    assert ollama_call["owner"] == "ollama"
    assert ollama_call["task_id"] == "task-abc-123"
    assert ollama_call["phase"] == "inline_image_prompt"

    assert sdxl_call["owner"] == "sdxl"
    assert sdxl_call["task_id"] == "task-abc-123"
    assert sdxl_call["phase"] == "inline_image"


@pytest.mark.asyncio
async def test_stage_propagates_task_id_into_try_sdxl():
    """End-to-end through the Stage: task_id from context reaches _try_sdxl."""
    captured: dict[str, Any] = {}

    async def fake_try_sdxl(num, search_query, topic, *, site_config, task_id):
        captured["task_id"] = task_id
        captured["num"] = num
        return None  # force Pexels fallback so the rest of the stage runs

    img_obj = SimpleNamespace(
        url="https://pexels.example/cat.jpg", photographer="Jane",
    )
    image_service = SimpleNamespace(
        search_featured_image=AsyncMock(return_value=img_obj),
    )

    db = MagicMock()
    db.update_task = AsyncMock()
    ctx: dict[str, Any] = {
        "task_id": "task-xyz-789",
        "topic": "Cats",
        "content": "Intro\n\n[IMAGE-1: cat]\n\nOutro",
        "database_service": db,
        "image_service": image_service,
        "site_config": _FAKE_SITE_CONFIG,
    }

    with patch(
        "services.stages.replace_inline_images._try_sdxl",
        new=AsyncMock(side_effect=fake_try_sdxl),
    ), patch(
        "services.text_utils.normalize_text", side_effect=lambda x: x,
    ):
        result = await ReplaceInlineImagesStage().execute(ctx, {})

    assert result.ok is True
    assert captured["task_id"] == "task-xyz-789"
    assert captured["num"] == "1"


@pytest.mark.asyncio
async def test_sdxl_prompt_in_placeholder_does_not_leak_to_alt():
    """Glad-Labs/poindexter#469 — SDXL-shaped descriptor → topic fallback alt.

    When the Image Decision Agent injects ``[IMAGE-N: <SDXL-prompt>]``
    and the SDXL render succeeds, the rendered ``<img alt="...">`` must
    show the topic-derived fallback, not the raw imperative-mood prompt.
    """
    from services.stages.replace_inline_images import _resolve_one_placeholder

    # The exact poisoned descriptor shape from the bug report.
    poisoned_desc = (
        "An isometric diagram of a simplified SDXL architecture. "
        "Show the key components (encoder, decoder, refiner) with arrows, "
        "no text, no faces"
    )
    topic = "Stable Diffusion XL on a Single RTX 5090"
    content_text = f"Intro\n\n[IMAGE-1: {poisoned_desc}]\n\nOutro"

    # SDXL path succeeds — returns a URL we can assert on.
    with patch(
        "services.stages.replace_inline_images._try_sdxl",
        new=AsyncMock(return_value="https://r2.example/inline-1.png"),
    ), patch(
        "services.stages.replace_inline_images._record_inline_image_asset",
        new=AsyncMock(return_value=None),
    ):
        result = await _resolve_one_placeholder(
            num="1",
            desc=poisoned_desc,
            topic=topic,
            content_text=content_text,
            image_service=SimpleNamespace(),
            used_image_ids=set(),
            site_config=_FAKE_SITE_CONFIG,
            task_id="task-469-repro",
            post_id=None,
        )

    # The raw SDXL prompt must NOT appear in the alt attribute.
    assert "Show the key components" not in result
    assert "no text" not in result
    assert "no faces" not in result
    # And the topic-derived fallback must.
    assert "Stable Diffusion XL on a Single RTX 5090" in result
    # Sanity-check the image src is the SDXL URL.
    assert 'src="https://r2.example/inline-1.png"' in result


@pytest.mark.asyncio
async def test_real_human_alt_in_placeholder_passes_through():
    """Negative case — a legitimate descriptor flows into alt unchanged.

    Protects against the heuristic going overboard and dropping
    real alts. "A close-up macro photo..." is a normal alt — the
    word "macro" appears in our INLINE_STYLES rotation but here it's
    used as a noun-modifier in natural prose.
    """
    from services.stages.replace_inline_images import _resolve_one_placeholder

    clean_desc = "A close-up macro photo of a circuit board with red LEDs"
    content_text = f"Intro\n\n[IMAGE-1: {clean_desc}]\n\nOutro"

    with patch(
        "services.stages.replace_inline_images._try_sdxl",
        new=AsyncMock(return_value="https://r2.example/inline-1.png"),
    ), patch(
        "services.stages.replace_inline_images._record_inline_image_asset",
        new=AsyncMock(return_value=None),
    ):
        result = await _resolve_one_placeholder(
            num="1",
            desc=clean_desc,
            topic="Electronics",
            content_text=content_text,
            image_service=SimpleNamespace(),
            used_image_ids=set(),
            site_config=_FAKE_SITE_CONFIG,
            task_id="task-clean",
            post_id=None,
        )

    # Real alt passes through.
    assert f'alt="{clean_desc}"' in result


@pytest.mark.asyncio
async def test_gpu_task_session_recorded_with_task_id():
    """``gpu_scheduler._record_task_session`` writes the task_id to the DB.

    Pins the cost-attribution sink: when ``gpu.lock`` is given a
    ``task_id``, the row inserted into ``gpu_task_sessions`` (the table
    that drives the per-task kWh / electricity-cost rollup) carries
    that task_id as its first column.
    """
    from datetime import datetime, timezone

    from services.gpu_scheduler import GPUScheduler

    scheduler = GPUScheduler()
    scheduler._get_gpu_utilization = AsyncMock(return_value=42.0)
    scheduler._get_gpu_power_watts = AsyncMock(return_value=300.0)

    # asyncpg.connect → execute path: capture the args.
    fake_conn = AsyncMock()
    fake_conn.execute = AsyncMock()
    fake_conn.close = AsyncMock()

    with patch("asyncpg.connect", new=AsyncMock(return_value=fake_conn)), patch(
        "brain.bootstrap.resolve_database_url", return_value="postgresql://x",
    ):
        await scheduler._record_task_session(
            task_id="task-pin-007",
            phase="inline_image",
            model="sdxl_lightning",
            started_at=datetime.now(timezone.utc),
            duration_seconds=2.5,
        )

    fake_conn.execute.assert_awaited_once()
    args = fake_conn.execute.await_args.args
    # args[0] is the SQL, args[1] is task_id (first $-bound positional).
    assert args[1] == "task-pin-007"
    assert args[2] == "inline_image"
