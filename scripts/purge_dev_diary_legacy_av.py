"""One-off: purge legacy dev_diary audio/video media (incident #1596 cleanup).

dev_diary was switched to generate NO audio/video — its niche policy
``default_media_to_generate`` is ``[]`` (verified in prod + the baseline
seed). But 18 legacy AV assets generated *before* that policy took hold
(2026-05-05 .. 2026-05-18) lingered across three surfaces:

* **DB** — 18 ``media_assets`` rows (10 podcast / 7 video / 1 video_short)
  + 7 ``media_approvals`` (medium='video', ``auto:grandfather``).
* **R2** — the public-CDN copies (``podcast/v2/<id>.mp3`` / ``video/<id>.mp4``),
  reachable by direct URL even though dev_diary is excluded from every RSS
  feed by the ``media_to_generate`` gate (``feedback_filter_on_seams_not_slugs``).
* **host disk** — the bind-mounted ``~/.poindexter/{podcast,video}/<id>.<ext>``.

The 7 videos were additionally mass-re-uploaded to YouTube on 2026-06-15 by a
grandfather migration that left ``dispatched_at`` NULL — see
Glad-Labs/poindexter#1596. Those YouTube uploads have no programmatic
handle and must be deleted by hand in YouTube Studio.

Scoped to dev_diary ONLY (``pipeline_tasks.template_slug='dev_diary'`` via
either the post-metadata or asset task seam) and to AV types only
(``podcast`` / ``video`` / ``video_short``) — it never touches canonical_blog
(glad-labs) media or dev_diary featured_images.

Safe by default: prints the plan and exits. Pass ``--execute`` to apply.

Usage (from repo root, with the backend on PYTHONPATH)::

    python scripts/purge_dev_diary_legacy_av.py             # DRY RUN (default)
    python scripts/purge_dev_diary_legacy_av.py --execute   # apply to prod

DB connection resolves like the other backfill scripts: ``--database-url`` →
``POINDEXTER_BRAIN_URL`` / ``GLADLABS_BRAIN_URL`` / ``DATABASE_URL`` → local
default (the prod brain DB on :5433).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

DEFAULT_DB_URL = (
    "postgresql://poindexter:poindexter-brain-local@localhost:5433/poindexter_brain"
)


# ---------------------------------------------------------------------------
# Deletion-targeting SQL (contract-tested for shape in the sibling test).
# ---------------------------------------------------------------------------

# Two LEFT-JOIN seams to dev_diary: the post's metadata pipeline_task_id, AND
# the asset's own task_id — so an AV row is caught however it was linked. The
# type filter is what keeps featured_images (legitimate for dev_diary) safe.
_SELECT_DEV_DIARY_AV_SQL = """
SELECT ma.id AS asset_id,
       ma.post_id::text AS post_id,
       ma.type,
       ma.url,
       ma.storage_path
FROM media_assets ma
LEFT JOIN posts p ON p.id = ma.post_id
LEFT JOIN pipeline_tasks pt1 ON pt1.task_id = (p.metadata->>'pipeline_task_id')
LEFT JOIN pipeline_tasks pt2 ON pt2.task_id = ma.task_id
WHERE (pt1.template_slug = 'dev_diary' OR pt2.template_slug = 'dev_diary')
  AND ma.type IN ('podcast', 'video', 'video_short')
ORDER BY ma.created_at
"""

# Delete the exact rows the SELECT surfaced, by primary key — no room to drift.
_DELETE_ASSETS_BY_ID_SQL = "DELETE FROM media_assets WHERE id = ANY($1::uuid[])"

# Approvals are post-keyed (no task_id column), so scope via the post→task
# seam. AV mediums only; featured_image is not a media_approvals medium.
_DELETE_DEV_DIARY_AV_APPROVALS_SQL = """
DELETE FROM media_approvals
WHERE medium IN ('video', 'podcast', 'video_short')
  AND post_id IN (
      SELECT p.id
      FROM posts p
      JOIN pipeline_tasks pt ON pt.task_id = (p.metadata->>'pipeline_task_id')
      WHERE pt.template_slug = 'dev_diary'
  )
"""


# ---------------------------------------------------------------------------
# Pure targeting helpers — the safety-critical core (unit-tested).
# ---------------------------------------------------------------------------


def r2_key_from_url(url: str, public_base: str) -> str | None:
    """Return the bucket key for an object under ``public_base``, else None.

    ``media_assets.url`` is ``f"{storage_public_url}/{key}"`` — strip the base
    to recover the key for ``R2UploadService.delete_object``. Returns None for
    empty URLs and for any URL *not* under our bucket (e.g. Pexels-hosted
    featured images), so the purge can never issue a delete against a host we
    don't own.
    """
    if not url or not public_base:
        return None
    base = public_base.rstrip("/") + "/"
    if not url.startswith(base):
        return None
    key = url[len(base):]
    return key or None


def host_path_from_storage_path(
    storage_path: str | None, host_root: Path,
) -> Path | None:
    """Translate a container ``storage_path`` to its host bind-mount path.

    Container paths look like ``/home/appuser/.poindexter/podcast/<f>.mp3`` or
    ``/root/.poindexter/video/<f>.mp4``; the host root is ``~/.poindexter``.
    Splits on the ``.poindexter/`` segment and rejoins under ``host_root``.
    Returns None for empty paths or paths with no ``.poindexter/`` segment —
    nothing safe to map, so nothing to delete.
    """
    if not storage_path:
        return None
    marker = ".poindexter/"
    idx = storage_path.find(marker)
    if idx == -1:
        return None
    rel = storage_path[idx + len(marker):]
    if not rel:
        return None
    return host_root / rel


# ---------------------------------------------------------------------------
# Environment resolution (mirrors scripts/backfill-media-assets.py)
# ---------------------------------------------------------------------------


def _resolve_db_url(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    for env_key in ("POINDEXTER_BRAIN_URL", "GLADLABS_BRAIN_URL", "DATABASE_URL"):
        val = os.getenv(env_key)
        if val:
            return val
    return DEFAULT_DB_URL


def _data_root() -> Path:
    """Host media root — ``~/.poindexter`` on Matt's PC (the bind-mount source)."""
    override = os.environ.get("POINDEXTER_DATA_ROOT")
    if override:
        return Path(override)
    root_mount = Path("/root/.poindexter")
    if root_mount.is_dir():
        return root_mount
    return Path(os.path.expanduser("~")) / ".poindexter"


def _ensure_backend_on_path() -> None:
    """Put ``src/cofounder_agent`` (for ``services.*``) and the repo root (for
    ``brain.*``) on sys.path so the script runs from the repo root without a
    manual PYTHONPATH."""
    root = Path(__file__).resolve().parent.parent
    for p in (root / "src" / "cofounder_agent", root):
        if p.is_dir() and str(p) not in sys.path:
            sys.path.insert(0, str(p))


def _ensure_secret_key_env() -> bool:
    """Populate ``POINDEXTER_SECRET_KEY`` from bootstrap.toml when unset.

    Encrypted ``app_settings`` secrets (``storage_secret_key``) need this key
    to decrypt. The container env normally carries it; a bare host run does
    not — so mirror the ``resolve_database_url`` pattern and read it from
    ``~/.poindexter/bootstrap.toml``. Never prints the value. Returns True when
    a key is available (in env or resolved from bootstrap)."""
    if os.environ.get("POINDEXTER_SECRET_KEY"):
        return True
    try:
        from brain.bootstrap import get_bootstrap_value
    except Exception:
        return False
    secret_key = get_bootstrap_value("poindexter_secret_key")
    if secret_key:
        os.environ["POINDEXTER_SECRET_KEY"] = secret_key
        return True
    return False


# ---------------------------------------------------------------------------
# Plan reporting
# ---------------------------------------------------------------------------


def _print_plan(
    plan: list[dict], public_base: str, host_root: Path, *, execute: bool,
) -> None:
    print("=" * 64)
    print(f"dev_diary legacy-AV purge — {'EXECUTE' if execute else 'DRY RUN'}")
    print("=" * 64)
    print(f"storage_public_url base : {public_base or '(unset)'}")
    print(f"host media root         : {host_root}")
    print(f"AV assets matched       : {len(plan)}")
    by_type: dict[str, int] = {}
    for item in plan:
        by_type[item["type"]] = by_type.get(item["type"], 0) + 1
    for t in sorted(by_type):
        print(f"    {t:<12}: {by_type[t]}")
    r2_count = sum(1 for i in plan if i["r2_key"])
    disk_count = sum(1 for i in plan if i["disk_path"] is not None)
    print(f"R2 objects to delete    : {r2_count}")
    print(f"disk files to delete    : {disk_count}")
    print("-" * 64)
    for item in plan:
        print(f"  [{item['type']:<11}] post={item['post_id']}")
        print(f"      r2_key : {item['r2_key'] or '(none — external/empty)'}")
        print(f"      disk   : {item['disk_path'] or '(none — R2-only)'}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def _run(db_url: str, *, execute: bool) -> int:
    _ensure_backend_on_path()
    if not _ensure_secret_key_env():
        print(
            "WARNING: POINDEXTER_SECRET_KEY not found in env or bootstrap.toml "
            "— R2 deletes will fail (and the run will abort before any DB/disk "
            "change). Ensure ~/.poindexter/bootstrap.toml has poindexter_secret_key.",
        )
    import asyncpg
    from services.bootstrap import build_container
    from services.r2_upload_service import R2UploadService

    host_root = _data_root()
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=4)
    try:
        container = await build_container(pool)
        site_config = container.site_config
        public_base = (site_config.get("storage_public_url", "") or "").rstrip("/")
        r2 = R2UploadService(site_config=site_config)

        rows = await pool.fetch(_SELECT_DEV_DIARY_AV_SQL)
        plan: list[dict] = [
            {
                "asset_id": r["asset_id"],
                "post_id": r["post_id"],
                "type": r["type"],
                "r2_key": r2_key_from_url(r["url"] or "", public_base),
                "disk_path": host_path_from_storage_path(
                    r["storage_path"], host_root,
                ),
            }
            for r in rows
        ]
        _print_plan(plan, public_base, host_root, execute=execute)

        if not plan:
            print("\nNothing to purge — no dev_diary AV assets found.")
            return 0

        if not execute:
            print("\nDRY RUN — no changes made. Re-run with --execute to apply.")
            return 0

        # 1) R2 objects first (irreversible; do it while the rows still exist
        #    so a partial failure leaves the DB handles intact for a re-run).
        r2_ok = r2_skip = r2_fail = 0
        for item in plan:
            key = item["r2_key"]
            if not key:
                r2_skip += 1
                continue
            if await r2.delete_object(key):
                r2_ok += 1
            else:
                r2_fail += 1

        # delete_object returns False only on creds/config failure or an
        # exception (S3 deletes succeed even for absent keys), so ANY failure
        # is a real problem. Abort BEFORE touching disk/DB — deleting the rows
        # now would orphan still-present *public* R2 objects with nothing left
        # to rediscover them. Leave everything intact and re-run after fixing
        # storage config (re-runs are idempotent).
        if r2_fail:
            print(
                f"\nABORT: {r2_fail} of {r2_fail + r2_ok} R2 delete(s) failed — "
                "leaving disk files and DB rows intact so the objects stay "
                "discoverable. Fix storage creds/config and re-run --execute.",
            )
            return 1

        # 2) Host-disk files (idempotent — already-absent is fine).
        disk_ok = disk_absent = 0
        for item in plan:
            p = item["disk_path"]
            if p is None:
                continue
            if p.exists():
                p.unlink()
                disk_ok += 1
            else:
                disk_absent += 1

        # 3) DB rows — assets by id, then the dev_diary AV approvals.
        asset_ids = [item["asset_id"] for item in plan]
        assets_res = await pool.execute(_DELETE_ASSETS_BY_ID_SQL, asset_ids)
        approvals_res = await pool.execute(_DELETE_DEV_DIARY_AV_APPROVALS_SQL)

        print("\n" + "=" * 64)
        print("PURGE APPLIED")
        print("=" * 64)
        print(f"R2 objects deleted : {r2_ok}  (skipped {r2_skip}, external/empty)")
        print(f"disk files deleted : {disk_ok}  (already absent {disk_absent})")
        print(f"media_assets       : {assets_res}")
        print(f"media_approvals    : {approvals_res}")
        print("=" * 64)
        return 0
    finally:
        await pool.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--execute", action="store_true",
        help="Apply the purge. Without this flag the script only prints the plan.",
    )
    parser.add_argument(
        "--database-url", default=None,
        help="Override DB connection. Defaults to env / local prod brain DB.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_url = _resolve_db_url(args.database_url)
    print(f"DB: {db_url.rsplit('@', 1)[-1]}  (execute={args.execute})")
    return asyncio.run(_run(db_url, execute=args.execute))


if __name__ == "__main__":
    sys.exit(main())
