"""Contract guard for the image atoms + helpers behind the image_rebuild graph.

The image_rebuild template re-drives the image pipeline through
``content.plan_image_markers`` / ``content.generate_images`` /
``content.inject_images`` plus the ``try_image_gen`` / ``try_pexels`` helpers
(via the sanctioned ``modules.content.atoms._image_helpers`` seam), and its fail-loud
gate atom keys on ``source == "image_gen"``. These tests pin those signatures +
result shapes so a future edit to the image path can't silently break the
rebuild consumer.
"""

from __future__ import annotations

import inspect

import pytest

from services.site_config import SiteConfig


@pytest.mark.unit
def test_try_image_gen_signature_stable():
    from modules.content.atoms._image_helpers import try_image_gen, try_pexels

    p = inspect.signature(try_image_gen).parameters
    assert list(p)[:2] == ["num", "search_query"]
    assert "topic" not in p  # dropped — raw title was leaking into image-gen prompts
    assert {"site_config", "task_id", "platform"} <= set(p)
    assert list(inspect.signature(try_pexels).parameters)[:3] == [
        "search_query",
        "topic",
        "image_service",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plan_image_markers_contract_shape():
    from modules.content.atoms import content_plan_image_markers

    # model set so the marker-free fallback returns early offline (no pool, no
    # operator-notify); ImageRebuildService calls with exactly these state keys.
    sc = SiteConfig(
        initial_config={
            "writer_max_inline_images": "3",
            "model_role_image_decision": "ollama/test-model",
        },
    )
    out = await content_plan_image_markers.run(
        {"content": "plain body, no markers", "topic": "t", "site_config": sc},
    )
    assert set(out) >= {"content", "image_plans"}
    assert isinstance(out["image_plans"], list)


@pytest.mark.unit
def test_generate_images_result_shape_documented():
    """generate_images must keep per-item source ∈ {image_gen,pexels,none} — the
    rebuild fail-loud gate keys on ``source == 'image_gen'``."""
    src = inspect.getsource(
        __import__(
            "modules.content.atoms.content_generate_images", fromlist=["run"]
        ).run
    )
    assert '"source"' in src or "'source'" in src
