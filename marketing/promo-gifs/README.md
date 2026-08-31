# Promotional product GIFs

Real product footage — live data, no mocks (per `feedback_no_dummy_data`).
Recorded 2026-08-31 against the running operator stack + production site.
All files ≤10 MB so they render inline on GitHub; all fit X/LinkedIn limits.

| File          | Shows                                                                                                         | Spec                 |
| ------------- | ------------------------------------------------------------------------------------------------------------- | -------------------- |
| `site.gif`    | gladlabs.io: hero → article grid → into a post                                                                | 14s · 800px · 7.1 MB |
| `console.gif` | Operator Console live: system pulse (task mid-pipeline), KPIs, approval inbox → TRACE → animated system MAP   | 13s · 840px · 5.3 MB |
| `grafana.gif` | Cost & Analytics @ 30d (per-model spend) → Pipeline @ 30d (quality, approvals) → Hardware & Power @ 7d        | 24s · 820px · 7.1 MB |
| `cli.gif`     | `poindexter costs budget` → `posts list` → `doctor`, replayed in a branded terminal from captured real output | 17s · 900px · 2.4 MB |

This directory is stripped from the public poindexter mirror (whole `marketing/`
tree), so the GIFs and harness never land in the OSS repo — copy a GIF out
deliberately when one is wanted there (e.g. embedded in the poindexter README).

## Re-shooting (harness/)

Prereqs: operator stack up; Playwright browsers installed (`~/.cache/ms-playwright`);
run node from inside the repo so `@playwright/test` resolves from root
`node_modules`. Host has no ffmpeg — `convert.sh` uses the one baked into the
`poindexter-prefect-worker` container and drops the finished GIF beside this
README.

```bash
cd marketing/promo-gifs/harness

# public site + Grafana (Grafana allows anonymous viewing — no creds needed)
node record.cjs site    && bash convert.sh site    800 10 "bayer:bayer_scale=5"
node record.cjs grafana && bash convert.sh grafana 820 7  "bayer:bayer_scale=5" 0.5 104
```

**Console scene** needs a throwaway read-only OAuth client (console mints its own
JWTs from `px_client_id`/`px_client_secret` in localStorage):

```bash
poindexter auth register-client --name promo-gif-recorder-temp \
  --scopes "api:read" --grant-type client_credentials
export PX_CLIENT_ID=pdx_...  PX_CLIENT_SECRET=...     # from the output; never commit
node record.cjs console && bash convert.sh console 840 8 "bayer:bayer_scale=5" 2.5 112
poindexter auth revoke-client --client-id "$PX_CLIENT_ID"   # always revoke after
```

**CLI scene** replays real captured output (regenerate before each shoot so the
numbers are current):

```bash
COLUMNS=120 poindexter costs budget         > costs.out
COLUMNS=120 poindexter posts list --limit 5 > posts.out
COLUMNS=120 poindexter doctor               > doctor.out   # exit 1 on FAILs is fine
python3 build_terminal.py                                   # writes terminal.html
node record.cjs cli && bash convert.sh cli 900 10 "bayer:bayer_scale=3" 0.3 128
```

`terminal.html` is a generated artifact (the root `.gitignore`'s `*.html` rule
keeps it out of git) — `build_terminal.py` holds the page template and bakes the
captured outputs in; the cli scene refuses to run without it.

`convert.sh <scene> [width] [fps] [dither] [start-trim-s] [max-colors]`.
`scout.cjs` snapshots the console's nav views + finds its scroll containers when
the UI has changed; checkpoint PNGs from every recording land in `shots/` for
review without watching the video. Scenes live in `record.cjs` — tour steps are
plain Playwright.

## Keeping GIFs small (learned the hard way)

- Time ranges are per board in `record.cjs`: Postgres-backed boards (cost,
  pipeline) show 30d; Prometheus-backed boards are capped by its ~15d
  retention, so hardware runs at 7d to keep the window full. Longer ranges =
  denser charts = bigger GIFs; budget fps/colors accordingly.
- Holds are nearly free; **continuous scrolling and full-frame animation are the
  expensive part** — scroll in short bursts with pauses, don't glide for 5s.
- Terminal scenes: **clear the screen between commands** — scrolled text redraws
  every pixel every frame (6.9 MB → 2.4 MB for the same content).
- Flat backgrounds only; gradients re-dither every frame.
- Photographic content wants `bayer` dither (error-diffusion noise kills GIF
  compression); flat UI can afford `sierra2_4a` at low cost.
- Use the start-trim to cut boot transients (console KPIs show `—` placeholders
  and a spurious "1 SERVICE DOWN" chip for the first ~2s while probes load).

## Editorial notes (2026-08-31)

- Console WALL view renders the wrong calendar date (task chip filed) — left out.
- QA Rails board opened on a red 25% approval gauge + a "No data" panel
  (judge-rail incident aftermath) — check it looks healthy before featuring.
- Mission Control's most prominent tile was "Firing 37" alerts — swapped for
  Cost & Analytics. Re-evaluate per shoot; the boards move with the system.
