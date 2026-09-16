# Demo GIF toolkit — `scripts/demo/`

Produces the README demo: **queue a topic → pipeline runs (timelapse) → approval queue → approve → live on gladlabs.io**, as `demo.gif` (<10 MB, README-embeddable) plus `demo.mp4` (higher quality, for the story post / social).

Everything runs against your live local stack, same as `capture-readme-screenshots.mjs`.

## Prerequisites (one-time, ~5 min)

- Stack up and healthy (`poindexter doctor`), Grafana on :3000 with anonymous viewer, `poindexter` CLI on PATH.
- `ffmpeg` — `sudo apt install ffmpeg`
- `vhs` (Charmbracelet's terminal recorder — renders scripted terminal sessions as clean video; it pulls in `ttyd` itself via the .deb):

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://repo.charm.sh/apt/gpg.key | sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg
echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" | sudo tee /etc/apt/sources.list.d/charm.list
sudo apt update && sudo apt install vhs
```

- Playwright + system Chrome — already satisfied if `capture-readme-screenshots.mjs` works for you.

## Run it

```bash
cd scripts/demo
./build-demo.sh                 # runs all phases: create → timelapse → approve → site → stitch
```

One full run takes roughly (pipeline duration + 10 minutes). Since a real pipeline run is minutes long, the timelapse phase just waits alongside it, screenshotting Grafana every 5 s.

Phases are resumable — an interrupted evening doesn't start over:

```bash
./build-demo.sh --from timelapse    # skip create, reuse work/ state (needs TASK_ID in work/task_id)
./build-demo.sh --from approve
./build-demo.sh --from site
./build-demo.sh --from stitch       # re-stitch only (tweak captions/fps without recapturing)
```

Intermediate artifacts live in `scripts/demo/work/`, already gitignored by `scripts/demo/.gitignore`. Final outputs land in `docs/assets/readme/demo.gif` and `demo.mp4`.

## Knobs (env vars)

| Var                     | Default                           | What                                               |
| ----------------------- | --------------------------------- | -------------------------------------------------- |
| `DEMO_TOPIC`            | `"Self-hosting Qwen 3 on a 5090"` | Topic queued in the opening shot                   |
| `GRAFANA_URL`           | `http://localhost:3000`           | Same as the screenshot script                      |
| `FRAME_INTERVAL`        | `5`                               | Seconds between Grafana timelapse frames           |
| `TIMELAPSE_FPS`         | `6`                               | Playback speed of the sped-up section              |
| `MAX_WAIT_MIN`          | `30`                              | Give up waiting for `awaiting_approval` after this |
| `GIF_WIDTH` / `GIF_FPS` | `960` / `10`                      | Auto-degraded to 840/8 if the GIF lands >9.5 MB    |

## Before you commit

Same warning as the screenshot script, doubled because this is ~400 frames: **watch the whole GIF once, frame-scrub the Grafana section**, and check for hostnames, tailnet IPs, or panel strings the leak guard can't see inside pixels. Then embed in the README directly under the banner:

```markdown
<img src="docs/assets/readme/demo.gif" alt="Poindexter demo — queue a topic, watch the pipeline run, approve, published" width="100%">
```

Link `demo.mp4` under it for people who want the crisp version.
