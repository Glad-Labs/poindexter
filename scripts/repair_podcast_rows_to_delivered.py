"""One-off: make podcast rows describe the episode that was delivered (Glad-Labs/poindexter#1090).

The #884 migration (``20260717_154103_dedup_podcast_media_assets_and_add_unique_index``)
kept the NEWEST podcast row per post and moved the rest to
``media_assets_dedup_backup``. For about 25 approved, delivered episodes the
newest row was a re-render nobody delivered. Those rows have no URL, so the feed
falls back to the delivery key. The key still holds the approved audio, but the
enclosure length and duration come from the undelivered file.

The bucket decides what was delivered: the authenticated size of the object at
``podcast_episode_key`` (``R2UploadService.object_size``), compared per post.

- ``stamp``: the surviving row is the delivered file (same size). Stamp its URL.
- ``restore``: a backed-up row carries the delivered URL. Swap it back into
  ``media_assets`` and move the surviving row into the backup table, so nothing
  is deleted. Size and duration the restored row lacks are measured from the
  bucket object: older reconciliation rows recorded only a URL.
- ``describe``: no row describes the delivered file. Stamp the URL and set size
  and duration from the bucket object on the surviving row. Its old values are
  kept under ``metadata.poindexter_1090``.
- ``missing``: nothing is at the key. Reported, not changed: the approved audio
  is gone from the bucket, and it needs a re-render or removal.
- ``unverified``: the object store could not answer. Reported; a re-run retries.

Candidates are posts whose podcast approval is approved and dispatched while
their ``media_assets`` podcast row has no URL. After ``--apply``, a re-run finds
only the ``missing`` and ``unverified`` ones again.

Run it inside the worker, where the object-store credentials, ``ffprobe`` and the
database all are. The default is a dry run::

    docker exec -w /app poindexter-worker python /opt/scripts/repair_podcast_rows_to_delivered.py
    docker exec -w /app poindexter-worker python /opt/scripts/repair_podcast_rows_to_delivered.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The columns media_assets and media_assets_dedup_backup share, in the order the
# #884 migration copies them. An explicit list, so schema drift on either table
# fails loudly instead of misaligning columns.
ASSET_COLUMNS = (
    "id, tenant_id, site_id, type, source, storage_provider, url, "
    "storage_path, thumbnail_url, title, description, alt_text, metadata, "
    "ai_metadata, task_id, created_at, updated_at, post_id, provider_plugin, "
    "width, height, duration_ms, file_size_bytes, mime_type, cost_usd, "
    "electricity_kwh, platform_video_ids"
)

CANDIDATES_SQL = """
    SELECT ma.post_id::text AS post_id, ma.id::text AS keeper_id,
           ma.file_size_bytes AS keeper_size
      FROM media_assets ma
      JOIN media_approvals apr
        ON apr.post_id = ma.post_id AND apr.medium = 'podcast'
     WHERE ma.type = 'podcast'
       AND apr.status = 'approved'
       AND apr.dispatched_at IS NOT NULL
       AND COALESCE(ma.url, '') = ''
     ORDER BY ma.post_id
"""

# Backed-up rows that were delivered (they carry a URL), newest first.
BACKUPS_SQL = """
    SELECT id::text AS id, url, file_size_bytes AS size, duration_ms
      FROM media_assets_dedup_backup
     WHERE post_id = $1::uuid AND type = 'podcast' AND COALESCE(url, '') <> ''
     ORDER BY created_at DESC NULLS LAST, id DESC
"""

STAMP_SQL = """
    UPDATE media_assets
       SET url = $2, storage_provider = 'cloudflare_r2', updated_at = NOW()
     WHERE id = $1::uuid AND COALESCE(url, '') = ''
"""

MOVE_LIVE_TO_BACKUP_SQL = f"""
    INSERT INTO media_assets_dedup_backup ({ASSET_COLUMNS})
    SELECT {ASSET_COLUMNS} FROM media_assets
     WHERE id = $1::uuid AND COALESCE(url, '') = ''
"""  # nosec B608 - ASSET_COLUMNS is the hardcoded column list above, never external input

DELETE_LIVE_SQL = "DELETE FROM media_assets WHERE id = $1::uuid AND COALESCE(url, '') = ''"

MOVE_BACKUP_TO_LIVE_SQL = f"""
    INSERT INTO media_assets ({ASSET_COLUMNS})
    SELECT {ASSET_COLUMNS} FROM media_assets_dedup_backup WHERE id = $1::uuid
"""  # nosec B608 - ASSET_COLUMNS is the hardcoded column list above, never external input

DELETE_BACKUP_SQL = "DELETE FROM media_assets_dedup_backup WHERE id = $1::uuid"

# Fills only what the restored row lacks: a pipeline row brings its own size
# and duration, a reconciliation row brought only a URL.
FILL_RESTORED_SQL = """
    UPDATE media_assets
       SET url = COALESCE(NULLIF(url, ''), $2),
           file_size_bytes = COALESCE(file_size_bytes, $3),
           duration_ms = COALESCE(duration_ms, $4),
           metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
               'poindexter_1090',
               jsonb_build_object('action', 'restore', 'displaced_row', $5::text)
           ),
           updated_at = NOW()
     WHERE id = $1::uuid
"""

# SET expressions read the OLD row, so the replaced values are kept in metadata.
DESCRIBE_SQL = """
    UPDATE media_assets
       SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
               'poindexter_1090',
               jsonb_build_object(
                   'action', 'describe',
                   'previous_file_size_bytes', file_size_bytes,
                   'previous_duration_ms', duration_ms
               )
           ),
           url = $2,
           storage_provider = 'cloudflare_r2',
           file_size_bytes = $3,
           duration_ms = $4,
           updated_at = NOW()
     WHERE id = $1::uuid AND COALESCE(url, '') = ''
"""


@dataclass(frozen=True)
class Plan:
    post_id: str
    action: str
    keeper_id: str
    bucket_size: int | None = None
    backup_id: str | None = None
    needs_duration: bool = False
    note: str = ""


def plan_post(
    post_id: str,
    keeper_id: str,
    keeper_size: int | None,
    backups: list[dict[str, Any]],
    bucket_size: int | None,
    *,
    unverified: bool = False,
) -> Plan:
    """Decide one post from what the bucket holds. Pure: no I/O."""
    if unverified:
        return Plan(post_id, "unverified", keeper_id, note="object store did not answer")
    if bucket_size is None:
        return Plan(post_id, "missing", keeper_id, note="no object at the delivery key")
    if keeper_size == bucket_size:
        return Plan(post_id, "stamp", keeper_id, bucket_size, note="surviving row is the delivered file")
    exact = next((b for b in backups if b.get("size") == bucket_size), None)
    if exact is not None:
        return Plan(
            post_id, "restore", keeper_id, bucket_size, exact["id"],
            needs_duration=exact.get("duration_ms") is None,
            note="backed-up row is the delivered file",
        )
    stub = next((b for b in backups if b.get("size") is None), None)
    if stub is not None:
        return Plan(
            post_id, "restore", keeper_id, bucket_size, stub["id"],
            needs_duration=True, note="backed-up URL-only row; size and duration measured",
        )
    return Plan(
        post_id, "describe", keeper_id, bucket_size, needs_duration=True,
        note="no row describes the delivered file",
    )


def probe_duration_ms(url: str) -> int | None:
    """Duration of the object at ``url`` via ffprobe, or None (reported)."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", url,
            ],
            capture_output=True, text=True, timeout=180, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  ffprobe failed for {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    raw = out.stdout.strip()
    try:
        return int(float(raw) * 1000)
    except ValueError:
        print(f"  ffprobe gave no duration for {url}: {out.stderr.strip()[:200]}", file=sys.stderr)
        return None


def _expect(status: str, want: str, what: str) -> None:
    """asyncpg returns e.g. ``UPDATE 1``; anything else aborts the transaction."""
    if status != want:
        raise RuntimeError(f"{what}: expected {want!r}, got {status!r}")


async def apply_plan(conn: Any, plan: Plan, *, url: str, duration_ms: int | None) -> None:
    """Apply one plan. Run it inside a transaction: a failed guard raises."""
    if plan.action == "stamp":
        _expect(await conn.execute(STAMP_SQL, plan.keeper_id, url), "UPDATE 1", "stamp")
    elif plan.action == "restore":
        _expect(await conn.execute(MOVE_LIVE_TO_BACKUP_SQL, plan.keeper_id), "INSERT 0 1", "back up surviving row")
        _expect(await conn.execute(DELETE_LIVE_SQL, plan.keeper_id), "DELETE 1", "remove surviving row")
        _expect(await conn.execute(MOVE_BACKUP_TO_LIVE_SQL, plan.backup_id), "INSERT 0 1", "restore delivered row")
        _expect(await conn.execute(DELETE_BACKUP_SQL, plan.backup_id), "DELETE 1", "drop restored row from backup")
        _expect(
            await conn.execute(
                FILL_RESTORED_SQL, plan.backup_id, url, plan.bucket_size, duration_ms, plan.keeper_id,
            ),
            "UPDATE 1", "fill restored row",
        )
    elif plan.action == "describe":
        _expect(
            await conn.execute(DESCRIBE_SQL, plan.keeper_id, url, plan.bucket_size, duration_ms),
            "UPDATE 1", "describe",
        )
    else:
        raise ValueError(f"nothing to apply for action {plan.action!r}")


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    from poindexter.brain.bootstrap import resolve_database_url

    resolved = resolve_database_url()
    if not resolved:
        raise SystemExit("no database URL: set DATABASE_URL or configure bootstrap.toml")
    return resolved


async def run(*, apply: bool) -> int:
    import asyncpg

    from poindexter.services.bootstrap import build_container
    from poindexter.services.r2_upload_service import (
        ObjectStoreUnavailable,
        R2UploadService,
        podcast_episode_key,
    )
    from poindexter.services.settings_read_telemetry import flush_read_telemetry

    pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=2)
    site_config = None
    try:
        site_config = (await build_container(pool)).site_config
        cdn_version = site_config.get("podcast_cdn_version", "v2")
        r2 = R2UploadService(site_config=site_config)

        plans: list[Plan] = []
        for row in await pool.fetch(CANDIDATES_SQL):
            backups = [dict(b) for b in await pool.fetch(BACKUPS_SQL, row["post_id"])]
            key = podcast_episode_key(row["post_id"], cdn_version)
            try:
                size, unverified = await r2.object_size(key), False
            except ObjectStoreUnavailable as exc:
                print(f"  {row['post_id']}: object store unavailable: {exc}", file=sys.stderr)
                size, unverified = None, True
            plans.append(plan_post(
                row["post_id"], row["keeper_id"], row["keeper_size"], backups, size,
                unverified=unverified,
            ))

        for p in plans:
            print(f"{p.post_id}  {p.action:<10} bucket={p.bucket_size}  {p.note}")
        counts: dict[str, int] = {}
        for p in plans:
            counts[p.action] = counts.get(p.action, 0) + 1
        print("totals:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to repair")

        if not apply:
            print("DRY RUN: nothing changed. Re-run with --apply.")
            return 0

        applied = 0
        for p in plans:
            if p.action in ("missing", "unverified"):
                continue
            url = r2.object_url(podcast_episode_key(p.post_id, cdn_version))
            duration = probe_duration_ms(url) if p.needs_duration else None
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await apply_plan(conn, p, url=url, duration_ms=duration)
            applied += 1
            print(f"applied {p.action} to {p.post_id} (duration_ms={duration})")
        print(f"applied {applied} of {len(plans)}")
        return 0
    finally:
        if site_config is not None:
            await flush_read_telemetry(pool, site_config)
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1] / "src" / "cofounder_agent"
    if backend.is_dir() and str(backend) not in sys.path:
        sys.path.insert(0, str(backend))  # host runs; inside the worker /app is the cwd
    return asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    sys.exit(main())
