# gladlabs-rig — operator PC sensor & lighting stack

Snapshot of the Pop!_OS rig's sensor-display and temperature-reactive lighting
config, built 2026-07-24. **Operator-private** — this directory is stripped
from the public poindexter mirror (`scripts/sync-to-github.sh` +
`_STRIP_DIR_PREFIXES` in `scripts/ci/check_public_mirror_safety.py`).

Deep lore (every gotcha found while building this) lives in Claude's memory:
`reference_sensor_strip_conky.md`. This README is just enough to restore.

## What's here

| Dir             | Contents                                                                                                                                                                         | Restores to                                          |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `applications/` | App-menu entry that drives `conky-strip.service` (shadows the packaged one)                                                                                                      | `~/.local/share/applications/`                       |
| `btop/`         | btop config + deuteranomaly-safe theme                                                                                                                                           | `~/.config/btop/`                                    |
| `conky/`        | 8.8" 1920x480 sensor-strip panel (renderer, launcher + its test, loop poller, bg generator)                                                                                      | `~/.config/conky/`                                   |
| `openrgb/`      | OpenRGB detector config (Corsair disabled) + ARGB temp-sync daemon                                                                                                               | `~/.config/OpenRGB/`, `~/.config/openrgb-temp-sync/` |
| `openlinkhub/`  | OLH config, gladlabs thermal RGB palette, quiet fan curves, hub/XC7 device profiles (channel assignments, adapter declaration, probe binds, LCD rotation), coolant bridge script | `/opt/OpenLinkHub/...`, `/usr/local/bin/`            |
| `systemd-user/` | conky-strip / openrgb-server / argb-temp-sync units                                                                                                                              | `~/.config/systemd/user/`                            |

## Prereqs (install before running install.sh)

- `apt: conky-all python3-pil i2c-tools` (btop ships with Pop)
- **OpenLinkHub 0.8.9** deb — github.com/jurkovic-nikola/OpenLinkHub releases
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
- Editing OLH RGB colors later: change `openlinkhub/rgb.json` **and bump the
  profile's `version`** before copying — devices render from per-serial copies
  in `database/rgb/` that only refresh on a version mismatch (upstream #487).

## Upstream issues filed from this build

- jurkovic-nikola/OpenLinkHub#487 — temperature effect ignores MinTemp/MaxTemp
  (needs `temperature` anchors on the palette colors; includes workaround)
- jurkovic-nikola/OpenLinkHub#488 — `/api/hub/linkAdapter` success response
  says "Non-existing device"
