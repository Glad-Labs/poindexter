"""Caller-side handling of an OCR-gate render rejection (poindexter#1004).

The image-gen server blocks a text-leaking render with HTTP 422 and an
``{"detail": {"error": "ocr_gate_rejected", ...}}`` body. Two things have to
hold at every render call site:

  * the rejection is recognised as a *verdict*, so the client's own retry loop
    stops instead of buying a second full set of server-side seed re-rolls;
  * every other non-200 keeps its existing retry behaviour, because those
    really are transient windows (image-gen restarting for a VRAM reclaim, a
    GPU lock timing out).

Pinned here because the whole point of the fix is that a rejection reaches the
already-wired "no image-gen image" path — the one that falls back to Pexels
when enabled and otherwise emits an `image_gen_downgrade` finding. A rejection
that got retried into a timeout, or one that got swallowed, would defeat that.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.image_ocr_gate import (
    OCR_GATE_REJECTED_STATUS,
    describe_ocr_gate_rejection,
    is_ocr_gate_rejection,
    safe_json,
)
from services.site_config import SiteConfig

_REJECTION_BODY = {
    "detail": {
        "error": "ocr_gate_rejected",
        "ocr_gate_status": "fail",
        "ocr_text_chars": 20,
        "ocr_gate_attempts": 3,
        "threshold": 6,
        "model": "z_image_turbo",
        "message": "image-gen render blocked by the OCR text-leakage gate",
    },
}


def _resp(status: int, body=None):
    r = MagicMock()
    r.status_code = status
    r.json = MagicMock(return_value=body if body is not None else {})
    return r


# ---------------------------------------------------------------------------
# is_ocr_gate_rejection / describe / safe_json
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_recognises_the_rejection_shape():
    assert is_ocr_gate_rejection(OCR_GATE_REJECTED_STATUS, _REJECTION_BODY) is True


@pytest.mark.unit
def test_other_statuses_are_not_rejections():
    """503 is the restart window — retryable. Misreading it as a content
    verdict would stop the retry that recovers most renders."""
    for status in (200, 500, 503, 504):
        assert is_ocr_gate_rejection(status, _REJECTION_BODY) is False


@pytest.mark.unit
def test_unrelated_422_is_not_read_as_a_text_verdict():
    """Checked on the `error` marker, not the bare status, so a future
    validation 422 from this endpoint can't be reported as leaked text."""
    assert is_ocr_gate_rejection(422, {"detail": {"error": "something_else"}}) is False
    assert is_ocr_gate_rejection(422, {"detail": "flat string"}) is False
    assert is_ocr_gate_rejection(422, None) is False
    assert is_ocr_gate_rejection(422, "not json at all") is False


@pytest.mark.unit
def test_describe_includes_the_numbers_an_operator_needs():
    msg = describe_ocr_gate_rejection(_REJECTION_BODY)
    assert "fail" in msg
    assert "20" in msg      # chars found
    assert "6" in msg       # threshold
    assert "3" in msg       # server-side attempts already spent


@pytest.mark.unit
def test_describe_renders_unavailable_chars_as_a_word_not_zero():
    """`None` chars must not print as 0 anywhere — including in the log line
    an operator reads at 2am."""
    msg = describe_ocr_gate_rejection({
        "detail": {
            "error": "ocr_gate_rejected", "ocr_gate_status": "unavailable",
            "ocr_text_chars": None, "threshold": 6, "ocr_gate_attempts": 1,
        },
    })
    assert "unavailable" in msg
    assert "chars=0" not in msg


@pytest.mark.unit
def test_describe_degrades_on_a_malformed_body():
    assert "no detail returned" in describe_ocr_gate_rejection(None)
    assert "no detail returned" in describe_ocr_gate_rejection({"detail": 42})


@pytest.mark.unit
def test_safe_json_swallows_parse_failure():
    bad = MagicMock()
    bad.json = MagicMock(side_effect=ValueError("not json"))
    assert safe_json(bad) is None


# ---------------------------------------------------------------------------
# Inline batch path — modules/content/atoms/_image_helpers._render_one_with_retry
# ---------------------------------------------------------------------------

def _sc(**overrides):
    base = {"image_gen_render_attempts": "2", "image_gen_retry_backoff_seconds": "0"}
    base.update(overrides)
    return SiteConfig(initial_config=base)


async def _render_inline(client, attempts=3):
    from modules.content.atoms._image_helpers import _render_one_with_retry

    return await _render_one_with_retry(
        client, num="1", prompt="a glowing server rack", neg_prompt="",
        image_gen_url="http://image-gen:9836", task_id="t-1",
        render_timeout=90, site_config=_sc(), attempts=attempts,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inline_batch_does_not_retry_a_gate_rejection():
    """One POST, then give up. The server already re-rolled the seed
    image_ocr_gate_max_attempts times before returning 422."""
    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(422, _REJECTION_BODY))
    with patch("modules.content.atoms._image_helpers.asyncio.sleep", AsyncMock()):
        out = await _render_inline(client, attempts=3)

    assert out is None
    assert client.post.await_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inline_batch_still_retries_a_transient_non_200():
    """Guard the other direction — the 503 restart window must keep its
    retry, or this fix would trade one silent downgrade for another."""
    client = MagicMock()
    client.post = AsyncMock(side_effect=[_resp(503), _resp(200)])
    with patch(
        "modules.content.atoms._image_helpers._resolve_gen_response",
        AsyncMock(return_value="/tmp/x.png"),
    ), patch(
        "modules.content.atoms._image_helpers._upload_to_r2_with_fallback",
        AsyncMock(return_value="https://cdn.example/owned.webp"),
    ), patch("modules.content.atoms._image_helpers.asyncio.sleep", AsyncMock()):
        out = await _render_inline(client, attempts=3)

    assert out == "https://cdn.example/owned.webp"
    assert client.post.await_count == 2


# ---------------------------------------------------------------------------
# Featured/hero path — modules/content/stages/source_featured_image
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_featured_render_flags_a_gate_rejection_in_its_meta():
    """`_render_image_gen` returns (None, meta); the rejection has to travel
    in the meta because that tuple is all the retry loop above it sees."""
    from modules.content.stages import source_featured_image as sfi

    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(422, _REJECTION_BODY))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)

    lock = MagicMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=False)

    with patch.object(sfi.httpx, "AsyncClient", MagicMock(return_value=ctx)), \
            patch("services.gpu_scheduler.gpu.lock", MagicMock(return_value=lock)):
        path, meta = await sfi._render_image_gen(
            "http://image-gen:9836", "a server rack", "text, words",
            task_id="t-1", timeout_seconds=5,
        )

    assert path is None
    assert meta.get("ocr_gate_rejected") is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_featured_render_leaves_meta_unflagged_for_other_failures():
    """A 503 must NOT set the flag, or the hero path would stop retrying the
    restart window it was built to survive."""
    from modules.content.stages import source_featured_image as sfi

    client = MagicMock()
    client.post = AsyncMock(return_value=_resp(503))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)

    lock = MagicMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=False)

    with patch.object(sfi.httpx, "AsyncClient", MagicMock(return_value=ctx)), \
            patch("services.gpu_scheduler.gpu.lock", MagicMock(return_value=lock)):
        path, meta = await sfi._render_image_gen(
            "http://image-gen:9836", "a server rack", "text, words",
            task_id="t-1", timeout_seconds=5,
        )

    assert path is None
    assert meta.get("ocr_gate_rejected") is not True
