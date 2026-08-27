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

    # central core, pulsing on the same clock
    pulse = 0.5 + 0.5 * math.cos(2 * math.pi * t)
    for rr in range(int(46 * SS), 0, -2 * SS):
        a = int(52 * pulse * (1 - rr / (46 * SS)) ** 2)
        if a > 0:
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=MINT + (a,))
    core = int((9 + 3 * pulse) * SS)
    d.ellipse([cx - core, cy - core, cx + core, cy + core], fill=MINT + (255,))

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    frames = [draw_frame(i / FRAMES) for i in range(FRAMES)]

    # One shared palette for every frame, or the colours crawl between frames.
    master = frames[0].copy()
    master.paste(frames[FRAMES // 3], (0, 0), None)
    palette = master.quantize(colors=256, method=Image.MEDIANCUT)
    frames = [f.quantize(palette=palette, dither=Image.FLOYDSTEINBERG) for f in frames]

    path = os.path.join(sys.argv[1] if len(sys.argv) > 1 else ".", OUT)
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
    )
    size_mb = os.path.getsize(path) / 1024 / 1024
    # /api/lcd/upload caps at 5 MB; the on-disk path install.sh uses does not,
    # but staying under it keeps both routes viable.
    print(
        f"{path} written: {SIZE}x{SIZE}, {FRAMES} frames, "
        f"{FRAME_MS}ms/frame, {size_mb:.1f} MB"
    )


if __name__ == "__main__":
    main()
