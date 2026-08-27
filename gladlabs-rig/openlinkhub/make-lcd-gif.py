#!/usr/bin/env python3
"""Generate the Glad Labs coolant-loop GIF for the XC7 ELITE LCD block (480x480).

The Glad Labs mark -- the controller dissolving into circuit traces -- rides at
12 o'clock, breathing, inside three concentric rings that sweep comet heads
around a dark field. Rings use the same thermal ramp as the
rest of the rig (mint -> amber -> orange), cool innermost and hot outermost,
matching how the temperature palette is mapped everywhere else.

This is a BACKGROUND: the block runs LCD mode 102, which composites three live
readouts on top of it. With three sensors enabled OpenLinkHub centres them at
baselines y~132 / 257 / 382 with labels at 172 / 297 / 422 (its own arithmetic:
paddingStart = -(sensors * 125) / 2 + margin, then +125 per sensor), i.e. text
covers nearly the whole vertical centre, which is why the logo sits at the top
rather than in the middle. Re-check those numbers before changing sensor count
or margin in lcd/animation.json.

The panel is physically round, so everything stays inside the inscribed circle
(r=240) with margin; corners are never seen. Every phase advances a whole number
of turns across the frame count, so the loop is seamless.

Regenerate: python3 make-lcd-gif.py [output-dir]  (install.sh writes it straight
into OpenLinkHub's image dir before restarting the service -- images are cached
at startup, so a running service needs /api/lcd/upload instead).
"""

import math
import os
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# Alphanumeric only -- OpenLinkHub's upload handler rejects a hyphen or
# underscore with "Invalid filename. Only letters and numbers allowed", and the
# stem is the name the LCD image is selected by.
OUT = "gladlabsloop.gif"
SIZE = 480
SS = 2  # supersample factor -- draw big, downsample for antialiasing
FRAMES = 60
FRAME_MS = 40  # 60 frames * 40ms = 2.4s loop

BASE = (10, 15, 20)
GRID = (26, 40, 50)
MINT = (0, 229, 214)
AMBER = (255, 184, 51)
ORANGE = (255, 128, 0)

# (radius, width, colour, comet count, turns per loop -- sign sets direction)
RINGS = (
    (95, 7, MINT, 1, 1),
    (145, 6, AMBER, 2, -1),
    (195, 5, ORANGE, 3, 1),
)

TRAIL_SEGS = 26  # comet tail resolution
TRAIL_DEG = 110  # tail length in degrees
REST_ALPHA = 34  # faint always-on ring, so the circuit reads at every phase
PALETTE_TILES = 8  # frames sampled to build the shared palette

# The Glad Labs logo: the controller-dissolving-into-circuit-traces mark from
# the site icon. Shipped as RGB on white, so it is flood-keyed from the corners
# the same way services/video_renderers/brand_endcard.py::_keyed_logo does --
# corner-connected white only, which leaves the white circuit dots INSIDE the
# artwork intact. It needs no recolouring for a dark panel: the navy body sits
# at luminance ~35 against a ~15 field, so it stays a readable silhouette while
# the mint traces carry the detail.
BRAND_CYAN = (34, 211, 238)  # #22D3EE
LOGO_SRC = Path(__file__).resolve().parents[2] / "web" / "public-site" / "public" / "icon-512.png"
# Mark is centred and large. The readouts (y~74-430) land straight on top of it,
# so LOGO_ALPHA holds it back far enough for the numbers to stay legible -- it
# reads as a brand emboss behind the instrument rather than a competing layer.
# 200px tall is near the ceiling: at 1.87 aspect that is 374 wide (+/-187), and
# the round crop allows only +/-218 at the mark's top and bottom edges.
LOGO_H = 200
LOGO_Y = 240
LOGO_ALPHA = 0.52


@lru_cache(maxsize=1)
def _logo_mark() -> Image.Image:
    """The controller mark, white-keyed and cropped away from the wordmark.

    Keyed once and cached: the flood-fill plus per-pixel sweep is ~260k pixels,
    and redoing it per frame would dominate the render.
    """
    if not LOGO_SRC.exists():
        raise SystemExit(
            f"logo not found at {LOGO_SRC}. This path is resolved relative to "
            "the script, so run it from its place in the repo (install.sh does)."
        )
    logo = Image.open(LOGO_SRC).convert("RGBA")
    if logo.getchannel("A").getextrema()[0] < 255:
        keyed = logo  # already transparent
    else:
        sentinel = (255, 0, 255, 255)
        keyed = logo.copy()
        w, h = keyed.size
        for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            try:
                ImageDraw.floodfill(keyed, corner, sentinel, thresh=45)
            except (ValueError, RecursionError):
                continue
        px = keyed.load()
        for y in range(h):
            for x in range(w):
                if px[x, y][:3] == sentinel[:3]:
                    px[x, y] = (0, 0, 0, 0)
        keyed.putalpha(keyed.getchannel("A").filter(ImageFilter.GaussianBlur(0.8)))

    # The artwork stacks the controller over a "GLAD LABS" wordmark, separated
    # by a band of empty rows. Split on that gap and keep the mark: at 60px tall
    # the wordmark would be ~8px and unreadable, so it earns no space.
    alpha = keyed.getchannel("A")
    w, h = keyed.size
    rows = [max(alpha.crop((0, y, w, y + 1)).getdata()) for y in range(h)]
    ink = [y for y, v in enumerate(rows) if v >= 8]
    gap_end = h
    for y in range(ink[0], ink[-1]):
        if rows[y] < 8 and all(rows[z] < 8 for z in range(y, min(y + 12, h))):
            gap_end = y
            break
    return keyed.crop(keyed.crop((0, 0, w, gap_end)).getbbox())


def draw_frame(t: float) -> Image.Image:
    """Render one frame. t is loop position in [0, 1)."""
    s = SIZE * SS
    img = Image.new("RGB", (s, s), BASE)
    d = ImageDraw.Draw(img, "RGBA")
    cx = cy = s / 2

    # dot grid, clipped to the visible circle -- same texture as the strip
    step = 24 * SS
    limit = 232 * SS
    for gy in range(int(cy % step), s, step):
        for gx in range(int(cx % step), s, step):
            if math.hypot(gx - cx, gy - cy) <= limit:
                d.point((gx, gy), fill=GRID + (255,))

    # outer tick ring -- instrument feel, 60 ticks
    for i in range(60):
        a = math.radians(i * 6)
        r0, r1 = 224 * SS, (231 if i % 5 else 234) * SS
        alpha = 150 if i % 5 == 0 else 55
        d.line(
            [
                (cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                (cx + r1 * math.cos(a), cy + r1 * math.sin(a)),
            ],
            fill=MINT + (alpha,),
            width=SS,
        )

    for radius, width, colour, comets, turns in RINGS:
        r = radius * SS
        w = width * SS
        box = [cx - r, cy - r, cx + r, cy + r]

        d.ellipse(box, outline=colour + (REST_ALPHA,), width=w)

        head = t * turns * 360.0
        for c in range(comets):
            base = head + c * (360.0 / comets)
            # Back to front: dimmest tail segment first, so each brighter
            # segment paints over the one behind it. Drawing head-first instead
            # lets the dim tail overpaint the head and the trail reads as dashes.
            for i in reversed(range(TRAIL_SEGS)):
                # tail trails *behind* the head, against the direction of travel
                span = TRAIL_DEG / TRAIL_SEGS
                lead = base - math.copysign(i * span, turns)
                a0, a1 = sorted((lead, lead - math.copysign(span * 1.9, turns)))
                fade = (1 - i / TRAIL_SEGS) ** 2
                alpha = int(235 * fade)
                if alpha <= 0:
                    continue
                d.arc(box, a0, a1, fill=colour + (alpha,), width=w)

    # Logo + bloom, breathing on the same clock as the ring sweep.
    pulse = 0.5 + 0.5 * math.cos(2 * math.pi * t)

    # Dark plate first: the rings pass straight behind the logo, so at some phases
    # a comet head sweeps through the artwork and visually cuts it. Knocking the
    # background back here keeps the mark whole without fighting the halo.
    plate_rx, plate_ry = 210 * SS, 118 * SS
    for k in range(24, 0, -1):
        f = k / 24
        rx, ry = int(plate_rx * f), int(plate_ry * f)
        d.ellipse(
            [cx - rx, LOGO_Y * SS - ry, cx + rx, LOGO_Y * SS + ry],
            fill=BASE + (16,),
        )

    # Halo, kept deliberately weak. There is no separate centre bloom any more:
    # the logo owns the centre, and a bright glow behind it washes out the middle
    # readout, which lands right here. Legibility is measured as the background
    # luminance under each text row, not eyeballed.
    halo_r = int(96 * (0.9 + 0.1 * pulse) * SS)
    for rr in range(halo_r, 0, -1):
        a = int((26 + 22 * pulse) * (1 - rr / halo_r) ** 2.6)
        if a > 0:
            d.ellipse(
                [cx - rr, LOGO_Y * SS - rr, cx + rr, LOGO_Y * SS + rr],
                fill=BRAND_CYAN + (a,),
            )

    mark = _logo_mark()
    mh = LOGO_H * SS
    mark = mark.resize((round(mark.width * mh / mark.height), mh), Image.LANCZOS)
    scale = LOGO_ALPHA * (0.86 + 0.14 * pulse)
    faded = mark.getchannel("A").point(lambda v: int(v * scale))
    mark = mark.copy()
    mark.putalpha(faded)
    img.paste(
        mark,
        (int(cx - mark.width / 2), int(LOGO_Y * SS - mark.height / 2)),
        mark,
    )

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def _assert_full_frames(path: str) -> None:
    """Fail loudly if any frame is a partial rect -- see the note in main()."""
    from PIL import ImageSequence

    want = (0, 0, SIZE, SIZE)
    bad = [
        i
        for i, frame in enumerate(ImageSequence.Iterator(Image.open(path)))
        if frame.tile and frame.tile[0][1] != want
    ]
    if bad:
        raise SystemExit(
            f"{path}: {len(bad)} of {FRAMES} frames are partial rects "
            f"(first: frame {bad[0]}). OpenLinkHub renders these as a strobe. "
            "Save with optimize=False."
        )


def main() -> None:
    frames = [draw_frame(i / FRAMES) for i in range(FRAMES)]

    # One shared palette for every frame, or the colours crawl between frames.
    # It has to be built from a montage spanning the WHOLE loop: derive it from
    # a single frame and every brightness that frame doesn't contain -- the
    # sphere at full pulse, rings at other angles -- has no entry to land on,
    # and dithering scatters it into speckle that reshuffles every frame. That
    # reads as a strobe, with only the frames nearest the sampled one solid.
    tiles = [frames[i * FRAMES // PALETTE_TILES] for i in range(PALETTE_TILES)]
    montage = Image.new("RGB", (SIZE, SIZE * PALETTE_TILES))
    for i, tile in enumerate(tiles):
        montage.paste(tile, (0, i * SIZE))
    palette = montage.quantize(colors=256, method=Image.MEDIANCUT)
    # No dithering: the palette above already covers this ramp, and dither noise
    # on a near-black field shimmers frame to frame.
    frames = [f.quantize(palette=palette, dither=Image.NONE) for f in frames]

    path = os.path.join(sys.argv[1] if len(sys.argv) > 1 else ".", OUT)
    # disposal=2 is LOAD-BEARING: every frame must be a full 480x480 rect.
    # OpenLinkHub's decoder cannot cope with partial frames -- animation.go does
    # `canvas := image.NewRGBA(pf.Bounds())` per frame, sizing the canvas to that
    # frame's own bounds, never compositing against the previous frame nor
    # honouring GIF disposal. Partial frames render as garbage that churns every
    # frame; on the block that reads as a strobe. (Corsair's bundled
    # concentric.gif is all full-frame and renders fine; openlinkhub.gif is not,
    # and misrenders the same way.)
    #
    # optimize=False alone does NOT achieve this. Pillow's frame differencing is
    # separate from `optimize` -- it crops each frame to the delta bbox unless
    # the PREVIOUS frame's disposal is 2 ("restore to background"), which is the
    # one setting that forces a full-rect write. Frames here are fully opaque
    # and repaint the whole canvas, so disposal=2 costs nothing visually.
    #
    # None of this is visible when verifying through Pillow, which composites
    # partial frames correctly on read -- hence _assert_full_frames below, which
    # inspects the stored rects rather than the composited result.
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=False,
        disposal=2,
    )
    _assert_full_frames(path)
    size_mb = os.path.getsize(path) / 1024 / 1024
    # /api/lcd/upload caps at 5 MB; the on-disk path install.sh uses does not,
    # but staying under it keeps both routes viable.
    print(
        f"{path} written: {SIZE}x{SIZE}, {FRAMES} frames, "
        f"{FRAME_MS}ms/frame, {size_mb:.1f} MB"
    )


if __name__ == "__main__":
    main()
