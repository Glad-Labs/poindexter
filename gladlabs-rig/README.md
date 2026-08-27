# gladlabs-rig — operator PC sensor & lighting stack

Snapshot of the Pop!_OS rig's sensor-display and temperature-reactive lighting
config, built 2026-07-24. **Operator-private** — this directory is stripped
from the public poindexter mirror (`scripts/sync-to-github.sh` +
`_STRIP_DIR_PREFIXES` in `scripts/ci/check_public_mirror_safety.py`).

Deep lore (every gotcha found while building this) lives in Claude's memory:
`reference_sensor_strip_conky.md`. This README is just enough to restore.

## What's here

| Dir             | Contents                                                                                                                                                                                                      | Restores to                                          |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `applications/` | App-menu entry that drives `conky-strip.service` (shadows the packaged one)                                                                                                                                   | `~/.local/share/applications/`                       |
| `btop/`         | btop config + deuteranomaly-safe theme                                                                                                                                                                        | `~/.config/btop/`                                    |
| `conky/`        | 8.8" 1920x480 sensor-strip panel (renderer, launcher + its test, loop poller, bg generator)                                                                                                                   | `~/.config/conky/`                                   |
| `openrgb/`      | OpenRGB detector config (Corsair disabled) + ARGB temp-sync daemon                                                                                                                                            | `~/.config/OpenRGB/`, `~/.config/openrgb-temp-sync/` |
| `openlinkhub/`  | OLH config, gladlabs thermal RGB palette, quiet fan curves, hub/XC7 device profiles (channel assignments, adapter declaration, probe binds, LCD rotation), XC7 LCD animation generator, coolant bridge script | `/opt/OpenLinkHub/...`, `/usr/local/bin/`            |
| `systemd-user/` | conky-strip / openrgb-server / argb-temp-sync units                                                                                                                                                           | `~/.config/systemd/user/`                            |

## Prereqs (install before running install.sh)

- `apt: conky-all python3-pil i2c-tools` (btop ships with Pop)
- **OpenLinkHub 0.9.1** deb — github.com/jurkovic-nikola/OpenLinkHub releases
  (creates the `OpenLinkHub.service` system unit)
- **OpenRGB 1.0rc3** bookworm deb — codeberg.org/OpenRGB/OpenRGB releases
  (linked from openrgb.org; NOT in apt)

## Restore

```bash
./install.sh
```

Idempotent; uses sudo for the OpenLinkHub/system paths, then restarts the
services. The conky launcher re-derives display/monitor positions per boot _and
keeps watching for layout changes afterwards_, so no display config is baked.

Check the launcher's placement logic without touching the running strip:

```bash
conky/test-launch-strip.sh
```

It stubs xrandr/pgrep/conky and drives the settle + re-anchor loops at ~20x
speed — no X server, no systemd, ~9s.

## Machine-specific facts baked into these files

- LINK hub serials `6FF34787…` (hub 1: 4x QX + LINK ADAPTER) and `D88EC280…`
  (hub 2: 10 fans + XD6 pump) — in `conky/loop-poll.py`, the device profiles,
  and `openlinkhub/olh-coolant-temp`.
- The LINK ADAPTER (hub 1 ch 2) is declared as "6x QX Series Fans" (204 LEDs) —
  it actually drives the 3 case strips + 100-LED PSU-cable strip chain.
- Thermal palette (everywhere): mint `#00e5d6` → amber `(255,184,51)` → orange
  `(255,128,0)`; LINK anchors 25/30/35 °C, OpenRGB windows: coolant 28–38,
  DIMM 40–50, GPU 35–60.
- **Never launch conky from the app menu's packaged entry or bare `conky`** —
  there is no `~/.conkyrc`, so it loads stock `/etc/conky/conky.conf` anchored to
  xinerama head 0 (the primary), not the strip. `applications/conky.desktop`
  shadows it by desktop-file ID and drives the systemd unit instead.
- `launch-strip.sh` anchors the strip relative to **monitor 0**, so it waits for
  the RandR layout to stop changing (3 identical reads) before sampling. Reading
  it mid-settle picks up whichever output woke first and mispositions the panel
  for the whole session — hit 2026-07-27, head 0 sampled as HDMI-A-3 `@+5360`
  and the strip drew 3440px off-screen.
- conky resolves `-x/-y` **once at startup**, so the launcher stays resident and
  re-polls RandR every 5s, relaunching conky when the strip-vs-monitor-0 offset
  changes and holds for 3 reads. Without that, any layout change _after_ launch
  strands the panel at its old absolute position — hit 2026-08-05, a COSMIC
  rearrange left it drawn across the Acer while the strip had moved to
  `+4423+1920`, and only a manual unit restart fixed it.
- **The graph row is positioned by TEXT FLOW, so it drifts.** `${goto}` fixes X,
  but the graphs' Y is wherever `strip.sh`'s output left the cursor. The footer's
  last column renders to **x=1909 — 11px clear of the 1920 edge**, so a wider live
  value (net throughput flipping to MiB, a two-digit loadavg) wraps that row and
  shoves the graphs a line down; when it stops wrapping they jump back up. That is
  how they came adrift on 2026-08-06 with no file having changed. `voffset 75` is
  calibrated for the **unwrapped** case: 54px graphs at y402-456, inside the
  y398-460 wells `make-strip-bg.py` bakes. Verify after any layout edit by
  capturing the live window and diffing it against `strip-bg.png` rather than
  eyeballing it. A durable fix means removing the wrap risk: trim the footer's
  last column, or emit the footer as a second `execpi` **after** the graph objects
  so its wrapping cannot move them.
- **The strip's connector name is not stable — match it by mode.** It moved
  `HDMI-A-1` (AMD iGPU) → `HDMI-A-2` (RTX 5090) on 2026-08-06 when the cable was
  swapped to put every display on one GPU. A name-only match makes
  `launch-strip.sh` report "never settled" forever on a panel that is plugged in
  and working, so it now falls back to the panel's unique `1920x480` mode
  (`STRIP_MODE`), and only when exactly one output has it.
- Editing OLH RGB colors later: change `openlinkhub/rgb.json` **and bump the
  profile's `version`** before copying — devices render from per-serial copies
  in `database/rgb/` that only refresh on a version mismatch (upstream #487).
- **The HX1500i is excluded from OLH (`"exclude": [7199]`, = 0x1C1F) and must
  stay that way.** The kernel `corsair_psu` hwmon driver owns that PSU — it is
  what feeds every `psu_*` / `node_hwmon_*` series on the Hardware & Power
  board — so OLH's own PSU support can never win the device. It doesn't fail
  quietly either: it retried forever, and the 0.8.9 run logged **15.4 million**
  lines in 3 days ("Invalid response. Probably another software is monitoring
  this device" / "Unable to init to PSU device" / "Unable to write data to a
  device") — 3,417 lines/min, ~1 GB/day of pure disk churn, measured to **zero**
  after the exclude. Excluding it costs nothing, because OLH never successfully
  read the PSU in the first place.
  **The cost was disk, not CPU** — worth stating because the obvious guess is
  wrong. A tight error loop looks like it must burn CPU, and `systemctl status`
  right after a start seems to confirm it (~18% of a core), but that figure is
  startup burst. Steady state is ~13% either way: the 0.8.9 run averaged 13.1%
  across 3 days _with_ the loop, and it measures 13.2% now without it. Rendering
  the LCD GIF at 25 fps is likewise ~free (13.4% vs 13.3% against the text
  mode). Measure over a window, never off cumulative CPU since start.
- **XC7 block LCD** runs mode 102 (`Animation`): the `gladlabsloop` GIF as
  background with three live readouts over it — Liquid / CPU / GPU temp, keyed
  mint / amber / orange. Config is `openlinkhub/lcd/animation.json` (sensor IDs
  are OLH's own: 0 CPU temp, 1 GPU temp, 2 liquid, 3 CPU load, 4 GPU load,
  5 pump RPM). **This is the one thing on this box that actually costs CPU**:
  compositing text onto all 60 frames and JPEG-encoding them runs 21.3% of a
  core vs 13.4% for the plain GIF (mode 10) and 13.3% for static text (mode 0).
  Halve `FRAMES` in `make-lcd-gif.py`, or raise `frameDelay` in animation.json,
  if that ever needs to come down.
- **The GIF carries the Glad Labs wordmark at 12 o'clock, not at centre.** The
  brand mark is typographic — "GL", uppercase, tracked (`packages/brand`) — and
  since Space Grotesk isn't vendored as a TTF this renders **Sora 600 in brand
  cyan `#22D3EE`**, the same substitution the video CTA end-card makes for
  raster output, so both branded motion surfaces agree. Position is forced by
  mode 102's layout, not taste: three sensors put readouts across y≈74–430, so
  a centred mark is either buried or drowns the numbers — 188px centred made
  "LIQUID TEMP" unreadable. The mark's ink sits at y 26–64, clearing the first
  readout by 10px. **Two numbers to respect when editing:** keep the halo radius
  under `LOGO_Y` (a 58px halo at y=44 ran off the canvas and read as a clipped
  glyph, not a clipped glow), and remember the centre bloom's ~180 stacked 1px
  ellipses **composite additively** — a nominal peak alpha of 48 measured as a
  field of 148 and swallowed the amber CPU row. Tune it by measuring the field,
  not by reading the constant.
- **XC7 block LCD, mode 10 fallback** (`Image / GIF`) shows `gladlabsloop`, a
  480x480 coolant-loop animation built by `openlinkhub/make-lcd-gif.py` on the
  same mint/amber/orange ramp as everything else. The GIF is generated by
  `install.sh`, not committed (~1.5 MB). Mode + image persist in
  `profiles/A62XT4520039MM.json` as `LCDMode`/`LCDImage`. Four constraints:
  **every frame must be a full 480x480 rect**, or the block strobes. OLH's
  `animation.go` does `canvas := image.NewRGBA(pf.Bounds())` per frame — it
  sizes the canvas to each frame's own bounds and never composites against the
  previous frame or honours GIF disposal, so a partial frame renders as garbage
  that churns every frame. Pillow crops frames to the changed-delta bbox by
  default, and **`optimize=False` does not stop it** — that flag governs palette
  optimisation, while the differencing is only suppressed when the previous
  frame's disposal is `2`. Hence `disposal=2` plus `_assert_full_frames()` in
  the generator, which inspects the stored rects. Verifying through Pillow will
  never catch this: Pillow composites partial frames correctly on read, so the
  GIF looks perfect everywhere except on the hardware. Corsair's own
  `concentric.gif` is all full-frame; `openlinkhub.gif` is not, and misrenders
  the same way. The other three:
  OLH **caches LCD images at startup**, so a file dropped in while the service
  runs is invisible until a restart — `POST /api/lcd/upload` (multipart field
  `animationFile`, 5 MB cap) registers one live instead, though re-uploading a
  name that already exists 409s (`Animation is already loaded`) _after_ writing
  the file, so a restart is the reliable way to replace one; image **filenames
  must be alphanumeric**, so `gladlabs-loop.gif` is rejected outright; and the
  GIF needs **one shared palette built from a montage spanning the whole loop**.
  Deriving it from a single frame leaves every brightness that frame lacks with
  no entry to land on, and dithering scatters those into speckle that reshuffles
  each frame — on-screen that reads as a strobe, with only the frames nearest
  the sampled one looking solid. `dither=NONE` on a good palette is both cleaner
  and ~3x smaller here.
- **The `temperature` anchors on our palette colors are permanent config, not a
  temporary workaround** — do not "clean them up" back to an upstream palette.
  Upstream closed #487 as by-design, and `interpolateTemperatureColor()` still
  maps purely on `Color.Temperature`, so anchor-less colors render one constant
  50%-blend color forever with no error. `database/rgb/cluster.json` still
  carries the unanchored upstream palette (our version bump never reached it),
  so anything driven off the cluster profile would hit exactly that.

## Upstream issues filed from this build

- jurkovic-nikola/OpenLinkHub#487 — temperature effect ignores MinTemp/MaxTemp
  (needs `temperature` anchors on the palette colors; includes workaround).
  **CLOSED 2026-08-05 as by-design** — "You're supposed to setup temps and
  colors on initial config, hence they are not contained in original rgb.json
  file". Re-verified unfixed on `main` at 0.9.1: `src/rgb/temperature.go` still
  maps on `Color.Temperature` and never reads `MinTemp`/`MaxTemp`, so the
  `probe-temperature` profile's own `minTemp`/`maxTemp` (0/60) and the
  per-channel `rgbMinTemp`/`rgbMaxTemp` overrides `lsh.go` computes are still
  dead config. The stated rationale only holds for `probe-temperature` —
  `cpu-`/`gpu-`/`liquid-temperature` all ship anchors upstream (added in
  fd455243 for #376, the same bug class). Assume no upstream fix; keep our
  anchors.
- jurkovic-nikola/OpenLinkHub#488 — `/api/hub/linkAdapter` success response
  says "Non-existing device". **FIXED in 0.9.0** (closed without comment) —
  the success path now returns `txtLinkAdapterUpdated` with status 1.
- pop-os/cosmic-comp#701 — Xwayland dies repeatedly and is never restarted, so
  every X11 app stays dead until the next login. Commented rather than filed
  fresh: #701 is the same bug (hybrid AMD-iGPU + nvidia), open since 2024-08-08.
  Our contribution is the quantified GPU correlation (5 deaths in ~10h with the
  strip on the iGPU, 0 in 14.5h across 3 boots after moving it to the 5090), the
  all-displays-on-one-GPU workaround, and two asks: re-arm Xwayland after an
  unexpected exit, and forward its stderr to the journal — it is captured
  nowhere today, which is why nobody in that thread has the fatal message.
