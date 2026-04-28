"""Integration test for the cooperative sidecar-unload protocol (#160).

Best-effort: this test only runs against a live wan-server reachable at
``http://localhost:9840``. When the sidecar isn't up — which is the
default state in CI and on most dev machines — the test is skipped.
Running it locally requires:

    poetry run python scripts/wan-server.py

…in another terminal so /health and /unload respond. The test then
asks the scheduler to issue ``request_sidecar_unload(["wan"])`` and
verifies VRAM dropped through /health probing.

Why this lives here, not under tests/unit:

* It speaks real HTTP to a real GPU sidecar.
* It mutates wan-server's VRAM state. Running it in CI without an
  isolated sidecar would race the rest of the pipeline.
* It needs the same skip-if-unreachable contract the other live
  integrations use (Pexels, Cloudinary, etc.).
"""
from __future__ import annotations

import os

import httpx
import pytest

from services.gpu_scheduler import GPUScheduler
from services.site_config import SiteConfig


_WAN_URL = os.environ.get("WAN_SERVER_URL", "http://localhost:9840")


def _wan_reachable() -> bool:
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{_WAN_URL}/health")
        return resp.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _wan_reachable(),
    reason="wan-server not reachable at %s" % _WAN_URL,
)


@pytest.mark.asyncio
async def test_cooperative_unload_drops_wan_vram() -> None:
    """End-to-end: warm wan-server, request unload, verify VRAM dropped.

    Steps:
        1. Hit /health to read current VRAM.
        2. Issue a tiny /generate to ensure the model is loaded
           (skipped when /health already reports 'ready').
        3. Read VRAM again — should be > 0.
        4. Call gpu.request_sidecar_unload(["wan"]).
        5. Read VRAM again — should be lower than step 3.
    """
    scheduler = GPUScheduler(
        site_config=SiteConfig(
            initial_config={"wan_server_url": _WAN_URL},
        ),
    )

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

        warm_health = (await client.get(f"{_WAN_URL}/health")).json()
        vram_before = int(warm_health.get("vram_used_mb") or 0)
        if vram_before <= 0:
            pytest.skip(
                "wan-server reports vram_used_mb=0 — no model loaded, "
                "nothing to unload",
            )

        results = await scheduler.request_sidecar_unload(["wan"])
        assert results["wan"]["ok"] is True, (
            f"unload request failed: {results['wan']}"
        )

        post_health = (await client.get(f"{_WAN_URL}/health")).json()
        vram_after = int(post_health.get("vram_used_mb") or 0)

    assert vram_after < vram_before, (
        f"VRAM did not drop after /unload: before={vram_before}MB "
        f"after={vram_after}MB"
    )
