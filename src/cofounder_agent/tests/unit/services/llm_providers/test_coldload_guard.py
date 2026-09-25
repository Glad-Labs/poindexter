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

from poindexter.services.llm_providers import coldload_guard
from poindexter.services.llm_providers.coldload_guard import maybe_reclaim_before_coldload

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
        patch("poindexter.services.gpu_scheduler.gpu", new=SimpleNamespace(
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
        "poindexter.services.gpu_scheduler.gpu",
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


# --- placement: reclaim the render GPU only for loads that land on it --------
#
# 2026-09-25: every cold qwen3-vl judge call (19.6 GB, on the GPU-1-pinned
# :11435 instance) ran the render-GPU ladder, mid-render: RIFE and chatterbox
# unloaded, ComfyUI sent /free and once restarted, sidecar restarts queued.
# These drive the REAL placement resolver in gpu_scheduler with the prod rows.

_JUDGE_BASE = "http://host.docker.internal:11435"
_JUDGE_TAGS = {"models": [
    {"name": "qwen3-vl:30b-a3b-instruct", "size": int(19.6 * _GB)},
    {"name": "gemma-4-31B-it-qat:latest", "size": 18 * _GB},
]}
_PROD_PLACEMENT = {
    "gpu_lock_per_device_enabled": "true",
    "gpu_lock_scopes": '{"render": [0], "qa_judge": [1], "llm_primary": [0]}',
    "ollama_base_url": _LOCAL_BASE,
    "ollama_vision_base_url": _JUDGE_BASE,
    "pipeline_gpu_index": "0",
}


@pytest.fixture
def placement(monkeypatch):
    """Install a SiteConfig for gpu_scheduler's placement resolver."""
    from poindexter.services import gpu_scheduler as gs
    from poindexter.services.site_config import SiteConfig

    for key in _PROD_PLACEMENT:
        monkeypatch.delenv(key.upper(), raising=False)

    def _apply(**overrides):
        values = {**_PROD_PLACEMENT, **overrides}
        cfg = SiteConfig(initial_config=values)
        monkeypatch.setattr(gs, "_sc", lambda: cfg)

    return _apply


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cold_judge_off_the_render_gpu_skips_the_ladder(placement, caplog):
    placement()
    fake = _FakeAsyncClient(tags=_JUDGE_TAGS)
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch, caplog.at_level("INFO", logger=coldload_guard.__name__):
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/qwen3-vl:30b-a3b-instruct",
            api_base=_JUDGE_BASE,
        )
    assert fired is False
    reclaim.assert_not_awaited()
    # It still noticed the cold load, and says why it stood down.
    assert fake.requested == [f"{_JUDGE_BASE}/api/ps", f"{_JUDGE_BASE}/api/tags"]
    assert "which loads on GPU 1; the reclaim ladder frees GPU 0" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cold_primary_load_still_runs_the_ladder(placement, caplog):
    """The 2026-08-25 case: gemma cold-loading on :11434, the render GPU."""
    placement()
    fake = _FakeAsyncClient(tags=_JUDGE_TAGS)
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch, caplog.at_level("INFO", logger=coldload_guard.__name__):
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/gemma-4-31B-it-qat:latest",
            api_base=_LOCAL_BASE,
        )
    assert fired is True
    reclaim.assert_awaited_once_with(include_ollama=False)
    assert "loads on GPU 0, overlapping the render GPU 0" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "api_base"),
    [
        # A third instance nobody declared a placement for.
        ({}, "http://host.docker.internal:11436"),
        # Scoping off: the shipped two-card map is not a claim about this box.
        ({"gpu_lock_per_device_enabled": "false"}, _JUDGE_BASE),
        # No judge instance configured.
        ({"ollama_vision_base_url": ""}, _JUDGE_BASE),
        # Judge unpinned: its scope widened onto the render card.
        ({"gpu_lock_scopes": '{"render": [0], "qa_judge": [0, 1], "llm_primary": [0]}'},
         _JUDGE_BASE),
        # A map nobody can read.
        ({"gpu_lock_scopes": "{not json"}, _JUDGE_BASE),
    ],
    ids=["unknown-instance", "scoping-off", "no-vision-url", "judge-unpinned", "malformed-map"],
)
async def test_unproven_placement_runs_the_ladder_as_before(
    placement, caplog, overrides, api_base,
):
    placement(**overrides)
    fake = _FakeAsyncClient(tags=_JUDGE_TAGS)
    gpu_patch, reclaim = _patch_gpu()
    with _patch_client(fake), gpu_patch, caplog.at_level("INFO", logger=coldload_guard.__name__):
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/qwen3-vl:30b-a3b-instruct",
            api_base=api_base,
        )
    assert fired is True
    reclaim.assert_awaited_once_with(include_ollama=False)
    assert "running the media VRAM reclaim ladder" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_placement_resolver_failure_runs_the_ladder_and_never_raises(placement):
    placement()
    fake = _FakeAsyncClient(tags=_JUDGE_TAGS)
    gpu_patch, reclaim = _patch_gpu()
    boom = MagicMock(side_effect=RuntimeError("settings unreadable"))
    with _patch_client(fake), gpu_patch, patch(
        "poindexter.services.gpu_scheduler.ollama_host_devices", new=boom,
    ):
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/qwen3-vl:30b-a3b-instruct",
            api_base=_JUDGE_BASE,
        )
    assert fired is True
    reclaim.assert_awaited_once_with(include_ollama=False)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_warm_model_never_resolves_placement(placement):
    """The steady state stays one /api/ps GET — no settings reads added."""
    placement()
    fake = _FakeAsyncClient(
        ps={"models": [{"name": "qwen3-vl:30b-a3b-instruct"}]}, tags=_JUDGE_TAGS,
    )
    resolver = MagicMock()
    with _patch_client(fake), patch(
        "poindexter.services.gpu_scheduler.ollama_host_devices", new=resolver,
    ):
        fired = await maybe_reclaim_before_coldload(
            resolved_model="ollama/qwen3-vl:30b-a3b-instruct",
            api_base=_JUDGE_BASE,
        )
    assert fired is False
    resolver.assert_not_called()
