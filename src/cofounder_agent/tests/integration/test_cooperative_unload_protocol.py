"""Integration test for the cooperative sidecar-unload protocol (#160).

Best-effort: this test only runs against a live wan-server reachable at
``http://localhost:9840``. When the sidecar isn't up — which is the
default state in CI and on most dev machines — the test is skipped.
Running it locally requires:

    docker compose -f docker-compose.local.yml up -d wan-server

…(or ``poetry run python scripts/wan-server.py`` in another terminal) so
/health and /unload respond. The test then asks the scheduler to run its
wan VRAM-reclaim rung and verifies VRAM dropped through /health probing.

What this drives, and why it moved
==================================
The protocol shipped as ``GPUScheduler.request_sidecar_unload(names)``.
That method is **gone**: the per-sidecar unload levers were folded into
the render-GPU reclaim ladder, ``GPUScheduler.reclaim_render_vram()``,
which walks one rung per sidecar (ollama / image-gen / chatterbox / wan /
stable-audio / comfyui). Its unit coverage was deleted with it (#130,
"target functions no longer exist"); this file was missed, so it kept
calling the deleted method behind a construction-time ``TypeError`` and
the protocol had no working end-to-end coverage at all.

We drive the **wan rung** (``_unload_wan``) rather than the whole ladder
deliberately. The ladder evicts every sidecar on the box — on an operator
rig that means yanking models a live pipeline is mid-way through using —
while the rung is the part that actually speaks the protocol: URL
resolution through ``site_config`` plus ``POST /unload``. Ladder *wiring*
(which rungs exist, in what order) is a unit-test concern; that a real
sidecar honours the request is what needs a real sidecar.

Soft unload (no ``hard=True``) on purpose: the server drops its pipeline
objects and stays up to lazy-reload on the next ``/generate``, instead of
exiting its process. ``vram_used_mb`` is ``torch.cuda.memory_allocated``,
which a soft unload does return — the reserved pool that only a process
exit frees is a different number.

Why this lives here, not under tests/unit:

* It speaks real HTTP to a real GPU sidecar.
* It mutates wan-server's VRAM state. Running it in CI without an
  isolated sidecar would race the rest of the pipeline.
* It needs the same skip-if-unreachable contract the other live
  integrations use (Pexels, Cloudinary, etc.).
"""
from __future__ import annotations

import asyncio
import functools
import os
import time
from collections.abc import Iterator
from unittest.mock import MagicMock

import httpx
import pytest

from services.gpu_scheduler import GPUScheduler
from services.site_config import SiteConfig

_WAN_URL = os.environ.get("WAN_SERVER_URL", "http://localhost:9840")

# How long to keep polling /health for the VRAM drop after the unload POST
# returns. The unload itself is synchronous behind wan-server's GPU lock,
# but ``_unload_wan`` caps its own POST at 10s and swallows the timeout, so
# a slow release would otherwise read as a broken protocol.
_VRAM_DROP_TIMEOUT_S = 30.0


@functools.lru_cache(maxsize=1)
def _wan_reachable() -> bool:
    """Synchronous reachability check, cached so it runs at most once.

    Called by the ``_require_wan`` autouse fixture at test *setup*
    (not import) time, so pytest collection makes no network call
    (#1057, sibling of #994).
    """
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{_WAN_URL}/health")
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_wan() -> None:
    """Skip every test in this module unless wan-server is reachable.

    Replaces the former module-level ``skipif(not _wan_reachable())``,
    which probed the sidecar at *import* time — so merely collecting this
    file (e.g. a CI `pytest tests/integration/` sweep) hit
    ``{_WAN_URL}/health``. A fixture defers the probe to test setup,
    keeping collection offline while preserving the "skip the whole
    module when the wan-server is unreachable" behaviour. The probe is
    cached, so it fires at most once per session.
    """
    if not _wan_reachable():
        pytest.skip(f"wan-server not reachable at {_WAN_URL}")


@pytest.fixture
def gpu_site_config() -> Iterator[None]:
    """Register a process-wide ``AppContainer`` pointing wan at ``_WAN_URL``.

    ``GPUScheduler.__init__`` takes no ``site_config``: the module reads
    settings through ``services.container_registry.get_container()`` (the
    #788 capstone of the #272 SiteConfig-DI migration), falling back to an
    empty ``SiteConfig`` when nothing is registered. Registering a container
    is therefore how a test hands the scheduler its config — the same shape
    ``tests/unit/conftest.py::_register_test_container`` uses, scoped to one
    test and restored afterwards so it can't leak.

    ``wan_server_url`` is the top-level key ``video_providers.wan2_1.
    _resolve_server_url`` prefers, and ``_unload_wan`` resolves the reclaim
    URL through that same function — so the rung under test provably hits
    the server this test is probing.
    """
    from services.container import AppContainer
    from services.container_registry import get_container, set_container

    previous = get_container()
    set_container(
        AppContainer(
            site_config=SiteConfig(initial_config={"wan_server_url": _WAN_URL}),
            pool=MagicMock(name="cooperative_unload_test.pool"),
        ),
    )
    try:
        yield
    finally:
        set_container(previous)


async def _read_vram_mb(client: httpx.AsyncClient) -> int:
    """Current ``vram_used_mb`` from wan-server's /health."""
    health = (await client.get(f"{_WAN_URL}/health")).json()
    return int(health.get("vram_used_mb") or 0)


async def _await_vram_drop(client: httpx.AsyncClient, *, below: int) -> int:
    """Poll /health until VRAM drops below ``below``; return the last reading.

    Returns the final reading either way — the caller asserts on it, so a
    genuine refusal (wan-server declines an unload while a generation is in
    flight) still fails at the end of the window instead of hanging.
    """
    deadline = time.monotonic() + _VRAM_DROP_TIMEOUT_S
    while True:
        current = await _read_vram_mb(client)
        if current < below or time.monotonic() >= deadline:
            return current
        await asyncio.sleep(1.0)


@pytest.mark.asyncio
async def test_cooperative_unload_drops_wan_vram(gpu_site_config: None) -> None:
    """End-to-end: warm wan-server, run the wan reclaim rung, verify VRAM dropped.

    Steps:
        1. Hit /health to read current VRAM.
        2. Issue a tiny /generate to ensure the model is loaded
           (skipped when /health already reports 'ready').
        3. Read VRAM again — should be > 0.
        4. Call the scheduler's wan reclaim rung (``_unload_wan``).
        5. Read VRAM again — should be lower than step 3.
    """
    scheduler = GPUScheduler()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            health_before = (await client.get(f"{_WAN_URL}/health")).json()
            if health_before.get("status") != "ready":
                # Warm the pipeline with a tiny prompt — the smallest valid
                # request still allocates the WanPipeline. We don't care
                # about the output, only the VRAM footprint, but use a
                # generous timeout because cold-start can be 30+ seconds.
                try:
                    await client.post(
                        f"{_WAN_URL}/generate",
                        json={
                            "prompt": "a small test scene",
                            "duration_s": 1,
                            "steps": 1,
                            "fps": 8,
                            "width": 256,
                            "height": 256,
                        },
                        timeout=httpx.Timeout(300.0),
                    )
                except Exception:
                    pytest.skip("wan-server /generate failed; skipping")

            vram_before = await _read_vram_mb(client)
            if vram_before <= 0:
                pytest.skip(
                    "wan-server reports vram_used_mb=0 — no model loaded, "
                    "nothing to unload",
                )

            # The rung is fire-and-forget by contract: it logs and swallows
            # (a sidecar that is simply down between renders is the common
            # case, not a bug), so the assertion has to come from the
            # server's own view of its VRAM rather than a return value.
            await scheduler._unload_wan()

            vram_after = await _await_vram_drop(client, below=vram_before)
    finally:
        await scheduler.aclose()

    assert vram_after < vram_before, (
        f"VRAM did not drop after /unload: before={vram_before}MB "
        f"after={vram_after}MB. If wan-server was mid-generation it "
        f"declines the unload by design (busy_generation_in_flight) — "
        f"re-run against an idle sidecar."
    )
