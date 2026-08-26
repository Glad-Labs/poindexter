"""MediaOrphanSweepJob — reap unreferenced R2 media objects.

Image and video R2 keys carry a fresh UUID per (re)generation
(``images/featured/{id8}-{uuid8}.jpg``, ``images/inline/{uuid12}.png``,
``video/{uuid}.mp4``), so every regeneration orphans the prior object — and no
other path deletes them (retention policies are DB-table-scoped;
``static_export_orphan_sweep`` only touches ``static/`` JSON;
``media_reconciliation`` only *adds*). This job is the safety net.

Keep-set (an object is KEPT if its key or basename appears here): non-terminal
posts (content + image-url columns), ``media_assets`` (url + storage_path), and
the R2 feed XML (channel art + episode enclosures). Everything else under a
swept prefix that is older than the grace window is an orphan.

Safety: dry-run unless ``media_orphan_sweep_armed=true``; a grace window; a
per-run cap; an empty-keep-set circuit breaker; and a hard prefix guard on the
delete call site. See docs/superpowers/specs/2026-07-11-r2-media-reaper-design.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.job import JobResult
from utils.exception_format import describe_exception
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


def _build_reference_haystack(
    post_rows: list[dict], ma_rows: list[dict], feed_texts: list[str],
) -> str:
    """Concatenate every string that could reference an R2 object key."""
    parts: list[str] = []
    for r in post_rows:
        parts.append(str(r.get("content") or ""))
        parts.append(str(r.get("featured_image_url") or ""))
        parts.append(str(r.get("cover_image_url") or ""))
        parts.append(str(r.get("featured_image_data") or ""))
    for r in ma_rows:
        parts.append(str(r.get("url") or ""))
        parts.append(str(r.get("storage_path") or ""))
    parts.extend(t for t in feed_texts if t)
    return " ".join(parts)


def _is_referenced(key: str, haystack: str) -> bool:
    """True if the full key OR its basename appears in the haystack.

    Basename matching survives domain variance (custom image domain vs the
    ``*.r2.dev`` bucket URL) and extension rewrites.
    """
    if key and key in haystack:
        return True
    base = key.rsplit("/", 1)[-1]
    return bool(base) and base in haystack


def _select_orphans(
    objects: list[dict],
    haystack: str,
    prefixes: tuple[str, ...],
    *,
    now: datetime,
    grace_days: int,
) -> list[dict]:
    """Objects under a swept prefix, unreferenced, older than the grace window."""
    cutoff = now - timedelta(days=grace_days)
    orphans: list[dict] = []
    for obj in objects:
        key = obj["key"]
        if not any(key.startswith(p) for p in prefixes):
            continue  # hard prefix guard
        lm = obj.get("last_modified")
        if lm is not None and lm > cutoff:
            continue  # grace window — protects just-uploaded, not-yet-linked
        if _is_referenced(key, haystack):
            continue
        orphans.append(obj)
    return orphans


class MediaOrphanSweepJob:
    name = "media_orphan_sweep"
    description = (
        "Delete unreferenced images/video/podcast objects from R2 "
        "(dry-run unless media_orphan_sweep_armed=true)"
    )
    schedule = "0 4 * * *"  # daily 04:00 operator-local
    idempotent = True

    _POSTS_SQL = """
        SELECT content,
               featured_image_url,
               cover_image_url,
               featured_image_data::text AS featured_image_data
          FROM posts
         WHERE status NOT IN ('rejected', 'deleted', 'archived')
    """
    _MEDIA_ASSETS_SQL = "SELECT url, storage_path FROM media_assets"

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        site_config = config.get("_site_config")
        if site_config is None:
            return JobResult(
                ok=False, detail="no _site_config bound to job run", changes_made=0,
            )

        armed = site_config.get_bool("media_orphan_sweep_armed", False)
        grace_days = site_config.get_int("media_orphan_sweep_grace_days", 14)
        max_deletes = site_config.get_int(
            "media_orphan_sweep_max_deletes_per_run", 500,
        )
        prefixes = tuple(
            p.strip()
            for p in site_config.get(
                "media_orphan_sweep_prefixes", "images/,video/,podcast/",
            ).split(",")
            if p.strip()
        )
        if not prefixes:
            return JobResult(
                ok=True, detail="no swept prefixes configured", changes_made=0,
            )

        # Source of truth for the keep-set. A failure here aborts before any
        # storage call — never delete when we can't read what's referenced.
        try:
            async with pool.acquire() as conn:
                post_rows = [dict(r) for r in await conn.fetch(self._POSTS_SQL)]
                ma_rows = [dict(r) for r in await conn.fetch(self._MEDIA_ASSETS_SQL)]
        except Exception as e:  # noqa: BLE001
            logger.exception("media_orphan_sweep: keep-set query failed: %s", e)
            return JobResult(
                ok=False, detail=f"DB query failed: {describe_exception(e)}", changes_made=0,
            )

        from services.r2_upload_service import R2UploadService

        r2 = R2UploadService(site_config=site_config)

        # Feed XML is part of the keep-set (channel art + enclosures). If it
        # can't be read we cannot verify podcast/ + video/, so we keep-more:
        # limit the sweep to images/ this cycle rather than risk deleting a
        # feed-only reference.
        feed_texts: list[str] = []
        feed_ok = True
        for feed_key in ("podcast/feed.xml", "video/feed.xml"):
            txt = await r2.get_object_text(feed_key)
            if txt is None:
                feed_ok = False
            else:
                feed_texts.append(txt)
        active_prefixes = prefixes if feed_ok else tuple(
            p for p in prefixes if p == "images/"
        )
        if not feed_ok:
            logger.warning(
                "media_orphan_sweep: feed fetch failed — limiting sweep to %s",
                active_prefixes,
            )

        haystack = _build_reference_haystack(post_rows, ma_rows, feed_texts)

        # Circuit breaker: an empty keep-set is never a normal state for a live
        # site — abort rather than treat every object as an orphan.
        if not haystack.strip():
            return JobResult(
                ok=False,
                detail="empty keep-set — aborting (circuit breaker)",
                changes_made=0,
            )
        if not active_prefixes:
            return JobResult(
                ok=True, detail="no prefixes to sweep this cycle", changes_made=0,
            )

        objects: list[dict] = []
        for prefix in active_prefixes:
            objects.extend(await r2.list_objects(prefix))

        now = datetime.now(timezone.utc)
        orphans = _select_orphans(
            objects, haystack, active_prefixes, now=now, grace_days=grace_days,
        )
        orphans.sort(key=lambda o: o["key"])
        orphan_bytes = sum(o["size"] for o in orphans)
        to_act = orphans[:max_deletes]
        capped_remainder = len(orphans) - len(to_act)

        def _prefix_of(key: str) -> str:
            return next((p for p in active_prefixes if key.startswith(p)), "?")

        by_prefix: dict[str, dict[str, int]] = {}
        for o in orphans:
            b = by_prefix.setdefault(_prefix_of(o["key"]), {"objects": 0, "bytes": 0})
            b["objects"] += 1
            b["bytes"] += o["size"]

        deleted = 0
        bytes_reclaimed = 0
        if armed:
            for o in to_act:
                key = o["key"]
                if not any(key.startswith(p) for p in active_prefixes):
                    continue  # belt-and-suspenders prefix guard
                if await r2.delete_object(key):
                    deleted += 1
                    bytes_reclaimed += o["size"]

        mode = "armed" if armed else "dry-run"
        shown_n = deleted if armed else len(orphans)
        shown_bytes = bytes_reclaimed if armed else orphan_bytes
        metrics = {
            "mode": mode,
            "scanned": len(objects),
            "orphans_found": len(orphans),
            "orphan_bytes": orphan_bytes,
            "deleted": deleted,
            "bytes_reclaimed": bytes_reclaimed,
            "capped_remainder": capped_remainder,
            "by_prefix": by_prefix,
        }
        verb = "reclaimed" if armed else "would reclaim"
        by_prefix_md = "\n".join(
            f"- `{p}` — {v['objects']} obj / {v['bytes'] / 1024 / 1024:.1f} MB"
            for p, v in sorted(by_prefix.items())
        ) or "- (none)"
        emit_finding(
            source="media_orphan_sweep",
            kind="media_orphan_sweep",
            severity="info",
            title=(
                f"Media reaper ({mode}): {verb} {shown_n} objects / "
                f"{shown_bytes / 1024 / 1024:.1f} MB"
            ),
            body=(
                f"## Media orphan sweep ({mode})\n\n"
                f"Scanned {len(objects)} objects under {list(active_prefixes)}; "
                f"{len(orphans)} orphaned ({orphan_bytes / 1024 / 1024:.1f} MB).\n\n"
                f"### By prefix\n{by_prefix_md}\n\n"
                + (
                    f"Deleted {deleted} this cycle"
                    + (f"; {capped_remainder} over the cap remain" if capped_remainder else "")
                    if armed
                    else "Dry-run — nothing deleted. Set "
                    "`media_orphan_sweep_armed=true` to arm."
                )
            ),
            dedup_key="media_orphan_sweep",
            extra=metrics,
        )

        detail = (
            f"{mode}: {'deleted' if armed else 'would delete'} {shown_n} orphan(s), "
            f"{shown_bytes // (1024 * 1024)} MB"
            + (" (capped this cycle; more remain)" if capped_remainder else "")
        )
        return JobResult(
            ok=True, detail=detail, changes_made=deleted, metrics=metrics,
        )
