"""Regression tests for the inline-image helper library (Glad-Labs/poindexter#157).

The inline-image render path — ``modules/content/atoms/_image_helpers``,
driven live by the ``content.generate_images`` atom — takes the GPU lock
twice, once for the Ollama prompt build and once for the image-gen render.
Pre-#157 the lock calls didn't carry ``task_id`` / ``phase``, so
``gpu_task_sessions`` (and downstream ``cost_logs`` rows) ended up
unattributed — the kWh + electricity-cost metrics couldn't be rolled up
per pipeline task.

These tests pin the contract: when ``_try_image_gen`` runs, both
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

from modules.content.atoms._image_helpers import _try_image_gen
from tests.unit._fake_platform import FakePlatform


class _LockRecorder:
    """Minimal stand-in for ``services.gpu_scheduler.gpu``.

    Records each ``lock(...)`` call's kwargs so tests can assert that
    ``task_id`` + ``phase`` reach the scheduler. The underlying async
    context manager is a no-op so ``_try_image_gen`` can run end-to-end.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @asynccontextmanager
    async def lock(self, owner: str, **kwargs: Any):
        self.calls.append({"owner": owner, **kwargs})
        yield


@pytest.mark.asyncio
async def test_try_image_gen_threads_task_id_to_both_gpu_locks():
    """Both ``gpu.lock`` calls inside ``_try_image_gen`` carry task_id+phase.

    The function takes the GPU twice (LLM prompt → image-gen render).
    Cost attribution in ``gpu_task_sessions`` / cost_logs needs both
    sessions tagged with the pipeline task UUID.
    """
    recorder = _LockRecorder()

    # Wave 3f (#667): dispatch now goes through platform.dispatch.complete.
    # FakePlatform returns a usable image-gen prompt (>20 chars). The image-gen
    # POST returns 200 so the path proceeds to R2 upload, which we
    # short-circuit with a stub ``_resolve_gen_response``.
    completion = MagicMock()
    completion.text = "a serene server room with cyan accents"

    platform = FakePlatform(dispatch_response=completion)

    gen_resp = MagicMock()
    gen_resp.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=gen_resp)

    site_config = SimpleNamespace(
        get=lambda _k, _d=None: _d if _d is not None else "",
        get_int=lambda _k, _d=0: _d,
        get_float=lambda _k, _d=0.0: _d,
        get_bool=lambda _k, _d=False: _d,
        _pool=MagicMock(),
    )

    with patch(
        "modules.content.atoms._image_helpers.gpu", recorder, create=True,
    ), patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "modules.content.atoms._image_helpers.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "modules.content.atoms._image_helpers._resolve_gen_response",
        new=AsyncMock(return_value="/tmp/glad-labs-generated-images/x.png"),
    ), patch(
        "modules.content.atoms._image_helpers._upload_to_r2_with_fallback",
        new=AsyncMock(return_value="https://r2.example/x.png"),
    ):
        result = await _try_image_gen(
            num="1",
            search_query="server racks",
            site_config=site_config,
            task_id="task-abc-123",
            platform=platform,
        )

    assert result == "https://r2.example/x.png"
    assert len(recorder.calls) == 2

    ollama_call, gen_call = recorder.calls
    assert ollama_call["owner"] == "ollama"
    assert ollama_call["task_id"] == "task-abc-123"
    assert ollama_call["phase"] == "inline_image_prompt"

    assert gen_call["owner"] == "image_gen"
    assert gen_call["task_id"] == "task-abc-123"
    assert gen_call["phase"] == "inline_image"


@pytest.mark.asyncio
async def test_batch_generate_inline_image_urls_uses_two_locks_for_n_images():
    """N inline images acquire the ollama lock ONCE and the image_gen lock ONCE.

    The #733 / poindexter#841 batching contract: the per-plan path took the GPU
    2N times (Ollama prompt + image-gen render per image, model-swapping between
    each); the batched two-phase path takes it exactly twice regardless of N —
    Phase 1 builds every prompt under one ``ollama`` lock, Phase 2 renders every
    image under one ``image_gen`` lock. Both locks still carry ``task_id`` so
    ``gpu_task_sessions`` cost attribution survives (#157).
    """
    from modules.content.atoms._image_helpers import batch_generate_inline_image_urls

    recorder = _LockRecorder()

    completion = MagicMock()
    completion.text = "a serene server room with cyan accents"
    platform = FakePlatform(dispatch_response=completion)

    gen_resp = MagicMock()
    gen_resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=gen_resp)

    site_config = SimpleNamespace(
        get=lambda _k, _d=None: _d if _d is not None else "",
        get_int=lambda _k, _d=0: _d,
        get_float=lambda _k, _d=0.0: _d,
        get_bool=lambda _k, _d=False: _d,
        _pool=MagicMock(),
    )

    placeholders = [
        ("1", "server racks"),
        ("2", "code on screens"),
        ("3", "a GPU die shot"),
    ]

    with patch(
        "modules.content.atoms._image_helpers.gpu", recorder, create=True,
    ), patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "modules.content.atoms._image_helpers.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "modules.content.atoms._image_helpers._resolve_gen_response",
        new=AsyncMock(return_value="/tmp/glad-labs-generated-images/x.png"),
    ), patch(
        "modules.content.atoms._image_helpers._upload_to_r2_with_fallback",
        new=AsyncMock(side_effect=[
            "https://r2.example/1.png",
            "https://r2.example/2.png",
            "https://r2.example/3.png",
        ]),
    ):
        urls = await batch_generate_inline_image_urls(
            placeholders,
            site_config=site_config,
            task_id="task-batch-777",
            platform=platform,
        )

    # Exactly two GPU acquisitions total, regardless of N=3 images.
    assert len(recorder.calls) == 2
    ollama_call, gen_call = recorder.calls
    assert ollama_call["owner"] == "ollama"
    assert ollama_call["task_id"] == "task-batch-777"
    assert ollama_call["phase"] == "inline_image_prompt_batch"
    assert gen_call["owner"] == "image_gen"
    assert gen_call["task_id"] == "task-batch-777"
    assert gen_call["phase"] == "inline_image_batch"

    # One URL per placeholder, in order.
    assert urls == [
        "https://r2.example/1.png",
        "https://r2.example/2.png",
        "https://r2.example/3.png",
    ]


@pytest.mark.asyncio
async def test_batch_generate_inline_image_urls_per_image_failure_is_isolated():
    """A single render failure yields None for that index without aborting the
    rest — run() then Pexels-falls-back only the failed image."""
    from modules.content.atoms._image_helpers import batch_generate_inline_image_urls

    recorder = _LockRecorder()
    completion = MagicMock()
    completion.text = "a serene server room with cyan accents"
    platform = FakePlatform(dispatch_response=completion)

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    bad_resp = MagicMock()
    bad_resp.status_code = 500
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    # image 1 renders, image 2 returns HTTP 500, image 3 renders.
    mock_client.post = AsyncMock(side_effect=[ok_resp, bad_resp, ok_resp])

    site_config = SimpleNamespace(
        get=lambda _k, _d=None: _d if _d is not None else "",
        get_int=lambda _k, _d=0: _d,
        get_float=lambda _k, _d=0.0: _d,
        get_bool=lambda _k, _d=False: _d,
        _pool=MagicMock(),
    )

    with patch(
        "modules.content.atoms._image_helpers.gpu", recorder, create=True,
    ), patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "modules.content.atoms._image_helpers.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "modules.content.atoms._image_helpers._resolve_gen_response",
        new=AsyncMock(return_value="/tmp/glad-labs-generated-images/x.png"),
    ), patch(
        "modules.content.atoms._image_helpers._upload_to_r2_with_fallback",
        new=AsyncMock(side_effect=["https://r2.example/1.png", "https://r2.example/3.png"]),
    ):
        urls = await batch_generate_inline_image_urls(
            [("1", "a"), ("2", "b"), ("3", "c")],
            site_config=site_config,
            task_id="task-iso",
            platform=platform,
        )

    # Still two locks total; the middle image is None, the others survive.
    assert len(recorder.calls) == 2
    assert urls == ["https://r2.example/1.png", None, "https://r2.example/3.png"]


@pytest.mark.asyncio
async def test_batch_skips_empty_description_without_using_topic():
    """A placeholder with no writer-provided description is skipped — never
    falls back to the raw article topic as the image-gen subject.

    The raw title getting handed to the prompt-building LLM let proper
    nouns/product names leak into the rendered image as garbled text.
    The fix: no description → no image-gen attempt for that slot at all
    (None → the caller's Pexels fallback), and no dispatch call is made.
    """
    from modules.content.atoms._image_helpers import batch_generate_inline_image_urls

    recorder = _LockRecorder()
    completion = MagicMock()
    completion.text = "a serene server room with cyan accents"
    platform = FakePlatform(dispatch_response=completion)

    gen_resp = MagicMock()
    gen_resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=gen_resp)

    site_config = SimpleNamespace(
        get=lambda _k, _d=None: _d if _d is not None else "",
        get_int=lambda _k, _d=0: _d,
        get_float=lambda _k, _d=0.0: _d,
        get_bool=lambda _k, _d=False: _d,
        _pool=MagicMock(),
    )

    # Placeholder "2" has no description — the writer left an empty
    # [IMAGE:] marker (or none at all).
    placeholders = [("1", "server racks"), ("2", ""), ("3", "a GPU die shot")]

    with patch(
        "modules.content.atoms._image_helpers.gpu", recorder, create=True,
    ), patch(
        "services.gpu_scheduler.gpu", recorder,
    ), patch(
        "modules.content.atoms._image_helpers.httpx.AsyncClient",
        return_value=mock_client,
    ), patch(
        "modules.content.atoms._image_helpers._resolve_gen_response",
        new=AsyncMock(return_value="/tmp/glad-labs-generated-images/x.png"),
    ), patch(
        "modules.content.atoms._image_helpers._upload_to_r2_with_fallback",
        new=AsyncMock(side_effect=[
            "https://r2.example/1.png",
            "https://r2.example/3.png",
        ]),
    ):
        urls = await batch_generate_inline_image_urls(
            placeholders,
            site_config=site_config,
            task_id="task-empty-desc",
            platform=platform,
        )

    # Placeholder "2" got None (Pexels fallback), the other two rendered.
    assert urls == ["https://r2.example/1.png", None, "https://r2.example/3.png"]
    # Only 2 dispatch calls (for "1" and "3") — the empty-desc slot never
    # asked the LLM to build a prompt, so it never had a chance to fall
    # back to the topic.
    assert len(platform.dispatch.calls) == 2


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
