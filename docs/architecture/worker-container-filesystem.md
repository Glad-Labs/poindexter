# Worker Container Filesystem Layout

The `poindexter-worker` container runs the FastAPI process as **`appuser`** (UID 1001, defined in `src/cofounder_agent/Dockerfile:62`). All container-side filesystem layout decisions are downstream of this UID:

- The in-container `HOME` is `/home/appuser`.
- Any Python code resolving `os.path.expanduser("~")` or `Path.home()` returns `/home/appuser`.
- A bind mount target of `/root/.poindexter` is reachable for **reads** (the kernel honors the mount regardless of process UID) but is **NOT** the path that `~/.poindexter` expands to.

## Required mounts

`docker-compose.local.yml` mounts the host `~/.poindexter` directory at two locations:

| Host path                        | Container path                               | Purpose                                                                                             |
| -------------------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `~/.poindexter/podcast`          | `/home/appuser/.poindexter/podcast`          | Generated podcast `.mp3` files (one per post).                                                      |
| `~/.poindexter/video`            | `/home/appuser/.poindexter/video`            | Generated video `.mp4` files.                                                                       |
| `~/.poindexter/generated-images` | `/home/appuser/.poindexter/generated-images` | SDXL outputs before R2 upload.                                                                      |
| `~/.poindexter/generated-videos` | `/home/appuser/.poindexter/generated-videos` | Wan2 intermediate clips.                                                                            |
| `~/.poindexter` (entire dir)     | `/root/.poindexter`                          | Legacy mount kept for code paths that still reference `/root/.poindexter` or use `HOST_HOME=/root`. |

The **appuser-home mounts are pinpoint per subdirectory** — not the whole `~/.poindexter` directory. This matters: if the host's full `~/.poindexter` were mounted at `/home/appuser/.poindexter`, then `~/.poindexter/bootstrap.toml` would land in appuser's home. `brain.bootstrap.resolve_database_url()` reads `~/.poindexter/bootstrap.toml` as **priority 1** (per `docs/architecture/bootstrap.md`) and the host's `database_url` value points at `localhost:15432` — unreachable from inside a container. The worker would crash on startup trying to connect to a host-only DSN even though `DATABASE_URL` is correctly set in the container's env.

The legacy `/root/.poindexter` mount is intentional — anything that reads bootstrap.toml directly (the migration runner, certain CLI scripts) still works through that path. The two mount targets share the same host directory, so writes are bidirectionally visible.

## Why this matters (the 2026-04-29 → 2026-05-12 silent failure)

Before 2026-05-12, the worker only mounted `/root/.poindexter`. Code that wrote to `~/.poindexter` (resolved against appuser's home) ended up in a container-local `/home/appuser/.poindexter/` directory that:

- Was invisible to the host (no bind mount).
- Disappeared on container recreate.
- Was reached by the R2 upload step (`services/r2_upload_service.upload_podcast_episode`) **only if the upload ran in the same container process before the file vanished**.

Combined with the existing fire-and-forget pattern (`_spawn_background(generate_podcast_episode(...))` in `services/publish_service.py`), this meant every publish since 2026-04-29 produced a "Queued episode generation" log line but **zero output on the host**. 13 days of silent failure caught by the 2026-05-12 audit (Matt's "podcasts and video generation working" request).

## Adding new media output types

When introducing a new media output type:

1. Decide on a host directory under `~/.poindexter/`.
2. Add a bind-mount entry in `docker-compose.local.yml` for the worker service pointing at `/home/appuser/.poindexter/<new-dir>`.
3. Confirm the directory exists on the host (the `up -d` flow won't create missing host paths — it'll bind-mount an empty directory that the container can write to, but Windows volume mounts can be finicky if the host path is missing).
4. If the legacy `/root/.poindexter` code path needs visibility, the existing `/root` mount already covers it.

## Diagnostics

To verify the bind mounts at runtime:

```bash
docker exec poindexter-worker bash -c '
  echo "uid: $(id -u)  home: $HOME"
  echo "appuser mounts:"
  mount | grep "/home/appuser/.poindexter"
  echo "podcast dir:"
  ls -la /home/appuser/.poindexter/podcast/ | head -5
'
```

A correctly mounted worker reports `uid: 1001`, lists 4 bind mounts under `/home/appuser/.poindexter`, and shows the historical host-side podcasts.
