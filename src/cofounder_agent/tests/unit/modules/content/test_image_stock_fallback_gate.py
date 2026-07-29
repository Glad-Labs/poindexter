"""Stock-fallback gate + downgrade findings for the image path (2026-07-28).

Both the inline atom and the featured stage are image-gen primary with Pexels
as a FALLBACK — stock was never a preference. But the fallback was silent: it
logged per-image and still reported success, so a run that swapped owned art
for stock was indistinguishable from a clean one. It ran that way for weeks
before anyone noticed, and only then because the drafts looked off.

Three changes are pinned here:

1. ``image_stock_fallback_enabled`` (default FALSE) gates the fallback. Off
   means no image rather than an undisclosed stock substitution. It is a
   setting, not a deletion, so a fork that wants stock can flip it on.
2. Either outcome emits a warn-severity ``image_gen_downgrade`` finding, so
   the degraded path announces itself instead of passing as a clean run.
3. The gate covers ALL THREE generation sites — the inline atom, the featured
   stage, and ``content.rebuild_featured_image`` on the image_rebuild path.
   The third was missed by the first pass and could still produce a silent
   stock hero; ``rebuild-images --allow-stock`` is its per-run override.

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
    from modules.content.atoms._image_helpers import stock_fallback_enabled

    assert stock_fallback_enabled(_sc()) is False
    assert stock_fallback_enabled(None) is False
    assert stock_fallback_enabled(_sc(image_stock_fallback_enabled="true")) is True


@pytest.mark.unit
def test_allow_stock_is_a_per_run_override():
    """`rebuild-images --allow-stock` must reach the GENERATION sites, not
    just the rebuild gate. Otherwise the flag relaxes the gate while the atoms
    quietly refuse to produce stock — a flag that no longer does what it says.
    """
    from modules.content.atoms._image_helpers import stock_fallback_enabled

    assert stock_fallback_enabled(_sc(), allow_stock=True) is True
    assert stock_fallback_enabled(None, allow_stock=True) is True
    # ...and it stays an override, not the default.
    assert stock_fallback_enabled(_sc(), allow_stock=False) is False


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


# ---------------------------------------------------------------------------
# image_rebuild path — content.rebuild_featured_image
# ---------------------------------------------------------------------------
# This atom was MISSED by the first pass of the gate (2026-07-28): it called
# try_pexels unconditionally, so the rebuild path could still produce a silent
# stock hero while the two content-pipeline paths refused to. Same gate, same
# finding, plus the per-run --allow-stock override.


async def _run_rebuild(state):
    mod = __import__(
        "modules.content.atoms.content_rebuild_featured_image", fromlist=["run"],
    )
    return await mod.run(state)


def _rebuild_state(sc, *, allow_stock=False):
    return {
        "topic": "observability",
        "featured_image_subject": "a glowing server rack",
        "task_id": "t-rebuild",
        "site_config": sc,
        "platform": MagicMock(),
        "image_service": MagicMock(),
        "allow_stock": allow_stock,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rebuild_hero_gated_off_skips_pexels_and_reports():
    emit = MagicMock()
    pexels = AsyncMock()
    with patch(
        "modules.content.atoms._image_helpers.try_image_gen",
        AsyncMock(return_value=None),
    ), patch(
        "modules.content.atoms._image_helpers.try_pexels", pexels,
    ), patch("utils.findings.emit_finding", emit):
        out = await _run_rebuild(_rebuild_state(_sc()))

    assert out["featured_source"] == "none"
    assert out["featured_image_url"] == ""
    pexels.assert_not_awaited(), "stock must not be searched while gated off"
    assert emit.call_args.kwargs["kind"] == "image_gen_downgrade"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rebuild_hero_allow_stock_reaches_generation():
    """--allow-stock must actually let this atom produce stock, not merely
    relax the downstream gate."""
    emit = MagicMock()
    with patch(
        "modules.content.atoms._image_helpers.try_image_gen",
        AsyncMock(return_value=None),
    ), patch(
        "modules.content.atoms._image_helpers.try_pexels",
        AsyncMock(return_value=("https://images.pexels.com/x.jpg", "Ada L")),
    ), patch("utils.findings.emit_finding", emit):
        out = await _run_rebuild(_rebuild_state(_sc(), allow_stock=True))

    assert out["featured_source"] == "pexels"
    assert out["featured_image_url"] == "https://images.pexels.com/x.jpg"
    # Still announced — an opted-in downgrade is still a downgrade.
    assert emit.call_args.kwargs["kind"] == "image_gen_downgrade"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rebuild_hero_success_is_quiet():
    emit = MagicMock()
    with patch(
        "modules.content.atoms._image_helpers.try_image_gen",
        AsyncMock(return_value="https://cdn.example/hero.webp"),
    ), patch("utils.findings.emit_finding", emit):
        out = await _run_rebuild(_rebuild_state(_sc()))

    assert out["featured_source"] == "image_gen"
    emit.assert_not_called()
