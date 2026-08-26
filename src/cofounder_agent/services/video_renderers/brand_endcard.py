"""On-theme CTA end-card renderer (2026-08-26).

The first end-card was the fallback ladder's utility card — flat navy +
wordmark — functional, but the operator asked for "appealing and on theme".
This module renders the brand version, anchored to the public site's actual
design system (``web/public-site/tailwind.config.cjs``):

- **Field**: the site's surface gradient — slate-950 ``#030712`` →
  slate-925 ``#0a0f1f`` — with a soft cyan radial bloom behind the lockup
  (the site's ``glow-cyan`` box-shadow, as a backdrop).
- **Motif**: a few smooth glowing streams with drifting particles, echoing
  the site's og-image (dark field, flowing cyan/teal light trails) and the
  pipeline's own "glowing data stream" shot language. Deterministic — a
  fixed-seed RNG, so every render of the card is pixel-identical.
- **Type**: the site's faces, bundled as OFL variable fonts under
  ``assets/fonts`` — Sora (weight 600) for the wordmark, Inter for the
  tagline. DejaVu → PIL-default fallbacks keep the no-font case rendering.
- **Logo**: an operator-local image (``video_endcard_logo_path``; kept OUT
  of the repo so operator branding never reaches the public mirror). RGB
  logos on white get auto-keyed by corner flood-fill — interior white
  details survive because only the border-connected background region is
  made transparent.

Never-fail contract: this module aims high but the caller falls back to the
plain ``_render_brand_card`` when ``render_endcard`` returns False — the
guaranteed floor stays the floor. Pure PIL; no network, no GPU, no ffmpeg.

Caption safe zones (the burn happens AFTER composition): portrait captions
sit middle-center, so the lockup lives in the top ~40% and the motif hugs
the lower third; landscape captions sit in the bottom band, so the lockup
centers slightly high and the motif stays low-alpha everywhere.
"""

from __future__ import annotations

import logging
import os
import random
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Site palette (web/public-site/tailwind.config.cjs + the shared card cyan).
_FIELD_TOP = (3, 7, 18)        # slate-950 #030712
_FIELD_BOTTOM = (10, 15, 31)   # slate-925 #0a0f1f
_CYAN = (34, 211, 238)         # #22D3EE — wordmark + glow accent
_TEAL = (45, 212, 191)         # #2DD4BF — stream/particle secondary
_BLUE = (59, 130, 246)         # #3B82F6 — the site's glow-blue
_SLATE_TEXT = (203, 213, 225)  # #CBD5E1 — tagline

_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"

# One fixed seed ⇒ the motif is deterministic; re-renders produce identical
# cards and golden-pixel tests stay stable.
_MOTIF_SEED = 20260826


def _load_font(filename: str, size: int, *, weight: int | None = None) -> Any:
    """Bundled variable font at ``size`` (optionally pinning the wght axis),
    degrading to DejaVu then PIL's default — every branch returns a font."""
    from PIL import ImageFont

    try:
        font = ImageFont.truetype(str(_FONT_DIR / filename), size=size)
        if weight is not None:
            try:
                font.set_variation_by_axes([weight])
            except Exception:  # noqa: BLE001 — static-FreeType Pillow: keep default instance
                # silent-ok: weight pinning is cosmetic; the default instance
                # of the same face is the correct degradation.
                pass
        return font
    except OSError:
        # silent-ok: the bundled font missing/unreadable is the expected
        # trigger for the DejaVu tier below — degradation IS the handling.
        pass
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _gradient_field(width: int, height: int) -> Any:
    """Vertical slate-950 → slate-925 gradient via a 1×N strip resize."""
    from PIL import Image

    strip = Image.new("RGB", (1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        strip.putpixel((0, y), tuple(
            round(a + (b - a) * t)
            for a, b in zip(_FIELD_TOP, _FIELD_BOTTOM, strict=True)
        ))
    return strip.resize((width, height))


def _radial_bloom(size: tuple[int, int], center: tuple[float, float],
                  radius: float, color: tuple[int, int, int], peak: int) -> Any:
    """A soft radial glow layer (RGBA) — blurred filled circle."""
    from PIL import Image, ImageDraw, ImageFilter

    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = center
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=color + (peak,),
    )
    return layer.filter(ImageFilter.GaussianBlur(radius=radius * 0.55))


def _flow_motif(width: int, height: int, *, portrait: bool) -> Any:
    """Glowing streams + particles echoing the og-image, as an RGBA layer.

    Streams are smooth quadratic arcs sweeping across the lower region
    (portrait keeps the middle caption band clean; landscape stays low-alpha
    under the bottom caption band). Drawn twice: a wide blurred pass for the
    glow, then a crisp thin pass on top.
    """
    from PIL import Image, ImageDraw, ImageFilter

    rng = random.Random(_MOTIF_SEED)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    crisp = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(crisp)

    def bezier(p0, p1, p2, steps=60):
        return [
            (
                (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t**2 * p2[0],
                (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t**2 * p2[1],
            )
            for t in (s / steps for s in range(steps + 1))
        ]

    y_lo = 0.62 if portrait else 0.68  # streams live below this fraction
    # (landscape keeps them clear of the tagline; the bottom caption band
    # tolerates the low-alpha crossings — burned captions carry their own
    # outline + shadow)
    n_streams = 4
    for i in range(n_streams):
        color = _CYAN if i % 2 == 0 else _TEAL
        y0 = height * (y_lo + rng.uniform(0.05, 0.32))
        y2 = height * (y_lo + rng.uniform(0.05, 0.32))
        ctrl_y = height * (y_lo + rng.uniform(-0.06, 0.30))
        pts = bezier(
            (-width * 0.15, y0),
            (width * rng.uniform(0.30, 0.70), ctrl_y),
            (width * 1.15, y2),
        )
        w_glow = max(6, round(width * 0.012))
        gdraw.line(pts, fill=color + (70,), width=w_glow, joint="curve")
        cdraw.line(pts, fill=color + (150,), width=max(2, w_glow // 4), joint="curve")
        # Particles drifting off the stream.
        for _ in range(6):
            px, py = pts[rng.randrange(10, len(pts) - 1)]
            py -= rng.uniform(0, height * 0.05)
            r = rng.uniform(1.5, max(2.5, width * 0.004))
            cdraw.ellipse(
                (px - r, py - r, px + r, py + r),
                fill=color + (rng.randrange(90, 200),),
            )

    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(4, width * 0.008)))
    return Image.alpha_composite(glow, crisp)


def _keyed_logo(logo_path: str) -> Any | None:
    """Load the operator logo as RGBA, flood-keying a white background.

    Only the border-connected background becomes transparent (corner
    flood-fill with tolerance), so white details INSIDE the artwork —
    circuit dots, rings — survive. Already-transparent RGBA logos pass
    through. Returns None when unusable (missing file, decode error).
    """
    from PIL import Image, ImageDraw, ImageFilter

    if not logo_path or not os.path.exists(logo_path):
        return None
    try:
        logo = Image.open(logo_path).convert("RGBA")
    except Exception as exc:  # noqa: BLE001 — a bad logo must not kill the card
        logger.warning("[ENDCARD] logo unreadable at %s: %s", logo_path, exc)
        return None
    alpha = logo.getchannel("A")
    if alpha.getextrema()[0] < 255:
        return logo  # real transparency already present
    # RGB-on-white: flood the background from each corner with a sentinel,
    # then turn the sentinel transparent and lightly feather the edge.
    sentinel = (255, 0, 255, 255)
    keyed = logo.copy()
    w, h = keyed.size
    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        try:
            ImageDraw.floodfill(keyed, corner, sentinel, thresh=45)
        except (ValueError, RecursionError):
            continue
    px = keyed.load()
    mask_needed = False
    for y in range(h):
        for x in range(w):
            if px[x, y][:3] == sentinel[:3]:
                px[x, y] = (0, 0, 0, 0)
                mask_needed = True
    if not mask_needed:
        return logo
    a = keyed.getchannel("A").filter(ImageFilter.GaussianBlur(radius=0.8))
    keyed.putalpha(a)
    return keyed


def render_endcard(
    *,
    output_path: str,
    width: int,
    height: int,
    wordmark: str,
    tagline: str = "",
    logo_path: str = "",
) -> bool:
    """Render the on-theme end-card PNG. False ⇒ caller uses the plain card."""
    try:
        from PIL import Image, ImageDraw, ImageFilter

        width, height = max(64, width), max(64, height)
        portrait = height > width
        card = _gradient_field(width, height).convert("RGBA")

        # Lockup geometry: logo above wordmark above divider above tagline,
        # vertically centered as a block around block_cy. Portrait sizes the
        # wordmark off width (the narrow dimension); landscape off height —
        # width/9 of 1920 dominated the frame and pushed the tagline into
        # the stream band.
        block_cy = height * (0.27 if portrait else 0.37)
        wm_px = max(48, width // 9 if portrait else round(height * 0.155))
        logo = _keyed_logo(logo_path)
        logo_h = 0
        if logo is not None:
            target_w = round(width * (0.34 if portrait else 0.17))
            ratio = target_w / logo.width
            logo = logo.resize((target_w, max(1, round(logo.height * ratio))))
            logo_h = logo.height

        # Backdrop bloom behind the lockup (site glow-cyan as ambience) and
        # a faint blue counter-bloom low-opposite for depth.
        card = Image.alpha_composite(card, _radial_bloom(
            (width, height), (width / 2, block_cy),
            radius=width * (0.42 if portrait else 0.26), color=_CYAN, peak=26,
        ))
        card = Image.alpha_composite(card, _radial_bloom(
            (width, height), (width * 0.85, height * 0.92),
            radius=width * 0.35, color=_BLUE, peak=16,
        ))
        card = Image.alpha_composite(
            card, _flow_motif(width, height, portrait=portrait),
        )

        draw = ImageDraw.Draw(card)
        wm_text = (wordmark or "").strip()
        tag_text = (tagline or "").strip()
        wm_font = _load_font("Sora.ttf", wm_px, weight=600)
        tag_font = _load_font("Inter.ttf", max(20, round(wm_px * 0.38)))

        # Measure the block to center it on block_cy.
        gap_logo = round(wm_px * 0.55)
        gap_div = round(wm_px * 0.42)
        wm_box = draw.textbbox((0, 0), wm_text, font=wm_font) if wm_text else (0, 0, 0, 0)
        wm_h = wm_box[3] - wm_box[1]
        tag_box = draw.textbbox((0, 0), tag_text, font=tag_font) if tag_text else (0, 0, 0, 0)
        tag_h = tag_box[3] - tag_box[1]
        divider_h = max(3, wm_px // 18) if tag_text else 0
        block_h = (
            logo_h + (gap_logo if logo_h and wm_text else 0)
            + wm_h + (gap_div if divider_h else 0)
            + divider_h + (gap_div if tag_text else 0) + tag_h
        )
        y = block_cy - block_h / 2

        if logo is not None:
            # Soft glow behind the mark, then the mark itself.
            lg = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            lg.paste(logo, (round((width - logo.width) / 2), round(y)), logo)
            card = Image.alpha_composite(
                card, lg.filter(ImageFilter.GaussianBlur(radius=10)),
            )
            card = Image.alpha_composite(card, lg)
            y += logo_h + (gap_logo if wm_text else 0)
            draw = ImageDraw.Draw(card)

        if wm_text:
            wx = (width - (wm_box[2] - wm_box[0])) / 2 - wm_box[0]
            wy = y - wm_box[1]
            glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            ImageDraw.Draw(glow).text((wx, wy), wm_text, font=wm_font,
                                      fill=_CYAN + (160,))
            card = Image.alpha_composite(
                card, glow.filter(ImageFilter.GaussianBlur(radius=max(6, wm_px // 12))),
            )
            draw = ImageDraw.Draw(card)
            draw.text((wx, wy), wm_text, font=wm_font, fill=_CYAN)
            y += wm_h + (gap_div if divider_h else 0)

        if divider_h:
            dw = round(width * 0.13)
            draw.line(
                [((width - dw) / 2, y), ((width + dw) / 2, y)],
                fill=_CYAN + (110,), width=divider_h,
            )
            y += divider_h + gap_div

        if tag_text:
            tx = (width - (tag_box[2] - tag_box[0])) / 2 - tag_box[0]
            draw.text((tx, y - tag_box[1]), tag_text, font=tag_font, fill=_SLATE_TEXT)

        card.convert("RGB").save(output_path, format="PNG")
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as exc:  # noqa: BLE001 — the plain card is the guaranteed floor
        logger.warning("[ENDCARD] on-theme render failed (%s) — falling back "
                       "to the plain brand card", exc)
        return False


__all__ = ["render_endcard"]
