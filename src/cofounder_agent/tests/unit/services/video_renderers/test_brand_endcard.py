"""On-theme end-card renderer (2026-08-26).

Pins the brand card's contract: site-gradient field, deterministic motif,
logo white-background keying that PRESERVES interior white details, and the
never-fail degradation chain (missing logo / fonts / bad inputs still render;
total failure returns False so the caller falls back to the plain card).
"""

from __future__ import annotations

from PIL import Image

from services.video_renderers.brand_endcard import (
    _keyed_logo,
    render_endcard,
)


def _render(tmp_path, **overrides):
    kwargs = dict(
        output_path=str(tmp_path / "card.png"),
        width=540, height=960,
        wordmark="Glad Labs", tagline="gladlabs.io", logo_path="",
    )
    kwargs.update(overrides)
    ok = render_endcard(**kwargs)
    return ok, kwargs["output_path"]


class TestRenderEndcard:
    def test_renders_gradient_field(self, tmp_path):
        ok, path = _render(tmp_path)
        assert ok
        img = Image.open(path).convert("RGB")
        # Site slate gradient: bottom row measurably lighter than top row.
        top = img.getpixel((270, 4))
        bottom = img.getpixel((270, 955))
        assert sum(bottom) > sum(top)
        # Field stays in the dark slate family (no washed-out background).
        assert sum(top) < 120

    def test_wordmark_present_in_upper_block_portrait(self, tmp_path):
        ok, path = _render(tmp_path)
        assert ok
        img = Image.open(path).convert("RGB")
        px = img.load()

        def cyanish_rows(y0, y1):
            n = 0
            for y in range(y0, y1, 2):
                for x in range(0, 540, 4):
                    r, g, b = px[x, y]
                    if g > 150 and b > 150 and r < 120:
                        n += 1
                        break
            return n

        assert cyanish_rows(int(960 * 0.12), int(960 * 0.45)) > 10
        # The portrait caption band (middle) stays free of bright lockup
        # pixels — burned captions land there.
        assert cyanish_rows(int(960 * 0.47), int(960 * 0.58)) == 0

    def test_deterministic_output(self, tmp_path):
        _, p1 = _render(tmp_path, output_path=str(tmp_path / "a.png"))
        _, p2 = _render(tmp_path, output_path=str(tmp_path / "b.png"))
        assert Image.open(p1).tobytes() == Image.open(p2).tobytes()

    def test_missing_logo_still_renders(self, tmp_path):
        ok, path = _render(tmp_path, logo_path=str(tmp_path / "nope.png"))
        assert ok
        assert Image.open(path).size == (540, 960)

    def test_logo_composited_when_present(self, tmp_path):
        logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        for x in range(20, 80):
            for y in range(20, 80):
                logo.putpixel((x, y), (255, 140, 0, 255))  # orange block
        lp = tmp_path / "logo.png"
        logo.save(lp)
        ok, path = _render(tmp_path, logo_path=str(lp))
        assert ok
        img = Image.open(path).convert("RGB")
        found = any(
            img.getpixel((x, y))[0] > 180 and img.getpixel((x, y))[1] > 80
            for y in range(0, 400, 3) for x in range(150, 390, 3)
        )
        assert found  # the orange mark landed in the upper lockup block

    def test_blank_wordmark_and_tagline_render(self, tmp_path):
        ok, _ = _render(tmp_path, wordmark="", tagline="")
        assert ok

    def test_landscape_renders(self, tmp_path):
        ok, path = _render(tmp_path, width=960, height=540)
        assert ok
        assert Image.open(path).size == (960, 540)


class TestLogoKeying:
    def test_white_background_keyed_but_interior_white_survives(self, tmp_path):
        # White field, navy square, WHITE dot INSIDE the navy square. The
        # border-connected white must go transparent; the interior white dot
        # must stay opaque (the flood never reaches it).
        logo = Image.new("RGB", (120, 120), (255, 255, 255))
        for x in range(30, 90):
            for y in range(30, 90):
                logo.putpixel((x, y), (10, 26, 47))
        for x in range(55, 65):
            for y in range(55, 65):
                logo.putpixel((x, y), (255, 255, 255))
        lp = tmp_path / "white_bg.png"
        logo.save(lp)
        keyed = _keyed_logo(str(lp))
        assert keyed is not None
        assert keyed.getpixel((2, 2))[3] == 0          # background gone
        assert keyed.getpixel((60, 60))[3] > 200       # interior white kept
        assert keyed.getpixel((40, 40))[3] > 200       # artwork kept

    def test_transparent_logo_passes_through(self, tmp_path):
        logo = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
        logo.putpixel((25, 25), (255, 255, 255, 255))
        lp = tmp_path / "alpha.png"
        logo.save(lp)
        keyed = _keyed_logo(str(lp))
        assert keyed is not None
        assert keyed.getpixel((0, 0))[3] == 0
        assert keyed.getpixel((25, 25))[3] == 255

    def test_missing_and_garbage_paths_return_none(self, tmp_path):
        assert _keyed_logo("") is None
        assert _keyed_logo(str(tmp_path / "missing.png")) is None
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not an image")
        assert _keyed_logo(str(bad)) is None
