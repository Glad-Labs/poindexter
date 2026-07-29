"""Stock-fallback gate + downgrade findings for the image path (2026-07-28).

Both the inline atom and the featured stage are image-gen primary with Pexels
as a FALLBACK — stock was never a preference. But the fallback was silent: it
logged per-image and still reported success, so a run that swapped owned art
for stock was indistinguishable from a clean one. It ran that way for weeks
before anyone noticed, and only then because the drafts looked off.

Two changes are pinned here:

1. ``image_stock_fallback_enabled`` (default FALSE) gates the fallback. Off
   means no image rather than an undisclosed stock substitution. It is a
   setting, not a deletion, so a fork that wants stock can flip it on.
2. Either outcome emits a warn-severity ``image_gen_downgrade`` finding, so
   the degraded path announces itself instead of passing as a clean run.

Deliberate stock — the video director choosing Pexels for a shot that needs
real photography, per the image-media policy — is a different path entirely
and is NOT gated by this.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.site_config import SiteConfig


def _sc(**overrides):
    base = {"alt_text_budget": "120"}
    base.update(overrides)
    return SiteConfig(initial_config=base)


def _state(sc):
    return {
        "image_plans": [{"num": "1", "desc": "a glowing server rack"}],
        "topic": "observability",
        "task_id": "t-1",
        "site_config": sc,
        "platform": MagicMock(),
    }


async def _run_atom(state):
    mod = __import__(
        "modules.content.atoms.content_generate_images", fromlist=["run"],
    )
    return await mod.run(state)


@pytest.mark.unit
def test_stock_fallback_defaults_off():
    """Default OFF is the point — owned imagery is the brand asset, and the
    silent substitution is what made this invisible."""
    from modules.content.atoms.content_generate_images import _stock_fallback_enabled

    assert _stock_fallback_enabled(_sc()) is False
    assert _stock_fallback_enabled(None) is False
    assert _stock_fallback_enabled(_sc(image_stock_fallback_enabled="true")) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_render_with_stock_disabled_yields_no_image_and_a_finding():
    """image-gen fails + stock off → no image, no Pexels call, and a finding."""
    emit = MagicMock()
    pexels = AsyncMock()
    with patch(
        "modules.content.atoms._image_helpers.batch_generate_inline_image_urls",
        AsyncMock(return_value=[None]),
    ), patch(
        "modules.content.atoms._image_helpers.try_pexels", pexels,
    ), patch(
        "modules.content.atoms._image_helpers.record_inline_image_asset", AsyncMock(),
    ), patch("utils.findings.emit_finding", emit):
        out = await _run_atom(_state(_sc()))

    assert out["image_results"][0]["url"] is None
    assert out["image_results"][0]["source"] == "none"
    pexels.assert_not_awaited(), "stock must not be consulted while gated off"
    assert emit.call_count == 1
    assert emit.call_args.kwargs["kind"] == "image_gen_downgrade"
    assert emit.call_args.kwargs["severity"] == "warn"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_render_with_stock_enabled_uses_pexels_but_still_reports():
    """Opting back in restores stock — but it is still a downgrade, so it is
    still announced. A fork enabling this should not lose the signal."""
    emit = MagicMock()
    with patch(
        "modules.content.atoms._image_helpers.batch_generate_inline_image_urls",
        AsyncMock(return_value=[None]),
    ), patch(
        "modules.content.atoms._image_helpers.try_pexels",
        AsyncMock(return_value=("https://images.pexels.com/x.jpg", "Ada L")),
    ), patch(
        "modules.content.atoms._image_helpers.record_inline_image_asset", AsyncMock(),
    ), patch("utils.findings.emit_finding", emit):
        out = await _run_atom(_state(_sc(image_stock_fallback_enabled="true")))

    assert out["image_results"][0]["source"] == "pexels"
    assert emit.call_count == 1
    assert emit.call_args.kwargs["kind"] == "image_gen_downgrade"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_successful_render_emits_no_finding():
    """The happy path must stay quiet, or the finding is just noise."""
    emit = MagicMock()
    with patch(
        "modules.content.atoms._image_helpers.batch_generate_inline_image_urls",
        AsyncMock(return_value=["https://cdn.example/owned.webp"]),
    ), patch(
        "modules.content.atoms._image_helpers.record_inline_image_asset", AsyncMock(),
    ), patch("utils.findings.emit_finding", emit):
        out = await _run_atom(_state(_sc()))

    assert out["image_results"][0]["source"] == "image_gen"
    emit.assert_not_called()
