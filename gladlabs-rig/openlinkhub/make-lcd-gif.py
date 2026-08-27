#!/usr/bin/env python3
"""Generate the Glad Labs coolant-loop GIF for the XC7 ELITE LCD block (480x480).

Three concentric rings sweep comet heads around a dark field, coloured on the
same thermal ramp the rest of the rig uses (mint -> amber -> orange), so the
block reads as part of the same system as the sensor strip and the LINK RGB.
Cool mint runs innermost and hot orange outermost, matching how the temperature
palette is mapped everywhere else.

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

from PIL import Image, ImageDraw

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
GLOW_R = 54  # central sphere's outer glow radius
CORE_R = 15  # its solid centre
PALETTE_TILES = 8  # frames sampled to build the shared palette


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

    # Central sphere, breathing on the same clock. Drawn as a dense radial
    # falloff (1px steps at supersample) rather than a few widely-spaced discs
    # -- coarse steps read as a flat dot wearing a haze, not as a sphere.
    pulse = 0.5 + 0.5 * math.cos(2 * math.pi * t)
    glow_r = int(GLOW_R * (0.86 + 0.14 * pulse) * SS)
    peak = 96 + 128 * pulse
    for rr in range(glow_r, 0, -1):
        a = int(peak * (1 - rr / glow_r) ** 2.2)
        if a > 0:
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=MINT + (a,))
    core_r = int(CORE_R * (0.80 + 0.20 * pulse) * SS)
    d.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=MINT + (255,))

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
