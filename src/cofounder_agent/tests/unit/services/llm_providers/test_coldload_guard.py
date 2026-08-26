"""Tests for ``services.llm_providers.coldload_guard``.

Pins the cold-load VRAM guard contract (2026-08-25 desktop-crash incident:
an ~18 GB ``structured_extraction_model`` cold-load beside a 7.4 GB idle
ComfyUI CUDA-OOM'd the render GPU; the OOM storm crashed Chrome and Claude
Desktop, whose GPU processes render on the same card):

* fires the shared reclaim ladder (``gpu.reclaim_render_vram`` with
  ``include_ollama=False``) ONLY for a local Ollama-prefixed model that is
  NOT resident and is at least ``min_gb`` big;
* every other path — disabled, cloud prefix, cloud base, resident model
  (either tag spelling), small model, probe failure — is a no-op that
  never raises and never runs the ladder;
* ``/api/tags`` sizes are cached so the steady state costs one ``/api/ps``
  GET per call.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm_providers import coldload_guard
from services.llm_providers.coldload_guard import maybe_reclaim_before_coldload

_LOCAL_BASE = "http://host.docker.internal:11434"

_GB = 1_000_000_000


class _FakeAsyncClient:
    """Route ``GET /api/ps`` and ``GET /api/tags`` to canned payloads."""

    def __init__(
        self,
        *,
        ps: dict | None = None,
        tags: dict | None = None,
        ps_status: int = 200,
        tags_status: int = 200,
        get_error: Exception | None = None,
    ) -> None:
        self._ps = ps if ps is not None else {"models": []}
        self._tags = tags if tags is not None else {"models": []}
        self._ps_status = ps_status
        self._tags_status = tags_status
        self._get_error = get_error
        self.requested: list[str] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, url: str) -> MagicMock:
        self.requested.append(url)
        if self._get_error is not None:
            raise self._get_error
        resp = MagicMock()
        if url.endswith("/api/ps"):
            resp.status_code = self._ps_status
            resp.json = MagicMock(return_value=self._ps)
        elif url.endswith("/api/tags"):
            resp.status_code = self._tags_status
            resp.json = MagicMock(return_value=self._tags)
        else:  # pragma: no cover - guard requests nothing else
            raise AssertionError(f"unexpected GET {url}")
        return resp


@pytest.fixture(autouse=True)
def _fresh_tags_cache():
    coldload_guard._tags_cache.clear()
    yield
    coldload_guard._tags_cache.clear()


def _patch_client(fake: _FakeAsyncClient):
    return patch.object(
        coldload_guard.httpx, "AsyncClient", new=lambda **_kw: fake,
    )


def _patch_gpu() -> tuple[object, SimpleNamespace]:
    reclaim = AsyncMock()
    return (
        patch("services.gpu_scheduler.gpu", new=SimpleNamespace(
            reclaim_render_vram=reclaim,
        )),
        reclaim,
    )


_BIG_TAGS = {"models": [{"name": "gemma-4-31B-it-qat:latest", "size": 18 * _GB}]}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_disabled_makes_no_http_calls():
    fake = _FakeAsyncClient()
    with _patch_client(fake):
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/gemma-4-31B-it-qat:latest",
            api_base=_LOCAL_BASE,
            enabled=False,
        )
    assert fired is False
    assert fake.requested == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloud_prefix_never_guards():
    fake = _FakeAsyncClient()
    with _patch_client(fake):
        fired = await maybe_reclaim_before_coldload(
            resolved_model="anthropic/claude-sonnet-5",
            api_base=_LOCAL_BASE,
        )
    assert fired is False
    assert fake.requested == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cloud_base_never_guards():
    fake = _FakeAsyncClient()
    with _patch_client(fake):
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/gemma-4-31B-it-qat:latest",
            api_base="https://api.example.com/v1",
        )
    assert fired is False
    assert fake.requested == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resident_model_is_a_noop():
    fake = _FakeAsyncClient(
        ps={"models": [{"name": "gemma-4-31B-it-qat:latest"}]},
        tags=_BIG_TAGS,
    )
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch:
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/gemma-4-31B-it-qat:latest",
            api_base=_LOCAL_BASE,
        )
    assert fired is False
    reclaim.assert_not_awaited()
    # The resident fast path stops at /api/ps — no tags fetch.
    assert [u for u in fake.requested if u.endswith("/api/tags")] == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tagless_pin_matches_latest_residency():
    # Ollama normalises "gemma-4-31B-it-qat" → ":latest" in /api/ps; a
    # tagless operator pin must not fire the ladder for a resident model.
    fake = _FakeAsyncClient(
        ps={"models": [{"name": "gemma-4-31B-it-qat:latest"}]},
        tags=_BIG_TAGS,
    )
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch:
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/gemma-4-31B-it-qat",
            api_base=_LOCAL_BASE,
        )
    assert fired is False
    reclaim.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_small_model_is_a_noop():
    fake = _FakeAsyncClient(
        tags={"models": [{"name": "llama3.2:3b", "size": 2 * _GB}]},
    )
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch:
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/llama3.2:3b",
            api_base=_LOCAL_BASE,
            min_gb=8.0,
        )
    assert fired is False
    reclaim.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_big_cold_model_fires_ladder_without_ollama_rung():
    fake = _FakeAsyncClient(tags=_BIG_TAGS)
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch:
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama_chat/gemma-4-31B-it-qat:latest",
            api_base=_LOCAL_BASE,
            min_gb=8.0,
        )
    assert fired is True
    reclaim.assert_awaited_once_with(include_ollama=False)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_probe_failure_never_raises_or_fires():
    fake = _FakeAsyncClient(get_error=ConnectionError("ollama down"))
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch:
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/gemma-4-31B-it-qat:latest",
            api_base=_LOCAL_BASE,
        )
    assert fired is False
    reclaim.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_size_is_a_noop():
    # Model absent from /api/tags (would 404 at generate time anyway).
    fake = _FakeAsyncClient(tags={"models": []})
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch:
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/ghost-model:1b",
            api_base=_LOCAL_BASE,
        )
    assert fired is False
    reclaim.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reclaim_failure_is_swallowed():
    fake = _FakeAsyncClient(tags=_BIG_TAGS)
    reclaim = AsyncMock(side_effect=RuntimeError("ladder exploded"))
    gpu_patch = patch(
        "services.gpu_scheduler.gpu",
        new=SimpleNamespace(reclaim_render_vram=reclaim),
    )
    with _patch_client(fake), gpu_patch:
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/gemma-4-31B-it-qat:latest",
            api_base=_LOCAL_BASE,
        )
    assert fired is True  # the guard ran; the load proceeds regardless
    reclaim.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tags_are_cached_across_calls():
    fake = _FakeAsyncClient(tags=_BIG_TAGS)
    gpu_patch, _reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch:
        for _ in range(2):
            await maybe_reclaim_before_coldload(
                resolved_model="ollama/gemma-4-31B-it-qat:latest",
                api_base=_LOCAL_BASE,
            )
    tags_gets = [u for u in fake.requested if u.endswith("/api/tags")]
    ps_gets = [u for u in fake.requested if u.endswith("/api/ps")]
    assert len(tags_gets) == 1  # second call served from the size cache
    assert len(ps_gets) == 2  # residency is always probed fresh
