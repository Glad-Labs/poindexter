"""MediaReconciliationJob — DB ↔ R2 drift watchdog for podcast + video media.

Sibling to ``static_export_reconciliation.py``. Catches the case where a
published post's podcast/video either was never generated or was
generated but never reached R2 — both have happened recently:

* 2026-04-29 → 2026-05-11: media generation silently broke because the
  worker container's UID-0 vs appuser-1001 mismatch wrote MP3/MP4 files
  to an unmounted directory. 8+ posts lost their podcast and video
  episodes; nobody noticed for 13 days because the publish_service's
  fire-and-forget asyncio task swallowed the error.

* 2026-05-08 → 2026-05-11: static R2 index froze (the bug
  static_export_reconciliation now catches). Same root cause as above —
  publish_service relied on background tasks completing, but they
  never did.

This watchdog is the safety net: every 15 min it scans published posts
in the recent window and, for any that are missing podcast or video on
R2, fires the regen path. The job is bounded by a per-cycle cap so a
large backlog doesn't pile up on the GPU forever.

## Fail-loud contract

Even when self-healing succeeds, the job emits a ``media_drift``
finding so the operator finds out the upstream regression happened
(and can decide whether to dig into the root cause). The finding's
``severity`` is ``warning`` while regen is succeeding and ``critical``
when regen itself starts failing — that's the case where the human
has to step in.

## Config (``plugin.job.media_reconciliation``)

- ``config.lookback_days`` (default 14) — scan posts published in the
  last N days. Older posts are considered intentionally archived.
- ``config.podcast_cap_per_cycle`` (default 3) — regen at most N missing
  podcasts per cycle. Podcast gen is fast (~30 s) so this is mostly a
  rate-limit on the disk/R2 side.
- ``config.video_cap_per_cycle`` (default 2) — regen at most N missing
  videos per cycle. Video gen runs on the 5090 (~5-10 min per video)
  so the cap matters more.
- ``config.alert_on_drift`` (default true) — emit a finding when drift
  is detected, in addition to running the regen.
- ``config.r2_public_base`` (default
  ``https://pub-1432fdefa18e47ad98f213a8a2bf14d5.r2.dev``) — the base
  URL we HEAD against to verify R2 has the file. Falls back to the
  ``r2_public_url`` app_setting when not provided.
- ``config.podcast_cdn_version`` (default ``v2``) — path prefix on R2.
  Mirrors the upload_podcast_episode contract.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from plugins.job import JobResult
from utils.findings import emit_finding

logger = logging.getLogger(__name__)


_DEFAULT_PODCAST_CDN_VERSION = "v2"


async def _resolve_r2_public_base(pool: Any, config: dict[str, Any]) -> str | None:
    """Resolve the R2 public base URL from job config or app_settings.

    Returns None when neither source is configured — caller treats that
    as "fork hasn't set up R2 yet, skip the job rather than crash".

    2026-05-12 cleanup (poindexter#485): the old ``_DEFAULT_R2_PUBLIC_BASE``
    constant baked Matt's R2 bucket name into a public OSS file. Forks
    would have probed his bucket for media existence and seen drift on
    every cycle.
    """
    explicit = (config.get("r2_public_base") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = 'r2_public_url'",
            )
        base = ((row["value"] if row else "") or "").strip().rstrip("/")
        if base:
            return base
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "media_reconciliation: r2_public_url lookup failed: %s", e,
        )
    return None
_HTTP_TIMEOUT = httpx.Timeout(8.0, connect=3.0)


class MediaReconciliationJob:
    name = "media_reconciliation"
    description = (
        "Reconcile R2 podcast/video assets against Postgres; regen on drift"
    )
    schedule = "every 15 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        lookback_days = int(config.get("lookback_days", 14))
        podcast_cap = max(0, int(config.get("podcast_cap_per_cycle", 3)))
        video_cap = max(0, int(config.get("video_cap_per_cycle", 2)))
        alert_on_drift = bool(config.get("alert_on_drift", True))
        r2_base = await _resolve_r2_public_base(pool, config)
        if not r2_base:
            logger.warning(
                "media_reconciliation: skipped — no R2 public base URL "
                "resolved (set app_settings.r2_public_url or "
                "config.r2_public_base)",
            )
            try:
                emit_finding(
                    source="media_reconciliation",
                    kind="r2_public_base_unresolved",
                    severity="info",
                    title="Media reconciliation skipped — R2 not configured",
                    body=(
                        "Neither config.r2_public_base nor "
                        "app_settings.r2_public_url is set. Media drift "
                        "detection is dormant until one of them is."
                    ),
                    dedup_key="media_reconciliation_r2_public_base_unresolved",
                )
            except Exception:
                # poindexter#455 — used to be silent. emit_finding is the
                # operator-visible signal for "this job is dormant" —
                # losing it silently means R2 stays unconfigured forever
                # with no breadcrumb. Debug-level: the warning above
                # already covers the log channel.
                logger.debug(
                    "[media_reconciliation] emit_finding for "
                    "r2_public_base_unresolved raised — operator visibility "
                    "degrades to log channel only",
                    exc_info=True,
                )
            return JobResult(
                ok=True,
                detail="skipped — no R2 public base configured",
            )
        cdn_ver = config.get("podcast_cdn_version") or _DEFAULT_PODCAST_CDN_VERSION

        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id::text AS id,
                           COALESCE(title, '(untitled)') AS title,
                           COALESCE(content, '') AS content,
                           podcast_url,
                           video_url,
                           published_at
                      FROM posts
                     WHERE status = 'published'
                       AND published_at IS NOT NULL
                       AND published_at >= $1
                     ORDER BY published_at DESC
                    """,
                    since,
                )
        except Exception as e:
            logger.exception("media_reconciliation: DB query failed: %s", e)
            return JobResult(
                ok=False, detail=f"DB query failed: {e}", changes_made=0,
            )

        if not rows:
            return JobResult(
                ok=True,
                detail=f"no published posts in last {lookback_days}d",
                changes_made=0,
                metrics={"scanned": 0, "missing_podcast": 0, "missing_video": 0},
            )

        # HEAD-check R2 for every row in parallel. Local files matter less
        # than R2 (operators read from R2); a file present locally but
        # absent on R2 is an upload-failure case the regen path also
        # handles.
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            results = await asyncio.gather(
                *[
                    self._check_post_media(client, r2_base, cdn_ver, dict(row))
                    for row in rows
                ],
                return_exceptions=False,
            )

        missing_podcast = [r for r in results if r["podcast_missing"]]
        missing_video = [r for r in results if r["video_missing"]]

        if not missing_podcast and not missing_video:
            return JobResult(
                ok=True,
                detail=f"in sync — {len(rows)} posts scanned, no drift",
                changes_made=0,
                metrics={
                    "scanned": len(rows),
                    "missing_podcast": 0,
                    "missing_video": 0,
                },
            )

        logger.warning(
            "media_reconciliation: drift detected — %d missing podcast, "
            "%d missing video out of %d scanned",
            len(missing_podcast), len(missing_video), len(rows),
        )

        # Self-heal: regen the missing files, capped per cycle. Podcast
        # before video — video uses the podcast MP3 as narration, so
        # regenerating the podcast first means a single cycle can recover
        # both for the same post.
        regen_podcast_ok = 0
        regen_podcast_fail = 0
        for r in missing_podcast[:podcast_cap]:
            try:
                if await self._regen_podcast(pool, r):
                    regen_podcast_ok += 1
                else:
                    regen_podcast_fail += 1
            except Exception as e:
                logger.exception(
                    "media_reconciliation: podcast regen for %s raised: %s",
                    r["id"], e,
                )
                regen_podcast_fail += 1

        regen_video_ok = 0
        regen_video_fail = 0
        for r in missing_video[:video_cap]:
            try:
                if await self._regen_video(pool, r):
                    regen_video_ok += 1
                else:
                    regen_video_fail += 1
            except Exception as e:
                logger.exception(
                    "media_reconciliation: video regen for %s raised: %s",
                    r["id"], e,
                )
                regen_video_fail += 1

        regen_failed = regen_podcast_fail + regen_video_fail
        any_regen_run = (
            regen_podcast_ok + regen_podcast_fail
            + regen_video_ok + regen_video_fail
        ) > 0

        if alert_on_drift:
            severity = "critical" if regen_failed > 0 else "warning"
            preview_podcast = "\n".join(
                f"- {r['id']} — {r['title'][:60]}"
                for r in missing_podcast[:5]
            ) or "(none)"
            preview_video = "\n".join(
                f"- {r['id']} — {r['title'][:60]}"
                for r in missing_video[:5]
            ) or "(none)"
            emit_finding(
                source="media_reconciliation",
                kind="media_drift",
                severity=severity,
                title=(
                    f"Media drift: {len(missing_podcast)} missing podcast, "
                    f"{len(missing_video)} missing video"
                    + (" (regen failures)" if regen_failed > 0 else "")
                ),
                body=(
                    f"## Missing podcast ({len(missing_podcast)})\n\n"
                    f"{preview_podcast}\n\n"
                    f"## Missing video ({len(missing_video)})\n\n"
                    f"{preview_video}\n\n"
                    f"## Regen this cycle (capped)\n"
                    f"- podcast: {regen_podcast_ok}/{podcast_cap} ok, "
                    f"{regen_podcast_fail} failed\n"
                    f"- video: {regen_video_ok}/{video_cap} ok, "
                    f"{regen_video_fail} failed\n\n"
                    f"## Likely causes\n"
                    f"1. Worker container UID/HOME mismatch wrote files to "
                    f"unmounted dir (fixed 2026-05-12 — see docker-compose "
                    f"pinpoint bind mounts).\n"
                    f"2. R2 upload silently failed (check r2_upload_service "
                    f"logs for the post_id).\n"
                    f"3. Podcast/video service raised but caller swallowed "
                    f"the error (fire-and-forget anti-pattern).\n"
                ),
                dedup_key="media_drift",
                extra={
                    "scanned": len(rows),
                    "missing_podcast": len(missing_podcast),
                    "missing_video": len(missing_video),
                    "regen_podcast_ok": regen_podcast_ok,
                    "regen_podcast_fail": regen_podcast_fail,
                    "regen_video_ok": regen_video_ok,
                    "regen_video_fail": regen_video_fail,
                },
            )

        return JobResult(
            ok=regen_failed == 0,
            detail=(
                f"drift: -{len(missing_podcast)} podcast / "
                f"-{len(missing_video)} video; "
                f"regen ok podcast={regen_podcast_ok} video={regen_video_ok}, "
                f"failed={regen_failed}"
            ),
            changes_made=regen_podcast_ok + regen_video_ok,
            metrics={
                "scanned": len(rows),
                "missing_podcast": len(missing_podcast),
                "missing_video": len(missing_video),
                "regen_podcast_ok": regen_podcast_ok,
                "regen_podcast_fail": regen_podcast_fail,
                "regen_video_ok": regen_video_ok,
                "regen_video_fail": regen_video_fail,
                "any_regen_run": int(any_regen_run),
            },
        )

    async def _check_post_media(
        self,
        client: httpx.AsyncClient,
        r2_base: str,
        cdn_ver: str,
        row: dict[str, Any],
    ) -> dict[str, Any]:
        """HEAD-check both R2 keys for a post. Returns a row dict augmented
        with ``podcast_missing`` / ``video_missing`` booleans.

        DB-recorded URL is not load-bearing: a post can have podcast_url
        populated but the actual MP3 missing on R2 (upload failed mid-
        flight). We trust R2's HEAD as the source of truth.
        """
        post_id = row["id"]
        podcast_url = f"{r2_base}/podcast/{cdn_ver}/{post_id}.mp3"
        video_url = f"{r2_base}/video/{post_id}.mp4"

        async def _exists(url: str) -> bool:
            try:
                resp = await client.head(url, follow_redirects=True)
                return 200 <= resp.status_code < 300
            except Exception:
                return False

        podcast_exists, video_exists = await asyncio.gather(
            _exists(podcast_url),
            _exists(video_url),
        )
        row["podcast_missing"] = not podcast_exists
        row["video_missing"] = not video_exists
        return row

    async def _regen_podcast(self, pool: Any, row: dict[str, Any]) -> bool:
        """Regenerate one missing podcast + upload to R2 + stamp posts.podcast_url.

        Returns True when both the gen and upload succeeded (signalled
        by a non-None upload URL).
        """
        # Lazy imports — these pull in TTS heavyweight deps we don't want
        # to load when no regen is needed this cycle.
        from services.podcast_service import generate_podcast_episode
        from services.r2_upload_service import upload_podcast_episode

        await generate_podcast_episode(
            row["id"], row["title"], row["content"],
        )
        url = await upload_podcast_episode(row["id"])
        if not url:
            logger.warning(
                "media_reconciliation: podcast upload returned None for %s",
                row["id"],
            )
            return False

        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE posts SET podcast_url = $1 WHERE id::text = $2",
                    url, row["id"],
                )
        except Exception as e:
            # Upload succeeded but DB stamping failed. Surface but
            # consider the regen successful — the file is on R2.
            logger.warning(
                "media_reconciliation: stamped URL upload but DB update "
                "failed for %s: %s", row["id"], e,
            )
        return True

    async def _regen_video(self, pool: Any, row: dict[str, Any]) -> bool:
        """Regenerate one missing video + upload + stamp posts.video_url."""
        from services.r2_upload_service import upload_video_episode
        from services.video_service import generate_video_for_post

        result = await generate_video_for_post(
            row["id"], row["title"], row["content"],
        )
        if not result.success:
            logger.warning(
                "media_reconciliation: video generation failed for %s: %s",
                row["id"], getattr(result, "error", "(no error attr)"),
            )
            return False

        url = await upload_video_episode(row["id"])
        if not url:
            logger.warning(
                "media_reconciliation: video upload returned None for %s",
                row["id"],
            )
            return False

        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE posts SET video_url = $1 WHERE id::text = $2",
                    url, row["id"],
                )
        except Exception as e:
            logger.warning(
                "media_reconciliation: video URL upload but DB update "
                "failed for %s: %s", row["id"], e,
            )
        return True
