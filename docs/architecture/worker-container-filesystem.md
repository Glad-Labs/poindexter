# Worker Container Filesystem Layout

The `poindexter-worker`, `poindexter-prefect-worker` and `poindexter-pipeline-bot` containers are built from `src/cofounder_agent/Dockerfile.worker` and run their processes as **`appuser`** (UID 1001). All container-side filesystem layout decisions are downstream of this UID:

- The in-container `HOME` is `/home/appuser`.
- Any Python code resolving `os.path.expanduser("~")` or `Path.home()` returns `/home/appuser`.
- `/root` keeps the base image's `0700`. appuser cannot traverse it, so nothing appuser uses is mounted under it.

## Required mounts

`docker-compose.local.yml` mounts **individual subdirectories** of the host's `~/.poindexter` at appuser's home:

| Host path                        | Container path                               | Services               | Purpose                                                      |
| -------------------------------- | -------------------------------------------- | ---------------------- | ------------------------------------------------------------ |
| `~/.poindexter/podcast`          | `/home/appuser/.poindexter/podcast`          | worker, prefect-worker | Generated podcast `.mp3` files (one per post).               |
| `~/.poindexter/video`            | `/home/appuser/.poindexter/video`            | worker, prefect-worker | Generated video `.mp4` files.                                |
| `~/.poindexter/generated-images` | `/home/appuser/.poindexter/generated-images` | worker, prefect-worker | image-gen outputs before R2 upload.                          |
| `~/.poindexter/generated-videos` | `/home/appuser/.poindexter/generated-videos` | worker, prefect-worker | Wan2 intermediate clips.                                     |
| `~/.poindexter/demo-clips`       | `/home/appuser/.poindexter/demo-clips`       | worker, prefect-worker | VHS CLI footage for the `cli_demo` shot source.              |
| `~/.poindexter/backups`          | `/home/appuser/.poindexter/backups`          | worker                 | `DbBackupJob` pg_dumps (`POINDEXTER_BACKUP_ROOT` overrides). |
| `~/.poindexter/singer-venv`      | `/home/appuser/.poindexter/singer-venv`      | worker                 | GA4/GSC singer tap venvs the tap runner executes.            |

pipeline-bot mounts nothing from `~/.poindexter`: it and the `/cli` subprocesses it spawns take the DSN and secret key from the container env.

### Never the whole directory

**No container mounts the whole `~/.poindexter`, at any path.** It holds `bootstrap.toml` (the master key: `poindexter_secret_key`, the database passwords, the OAuth signing key), and on an operator host it also holds code the host executes as the operator user: the deploy clone (`deploy/`), the deploy-sync launcher and its last-known-good driver (`deploy-sync/`), the host CLI's venv (`cli-venv/`) and the dr-backup and recovery scripts (`scripts/`).

Two reasons, either sufficient:

- **The master key.** `brain.bootstrap.resolve_database_url()` reads `~/.poindexter/bootstrap.toml` as **priority 1** (per `docs/architecture/bootstrap.md`). Mounted at `/home/appuser/.poindexter`, it would also hand the container the host's `database_url` (`localhost:5433`), which is unreachable from inside a container, so the worker would fail to connect even with `DATABASE_URL` set correctly.
- **Host code.** These containers run LLM-driven pipeline code. A container that can write the deploy clone or the deploy-sync launcher can run code on the host.

Until glad-labs-stack#4186 (2026-09-28), worker, pipeline-bot and prefect-worker did mount the whole directory, read-write at `/root/.poindexter`, commented as kept "for code paths that still reference `/root/.poindexter` or `HOST_HOME=/root`". There were none. `HOST_HOME` had no reader after #2254 (2026-07-10), and no code or settings row pointed under `/root`. On Linux the host dir's `0700` kept uid 1001 out, so the processes got `Permission denied`. Container root (`docker exec -u root`), a `chmod` or ACL on the host dir, or a Docker Desktop host (which never enforced that mode) would have exposed all of it. The mount, `HOST_HOME`, the duplicate `/root/Downloads` mount and the `chmod 0711 /root` in `Dockerfile.worker` that made `/root` traversable were removed together.

`scripts/ci/compose_poindexter_home_mount_lint.py` (in the `migrations-smoke` job) fails CI when any compose service mounts `~/.poindexter` or an ancestor of it (`~`, `/`), mounts a protected entry (`bootstrap.toml*`, `deploy`, `deploy-sync`, `cli-venv`, `scripts`, `worktrees`), or mounts a subdirectory its `ALLOWED_ENTRIES` doesn't declare. Its `EXEMPT` table lists the three deliberate exceptions, each with its reason:

- `backup-offsite` mounts the whole dir **read-only** for the offsite config snapshot. `bootstrap.toml` is what it backs up.
- `cadvisor` mounts `/` **read-only**, the standard cAdvisor host-filesystem mount.
- `brain-daemon` mounts the deploy clone read-write so the migration-drift self-heal can reset it.

## Why this matters (the 2026-04-29 → 2026-05-12 silent failure)

Before 2026-05-12, the worker only mounted `/root/.poindexter`. Code that wrote to `~/.poindexter` (resolved against appuser's home) ended up in a container-local `/home/appuser/.poindexter/` directory that:

- Was invisible to the host (no bind mount).
- Disappeared on container recreate.
- Was reached by the R2 upload step (`services/r2_upload_service.upload_podcast_episode`) **only if the upload ran in the same container process before the file vanished**.

Combined with the existing fire-and-forget pattern (`_spawn_background(generate_podcast_episode(...))` in `services/publish_service.py`), this meant every publish since 2026-04-29 produced a "Queued episode generation" log line but **zero output on the host**. 13 days of silent failure caught by the 2026-05-12 audit (Matt's "podcasts and video generation working" request).

## Adding new media output types

When introducing a new media output type:

1. Decide on a host directory under `~/.poindexter/`.
2. Add a bind-mount entry in `docker-compose.local.yml` (and `docker-compose.consumer.yml`) pointing at `/home/appuser/.poindexter/<new-dir>`, on **every** service that reads or writes it. Stage-2 media runs in prefect-worker, the uploaders in worker (the #906 shape: a mount on one and not the other strands every render). Make it `:ro` on a service that only reads.
3. Add `<new-dir>` to `ALLOWED_ENTRIES` in `scripts/ci/compose_poindexter_home_mount_lint.py`, with a one-line reason. The lint is default-deny, so CI fails until you do.
4. Confirm the directory exists on the host before `up -d`. Docker creates a missing bind source as a root-owned directory, which appuser cannot write.

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
docker inspect poindexter-worker --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

A correctly mounted worker reports `uid: 1001`, lists 7 bind mounts under `/home/appuser/.poindexter`, shows the historical host-side podcasts, and `docker inspect` shows no mount under `/root`.
