"""image-gen render retry — inline and featured/hero paths (2026-07-28).

The dominant render failure is a WINDOW, not a verdict: image-gen exits and
restarts to hand VRAM back for a video render (cold start + lazy model
reload), or the GPU lock times out under contention. Both clear in seconds.
Before the retry, one unlucky POST landing in that window silently became a
stock photo for the life of the post.

Pinned here: a transient failure is retried and recovers; a persistent one
still gives up (bounded, never a hang); the inline retry stays INSIDE the
caller's single image-gen GPU lock so it can't reintroduce the per-image lock
churn that poindexter#733 / #841 removed. The hero path retries around its own
call — one image, so at most one extra lock acquisition and no batching
invariant to protect.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig


def _sc(**overrides):
    base = {"image_gen_render_attempts": "2", "image_gen_retry_backoff_seconds": "0"}
    base.update(overrides)
    return SiteConfig(initial_config=base)


def _resp(status: int):
    r = MagicMock()
    r.status_code = status
    return r


async def _render(client, sc, attempts=2):
    from modules.content.atoms._image_helpers import _render_one_with_retry

    return await _render_one_with_retry(
        client, num="1", prompt="a glowing server rack", neg_prompt="",
        image_gen_url="http://image-gen:9836", task_id="t-1",
        render_timeout=90, site_config=sc, attempts=attempts,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_transient_failure_recovers_on_retry():
    """The exact production shape: first POST hits the restart window and
    fails, the second succeeds. Previously this post shipped stock art."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=[ConnectionError("connection refused"), _resp(200)])
    with patch(
        "modules.content.atoms._image_helpers._resolve_gen_response",
        AsyncMock(return_value="/tmp/x.png"),
    ), patch(
        "modules.content.atoms._image_helpers._upload_to_r2_with_fallback",
        AsyncMock(return_value="https://cdn.example/owned.webp"),
    ), patch("modules.content.atoms._image_helpers.asyncio.sleep", AsyncMock()):
        out = await _render(client, _sc())

    assert out == "https://cdn.example/owned.webp"
    assert client.post.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_200_is_retried_too():
    """A 5xx from a half-warm server is just as transient as a refused
    connection — both are the restart window."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=[_resp(503), _resp(200)])
    with patch(
        "modules.content.atoms._image_helpers._resolve_gen_response",
        AsyncMock(return_value="/tmp/x.png"),
    ), patch(
        "modules.content.atoms._image_helpers._upload_to_r2_with_fallback",
        AsyncMock(return_value="https://cdn.example/owned.webp"),
    ), patch("modules.content.atoms._image_helpers.asyncio.sleep", AsyncMock()):
        out = await _render(client, _sc())

    assert out == "https://cdn.example/owned.webp"
    assert client.post.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persistent_failure_gives_up_bounded():
    """Retry must be bounded — a genuinely dead server returns None rather
    than looping. None is what hands control to the (gated) fallback."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=ConnectionError("down"))
    with patch("modules.content.atoms._image_helpers.asyncio.sleep", AsyncMock()):
        out = await _render(client, _sc(), attempts=3)

    assert out is None
    assert client.post.await_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_first_attempt_success_does_not_retry():
    """No extra GPU time on the happy path."""
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(200))
    with patch(
        "modules.content.atoms._image_helpers._resolve_gen_response",
        AsyncMock(return_value="/tmp/x.png"),
    ), patch(
        "modules.content.atoms._image_helpers._upload_to_r2_with_fallback",
        AsyncMock(return_value="https://cdn.example/owned.webp"),
    ):
        out = await _render(client, _sc())

    assert out == "https://cdn.example/owned.webp"
    assert client.post.await_count == 1


@pytest.mark.unit
def test_retry_lives_inside_the_single_gpu_lock():
    """Guard against the obvious regression: re-acquiring the image_gen lock
    per attempt would undo the batching win of poindexter#733 / #841. The
    retry helper must not touch gpu.lock at all — its caller holds it."""
    import inspect

    from modules.content.atoms import _image_helpers

    src = inspect.getsource(_image_helpers._render_one_with_retry)
    assert "gpu.lock" not in src


# ---------------------------------------------------------------------------
# Featured/hero path — same retry, same reason
# ---------------------------------------------------------------------------
# The hero had NO retry at all until 2026-07-28: a single _render_image_gen
# call, and any None went straight to the (now gated) stock fallback. Five of
# the stock heroes observed over the 21 days to 2026-07-28 came through here.


@pytest.mark.unit
@pytest.mark.asyncio
async def test_featured_render_retries_before_conceding():
    """A transient hero render failure must be retried, not conceded."""
    from modules.content.stages import source_featured_image as sfi

    render = AsyncMock(side_effect=[(None, None), ("/tmp/hero.png", {"seed": 7})])
    with patch.object(sfi, "_render_image_gen", render), \
         patch.object(sfi, "_upload_featured_to_r2",
                      AsyncMock(return_value="https://cdn.example/hero.webp")), \
         patch.object(sfi, "_build_image_gen_prompt",
                      AsyncMock(return_value=("a glowing server rack", "text"))), \
         patch.object(sfi.asyncio, "sleep", AsyncMock()):
        out = await sfi._try_image_gen_featured(
            subject="observability", existing_prompt="a glowing server rack",
            task_id="t-1", on_style_picked=lambda _s: None, style_tracker=None,
            site_config=_sc(), platform=MagicMock(),
        )

    assert out is not None
    assert out.url == "https://cdn.example/hero.webp"
    assert render.await_count == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_featured_render_gives_up_after_attempts():
    """Bounded — a persistently dead server still returns None."""
    from modules.content.stages import source_featured_image as sfi

    render = AsyncMock(return_value=(None, None))
    with patch.object(sfi, "_render_image_gen", render), \
         patch.object(sfi, "_build_image_gen_prompt",
                      AsyncMock(return_value=("a glowing server rack", "text"))), \
         patch.object(sfi.asyncio, "sleep", AsyncMock()):
        out = await sfi._try_image_gen_featured(
            subject="observability", existing_prompt="a glowing server rack",
            task_id="t-1", on_style_picked=lambda _s: None, style_tracker=None,
            site_config=_sc(), platform=MagicMock(),
        )

    assert out is None
    assert render.await_count == 2
