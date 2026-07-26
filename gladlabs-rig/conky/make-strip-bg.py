#!/usr/bin/env python3
"""Generate the Glad Labs background for the 1920x480 sensor strip (v2 — punchier).

Tuned to read from ~2 feet with case lighting on: stronger grid/dividers/glows,
full-width mint accent under the header, and framed "wells" behind the graph row
so the plots read as panels even when traces are near-flat at idle.
Regenerate: python3 make-strip-bg.py  (conky picks it up on next tick).
"""
from PIL import Image, ImageDraw

W, H = 1920, 480
BASE = (10, 15, 20)
GRID = (26, 40, 50)
LINE = (42, 62, 74)
MINT = (0, 229, 214)
WELL_FILL = (4, 8, 11)
WELL_EDGE = (40, 62, 74)

img = Image.new("RGB", (W, H), BASE)
d = ImageDraw.Draw(img, "RGBA")

# vertical gradient: lighter at the top
for y in range(H):
    t = y / H
    shade = int(12 * (1 - t))
    d.line([(0, y), (W, y)], fill=(BASE[0] + shade, BASE[1] + shade, BASE[2] + shade + 3))

# dot grid
for gy in range(16, H, 24):
    for gx in range(16, W, 24):
        d.point((gx, gy), fill=GRID + (255,))

# mint corner glows
def glow(cx, cy, radius, color, peak):
    for r in range(radius, 0, -6):
        a = int(peak * (1 - r / radius) ** 2)
        if a <= 0:
            continue
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (a,))

glow(0, 0, 460, MINT, 12)
glow(W, H, 420, (51, 187, 238), 10)

# column dividers
for x in (498, 978, 1450):
    d.line([(x, 14), (x, H - 14)], fill=LINE + (255,))

# header underline: hairline + full-width mint accent
d.line([(16, 78), (W - 16, 78)], fill=LINE + (255,))
d.line([(16, 80), (W - 16, 80)], fill=MINT + (48,))

# graph wells at the bottom band (static graphs in conky.text land here)
for x0, x1 in ((24, 472), (508, 956), (988, 1436), (1460, 1908)):
    d.rounded_rectangle([x0, 392, x1, 462], radius=4, fill=WELL_FILL + (255,), outline=WELL_EDGE + (255,), width=1)

# corner ticks (top-left of each column label block)
for x in (20, 504, 984, 1456):
    d.line([(x, 14), (x, 40)], fill=MINT + (140,))

img.save("/home/mattm/.config/conky/strip-bg.png")
print("strip-bg.png v2 written")
