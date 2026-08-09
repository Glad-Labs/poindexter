"""Brand hero composition — deterministic, on-brand, no diffusion.

Why this exists: asked for a branded hero, image-gen produced the words
"Poindexter Philosophy" in mangled letterforms over two people with
six-fingered hands, violating the no-text AND no-people rules in
blog-generation/SKILL.md in a single image. Type has to be set, not generated.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.brand_hero import (
    BRAND,
    HERO_HEIGHT,
    HERO_WIDTH,
    HeroSpec,
    render_hero_html,
)

# tests/unit/services/ → tests → cofounder_agent → src → repo root.
# Off-by-one here makes the drift guard skip silently instead of failing —
# the exact fail-open-while-reporting-success shape it is meant to catch.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_TOKENS_CSS = _REPO_ROOT / "packages" / "brand" / "src" / "tokens" / "colors.css"


def test_palette_matches_brand_tokens():
    """BRAND must not drift from packages/brand/src/tokens/colors.css.

    That CSS is a JS-package asset outside src/cofounder_agent, so it is not on
    the worker container's filesystem and cannot be read at runtime — the
    palette is mirrored in Python instead. This is the drift guard: it runs
    wherever the repo is checked out (host + CI) and skips in-container.
    """
    if not _TOKENS_CSS.exists():
        pytest.skip("packages/brand not present (container runtime)")

    css = _TOKENS_CSS.read_text(encoding="utf-8")
    declared = dict(re.findall(r"(--gl-[a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8})\s*;", css))

    expected = {
        "base": "--gl-base",
        "surface_2": "--gl-surface-2",
        "text": "--gl-text",
        "text_muted": "--gl-text-muted",
        "text_dim": "--gl-text-dim",
        "cyan": "--gl-cyan",
        "cyan_dim": "--gl-cyan-dim",
        "amber": "--gl-amber",
    }
    for key, token in expected.items():
        assert token in declared, f"{token} vanished from colors.css"
        assert BRAND[key].lower() == declared[token].lower(), (
            f"BRAND[{key!r}] is {BRAND[key]} but {token} is {declared[token]} — "
            "re-mirror the token"
        )


def test_renders_defaults():
    html = render_hero_html()
    assert "POINDEXTER" in html.upper()
    assert BRAND["cyan"] in html and BRAND["amber"] in html
    assert "JetBrains Mono" in html  # the installed brand mono face
    assert f"{HERO_WIDTH}px" in html and f"{HERO_HEIGHT}px" in html


def test_accent_split_colours_the_title_head():
    html = render_hero_html(HeroSpec(title="Poindexter", accent_chars=4))
    assert '<span class="gl">Poin</span>dexter' in html


def test_accent_split_clamps_to_title_length():
    html = render_hero_html(HeroSpec(title="GL", accent_chars=99))
    assert '<span class="gl">GL</span>' in html


def test_zero_accent_chars_leaves_title_plain():
    html = render_hero_html(HeroSpec(title="Poindexter", accent_chars=0))
    assert 'class="gl"' not in html
    assert "Poindexter" in html


def test_tagline_accent_is_highlighted_once():
    html = render_hero_html(
        HeroSpec(tagline="a gate is a gate", tagline_accent="gate")
    )
    assert html.count('<span class="em">gate</span>') == 1


def test_tagline_accent_absent_is_not_an_error():
    html = render_hero_html(HeroSpec(tagline="no highlight here", tagline_accent="zzz"))
    assert "no highlight here" in html
    assert 'class="em"' not in html


def test_stage_states_pick_the_right_node_styles():
    html = render_hero_html(HeroSpec(stages=(
        ("Research", "done"), ("Gate", "gate"), ("Publish", "pending"),
    )))
    assert "node on" in html      # done
    assert "node gate" in html    # gate
    assert "label lit" in html    # gate label in amber
    # Edges after the gate are dim — nothing has flowed past it.
    assert "edge dim" in html


def test_edges_before_the_gate_are_not_dim():
    html = render_hero_html(HeroSpec(stages=(("A", "done"), ("B", "done"))))
    assert "edge dim" not in html


def test_stage_count_drives_column_count():
    html = render_hero_html(HeroSpec(stages=(("One", "done"), ("Two", "pending"))))
    assert html.count('class="stage"') == 2
    # n stages → n-1 edges
    assert html.count('class="edge') == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", '<script>alert(1)</script>'),
        ("tagline", 'a & b <b>c</b>'),
        ("footer_left", '"><img onerror=x>'),
        ("eyebrow_org", "<i>x</i>"),
    ],
)
def test_operator_supplied_text_is_escaped(field, value):
    """Title/tagline reach this from CLI flags — they must not inject markup."""
    html = render_hero_html(HeroSpec(**{field: value, "accent_chars": 0}))
    # The raw markup must not survive as markup. Its characters may well
    # appear inside the escaped run (&lt;img onerror=x&gt;), which is inert —
    # so assert on the dangerous form, not on the substring.
    assert value not in html
    assert "&lt;" in html or "&amp;" in html or "&quot;" in html


def test_stage_labels_are_escaped():
    html = render_hero_html(HeroSpec(stages=(("<b>x</b>", "done"),)))
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;" in html


@pytest.mark.asyncio
async def test_render_png_returns_none_when_capture_fails(monkeypatch):
    """Mirrors capture_preview_screenshot's contract: None, never raises."""
    from services import brand_hero

    async def _capture(url, **kwargs):
        return None

    monkeypatch.setattr(
        "services.preview_screenshot.capture_preview_screenshot", _capture,
    )
    assert await brand_hero.render_hero_png() is None


@pytest.mark.asyncio
async def test_render_png_passes_hero_dimensions_and_cleans_up(monkeypatch):
    from services import brand_hero

    seen: dict[str, object] = {}

    async def _capture(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        # The temp file must still exist while chromium is reading it.
        assert url.startswith("file://")
        assert Path(url[len("file://"):]).exists()
        return b"\x89PNG\r\n\x1a\n fake"

    monkeypatch.setattr(
        "services.preview_screenshot.capture_preview_screenshot", _capture,
    )
    out = await brand_hero.render_hero_png()
    assert out == b"\x89PNG\r\n\x1a\n fake"
    assert seen["viewport_width"] == HERO_WIDTH
    assert seen["viewport_height"] == HERO_HEIGHT
    assert seen["full_page"] is False
    # Temp file removed after the render.
    assert not Path(str(seen["url"])[len("file://"):]).exists()
