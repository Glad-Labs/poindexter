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
in the recent window and, for any that are missing podcast or video,
re-dispatches the gated pipeline (it never authors media itself — see
Pass 2). The job is bounded by a per-cycle cap so a large backlog
doesn't re-arm the dispatcher all at once.

## Fail-loud contract

Even when self-healing succeeds, the job emits a ``media_drift``
finding so the operator finds out the upstream regression happened
(and can decide whether to dig into the root cause). The finding's
``severity`` is always ``warning``: re-dispatch never "fails" the job —
drift that can't be re-dispatched (no task seam, or the per-task cap was
hit) is surfaced in the finding for the operator, not counted as an error.

## Two reconciliation passes

The job runs two passes per cycle, because two *different* drifts
produce the same symptom (a media-wanting post with no asset):

1. **Row-stamp pass (unbounded, cheap).** For every media-wanting post
   whose file IS present on R2 but has NO ``media_assets`` row, stamp
   the row idempotently. This is the dominant gap (Glad-Labs/poindexter
   #560): 81 MP3 / 60 MP4 files already exist in-container/on-R2 but
   only ~16-17 of 61 media-wanting posts have a DB asset row, because
   the file-present-row-absent case was never detected — the old job
   only wrote a row as a *side-effect of regeneration*. Stamping is a
   pure DB write (no GPU, no upload), so this pass is NOT capped and
   NOT time-windowed.
2. **Re-dispatch pass (capped).** For media-wanting posts whose media is
   genuinely ABSENT (no ``media_assets`` row), re-dispatch the gated
   pipeline — clear the source task's dispatch marker so the dispatcher
   re-runs it through the full quality gates + Gate-2 — capped per cycle
   and per task. The watchdog NEVER authors media directly (#1904); a
   *delivered* episode whose R2 object vanished is re-uploaded from the
   durable local render (no re-render), falling back to re-dispatch only
   if that local file is also gone.

## Approval-gate seeding (self-healing)

Both passes route every stamped asset through ``_record_media_asset`` →
``_seed_approval_gate``, which seeds a ``media_approvals`` row (idempotent
via ``ON CONFLICT``, non-fatal) so the asset can't reach a public feed
un-reviewed. This is the durable fix for the 2026-05-27→06-13 podcast-feed
freeze: reconciliation stamped ``media_assets`` rows but the only seeder
(``podcast_distribute``) is dormant behind ``podcast_pipeline_trigger_enabled``,
so reconciliation-made podcasts never entered the approval queue and the
gated feed silently excluded them (``feedback_approval_gate_all_media``).

## Config (``plugin.job.media_reconciliation``)

- ``config.lookback_days`` (default 14) — RE-DISPATCH-pass window: only
  re-dispatch genuinely-missing media for posts published in the last N
  days (older drift is surfaced for operator triage, not auto-healed).
  The cheap row-stamp pass ignores this and scans the full
  ``max_lookback_days`` window — stamping a missing row for an old post
  costs nothing.
- ``config.max_lookback_days`` (default 0 = unbounded) — SCAN window
  for the row-stamp pass. ``0`` scans every published media-wanting
  post regardless of age, which is what closes #560 (the gap is
  dominated by posts older than 14 days). Set a positive value to bound
  the per-cycle HEAD-check fan-out on very large sites.
- ``config.podcast_cap_per_cycle`` (default 3) — re-dispatch at most N
  missing podcasts per cycle. Each re-dispatch just clears a marker (the
  gated podcast_pipeline does the actual render), so this is a light
  rate-limit on how many tasks re-arm per cycle.
- ``config.video_cap_per_cycle`` (default 2) — re-dispatch at most N
  missing videos per cycle. The gated Stage-2 pipeline runs the render
  on the 5090 (~5-10 min), so the cap bounds GPU pile-up downstream.
- ``config.alert_on_drift`` (default true) — emit a finding when drift
  is detected, in addition to re-dispatching.
- ``config.r2_public_base`` (default: resolved from the
  ``storage_public_url`` app_setting) — the base URL we HEAD against to
  verify R2 has the file. None when neither config nor app_setting is set.
- ``config.podcast_cdn_version`` (default ``v2``) — path prefix on R2.
  Mirrors the podcast delivery R2 key (``podcast/{ver}/{post_id}.mp3``).
"""

from __future__ import annotations

import asyncio
import logging
import os
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
                "SELECT value FROM app_settings WHERE key = 'storage_public_url'",
            )
        base = ((row["value"] if row else "") or "").strip().rstrip("/")
        if base:
            return base
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "media_reconciliation: storage_public_url lookup failed: %s", e,
        )
    return None
_HTTP_TIMEOUT = httpx.Timeout(8.0, connect=3.0)


# Default exclude list — dev_diary "What we shipped" posts. These are
# build-in-public status updates that don't need TTS or video. Operator
# can override via the comma-separated app_settings key below.
_DEFAULT_EXCLUDE_SLUG_PREFIXES: tuple[str, ...] = (
    "what-we-shipped-",
    "daily-dev-diary-",
)


async def _resolve_exclude_slug_prefixes(
    pool: Any, config: dict[str, Any],
) -> list[str]:
    """Read ``media_reconciliation_exclude_slug_prefixes`` from
    ``app_settings`` (comma-separated). Falls back to PluginConfig
    ``config.exclude_slug_prefixes`` (list), then to
    :data:`_DEFAULT_EXCLUDE_SLUG_PREFIXES`.

    Posts whose ``slug`` starts with any prefix are excluded from media
    reconciliation — they're declared exempt from podcast/video
    derivatives. Captured 2026-05-15: dev_diary "What we shipped" posts
    were generating fake media-drift alerts every 15 min because the
    reconciliation job didn't know they were text-only.
    """
    cfg_val = config.get("exclude_slug_prefixes")
    if isinstance(cfg_val, (list, tuple)) and cfg_val:
        return [str(p).strip() for p in cfg_val if str(p).strip()]
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = 'media_reconciliation_exclude_slug_prefixes'",
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "media_reconciliation: exclude-prefix lookup failed: %s — "
            "falling back to defaults", e,
        )
        return list(_DEFAULT_EXCLUDE_SLUG_PREFIXES)
    if row is None or not row["value"]:
        return list(_DEFAULT_EXCLUDE_SLUG_PREFIXES)
    parts = [p.strip() for p in str(row["value"]).split(",")]
    return [p for p in parts if p]


# Fingerprint of the last drift state we emitted a ``media_drift`` finding
# for. The job runs every 15 min and — captured in the 2026-07-01 audit —
# re-emitted the byte-identical "0 missing podcast, 6 missing video"
# finding 191 times in 48h while six unresolvable videos sat outside the
# regen window. Delivery is deduped downstream (alert_dedup_state), but the
# emission spam still drowned the Findings dashboard (77% of all findings).
# Emit only when the drift SET changes; a cleared-then-recurring drift
# re-fires because clearing also updates the stored fingerprint.
_DRIFT_FINGERPRINT_KEY = "media_reconciliation_last_drift_fingerprint"


def _drift_fingerprint(
    missing_podcast: list[dict[str, Any]],
    missing_video: list[dict[str, Any]],
    redeliver_podcast: list[dict[str, Any]],
) -> str:
    """Stable hash of WHICH posts are drifted (not the per-cycle heal stats).

    Empty drift is the empty string (the "unset" sentinel per
    ``feedback_app_settings_value_not_null``), so a fully-healed state
    reads the same as never-drifted.
    """
    import hashlib

    if not (missing_podcast or missing_video or redeliver_podcast):
        return ""
    payload = "|".join(
        ",".join(sorted(str(r["id"]) for r in rows))
        for rows in (missing_podcast, missing_video, redeliver_podcast)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _looks_like_fingerprint(value: str) -> bool:
    """A stored fingerprint is a 64-char sha256 hexdigest; anything else
    (blank, or a value from a key collision/manual edit) reads as unset."""
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


async def _read_last_drift_fingerprint(pool: Any) -> str:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM app_settings WHERE key = $1",
                _DRIFT_FINGERPRINT_KEY,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "media_reconciliation: drift-fingerprint read failed: %s — "
            "treating as changed (emit)", e,
        )
        return ""
    value = str(row["value"]) if row and row["value"] else ""
    return value if _looks_like_fingerprint(value) else ""


async def _write_last_drift_fingerprint(pool: Any, fingerprint: str) -> None:
    """UPSERT the fingerprint (same state-in-app_settings pattern as the
    findings_alert_route_watermark). Best-effort — a failed write just
    means one duplicate finding next cycle."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings (key, value, category, description, updated_at)
                VALUES ($1, $2, 'plugin_telemetry', $3, NOW())
                ON CONFLICT (key) DO UPDATE
                  SET value = EXCLUDED.value, updated_at = NOW()
                """,
                _DRIFT_FINGERPRINT_KEY,
                fingerprint,
                (
                    "Hash of the drifted-post set last reported by the "
                    "media_drift finding. MediaReconciliationJob emits only "
                    "when this changes. Operators can blank it to force a "
                    "re-emit next cycle."
                ),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "media_reconciliation: drift-fingerprint write failed: %s", e,
        )


class MediaReconciliationJob:
    name = "media_reconciliation"
    description = (
        "Reconcile R2 podcast/video assets against Postgres; regen on drift"
    )
    schedule = "every 15 minutes"
    idempotent = True

    async def run(self, pool: Any, config: dict[str, Any]) -> JobResult:
        # DI seam (glad-labs-stack#330): scheduler seeds `_site_config`
        # on every config dict. Captured here so the regen helpers can
        # build a fresh R2UploadService per regen attempt.
        self._site_config = config.get("_site_config")

        lookback_days = int(config.get("lookback_days", 14))
        # Row-stamp pass scans the full window (default unbounded). The
        # #560 gap is dominated by posts > 14d old whose files exist on
        # R2 but never got a media_assets row — see module docstring.
        max_lookback_days = int(config.get("max_lookback_days", 0))
        podcast_cap = max(0, int(config.get("podcast_cap_per_cycle", 3)))
        video_cap = max(0, int(config.get("video_cap_per_cycle", 2)))
        alert_on_drift = bool(config.get("alert_on_drift", True))
        # Filtering moved off slug-prefix patterns onto the canonical
        # ``posts.media_to_generate`` array (Glad-Labs/glad-labs-stack
        # #482 + #195). A post's media policy is the array of media
        # types it should produce; an empty array means the post is
        # exempt from podcast/video reconciliation by design (the
        # dev_diary niche seeds this way per
        # ``niches.default_media_to_generate``).
        #
        # The slug-prefix exclude is retained as a secondary safety
        # net only — operators with a niche whose seed array is wrong
        # can still skip a slug pattern while the niche policy is
        # being repaired. Drop the slug exclude entirely once that
        # backstop is no longer needed.
        exclude_slug_prefixes = await _resolve_exclude_slug_prefixes(
            pool, config,
        )
        r2_base = await _resolve_r2_public_base(pool, config)
        if not r2_base:
            logger.warning(
                "media_reconciliation: skipped — no R2 public base URL "
                "resolved (set app_settings.storage_public_url or "
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
                        "app_settings.storage_public_url is set. Media drift "
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

        # Scan window for the row-stamp pass. ``max_lookback_days <= 0``
        # means unbounded (scan every published media-wanting post) — the
        # default, because #560's gap is dominated by old posts whose
        # files exist on R2 but never got a media_assets row. ``since``
        # of ``None`` disables the time predicate in the SQL below.
        since = (
            None
            if max_lookback_days <= 0
            else datetime.now(timezone.utc) - timedelta(days=max_lookback_days)
        )
        # Regen-pass cutoff: genuinely-missing media is only regenerated
        # for posts inside ``lookback_days`` (GPU-bound; older posts are
        # archived for regen). A NULL published_at sorts as "old".
        regen_cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        try:
            async with pool.acquire() as conn:
                # Media URLs are computed deterministically from r2_base +
                # cdn_ver + post_id (see _check_post_media). The DB lookup
                # is just for the regen path's content + title; the URLs
                # live in ``media_assets`` (one row per (post_id, type))
                # and are recorded by ``_record_media_asset`` after a
                # successful regen + upload.
                # Build a list of LIKE patterns for the exclude-prefix
                # filter. Pass as a single text[] so the SQL stays
                # parameterized — no string concatenation into the query.
                exclude_patterns = [f"{p}%" for p in exclude_slug_prefixes]
                # Pull ``media_to_generate`` so the per-row check can
                # distinguish "expected and missing" from "not expected
                # at all" — only the former counts as drift. Skip rows
                # whose policy array is empty or NULL (treated as
                # "exempt") via the SQL filter so the HEAD-check fan-out
                # below stays small. ``$1`` may be NULL (unbounded scan).
                rows = await conn.fetch(
                    """
                    SELECT id::text AS id,
                           COALESCE(title, '(untitled)') AS title,
                           COALESCE(content, '') AS content,
                           published_at,
                           COALESCE(media_to_generate, ARRAY[]::text[]) AS media_to_generate
                      FROM posts
                     WHERE status = 'published'
                       AND published_at IS NOT NULL
                       AND ($1::timestamptz IS NULL OR published_at >= $1)
                       AND NOT (slug ILIKE ANY($2::text[]))
                       AND cardinality(COALESCE(media_to_generate, ARRAY[]::text[])) > 0
                     ORDER BY published_at DESC
                    """,
                    since,
                    exclude_patterns,
                )
                # Which (post_id, type) already have a media_assets row?
                # The row-stamp pass below only stamps posts whose file is
                # present on R2 but whose DB row is absent — so we need the
                # existing set up front. One batch query keyed on the
                # scanned ids keeps this O(1) round-trips.
                post_ids = [r["id"] for r in rows]
                existing_rows = (
                    await conn.fetch(self._EXISTING_MEDIA_SQL, post_ids)
                    if post_ids
                    else []
                )
        except Exception as e:
            logger.exception("media_reconciliation: DB query failed: %s", e)
            return JobResult(
                ok=False, detail=f"DB query failed: {e}", changes_made=0,
            )

        # Set of (post_id, asset_type) pairs that already have a row. Post-#1460
        # the long-form video type is just ``video`` (``video_long`` was
        # collapsed in), so the (post, "video") membership the video-drift check
        # consults is a direct type match. ``existing_assets`` carries the
        # storage columns alongside it so the podcast integrity check (#1904)
        # can tell a delivered (cloudflare_r2) episode from a pending local one.
        existing_pairs: set[tuple[str, str]] = set()
        existing_assets: dict[tuple[str, str], dict[str, Any]] = {}
        for er in existing_rows:
            key = (er["post_id"], er["type"])
            existing_pairs.add(key)
            cand = {
                "storage_provider": er.get("storage_provider"),
                "storage_path": er.get("storage_path"),
                "url": er.get("url"),
            }
            # When several rows resolve to one (post, type) — e.g. a task-keyed
            # local render plus a post-keyed delivered row — prefer the
            # delivered one, since that's the row whose R2 object we integrity-check.
            prior = existing_assets.get(key)
            if prior is None or cand["storage_provider"] == "cloudflare_r2":
                existing_assets[key] = cand

        if not rows:
            window = (
                "all-time" if since is None else f"last {max_lookback_days}d"
            )
            return JobResult(
                ok=True,
                detail=f"no published media-wanting posts ({window})",
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
                    self._check_post_media(
                        client, r2_base, cdn_ver, dict(row), existing_pairs,
                        existing_assets,
                    )
                    for row in rows
                ],
                return_exceptions=False,
            )

        missing_podcast = [r for r in results if r["podcast_missing"]]
        missing_video = [r for r in results if r["video_missing"]]
        # Delivered-then-lost: an episode that WAS on R2 (storage_provider=
        # cloudflare_r2) whose object has since vanished. Not "missing" (the row
        # exists) — re-delivered from the durable local render in Pass 2, never
        # re-rendered.
        redeliver_podcast = [r for r in results if r.get("podcast_delivered_gone")]

        # ---- Pass 1: row-stamp (unbounded, cheap, no GPU) ---------------
        # The #560 gap: file IS on R2 but the media_assets row is absent.
        # Stamp the deterministic R2 URL onto a row so cleanup / retention
        # / cost-attribution / the public feed see the asset. This is a
        # pure DB write, so it's NOT capped and NOT time-windowed — every
        # file-present-row-absent post gets healed in a single cycle.
        # Video is no longer R2-reconciled (#1460): Stage-2 produces it
        # task-keyed and distribution back-stamps the media_assets row, so
        # there's no {post_id}.mp4 on R2 to stamp. Only podcast files get the
        # cheap file-present row-stamp pass.
        stamped_podcast = 0
        for r in results:
            pid = r["id"]
            if (
                r.get("podcast_exists")
                and (pid, "podcast") not in existing_pairs
            ):
                await self._record_media_asset(
                    pool, post_id=pid, asset_type="podcast",
                    url=r["podcast_url"],
                )
                existing_pairs.add((pid, "podcast"))
                stamped_podcast += 1

        stamped_total = stamped_podcast
        if stamped_total:
            logger.info(
                "media_reconciliation: stamped %d file-present-row-absent "
                "podcast media_assets rows — #560 gap close",
                stamped_total,
            )

        if not missing_podcast and not missing_video and not redeliver_podcast:
            # Drift fully cleared: reset the change-gate fingerprint so a
            # RECURRENCE of the same drift set re-emits (it's news again).
            if await _read_last_drift_fingerprint(pool):
                await _write_last_drift_fingerprint(pool, "")
            return JobResult(
                ok=True,
                detail=(
                    f"in sync — {len(rows)} posts scanned, no drift; "
                    f"stamped {stamped_total} missing rows"
                ),
                changes_made=stamped_total,
                metrics={
                    "scanned": len(rows),
                    "missing_podcast": 0,
                    "missing_video": 0,
                    "stamped_podcast": stamped_podcast,
                },
            )

        logger.warning(
            "media_reconciliation: drift detected — %d missing podcast, "
            "%d missing video, %d podcast R2-lost out of %d scanned",
            len(missing_podcast), len(missing_video),
            len(redeliver_podcast), len(rows),
        )

        # ---- Pass 2: self-heal genuinely-absent media ------------------
        # NEITHER lane authors media. On drift the watchdog re-dispatches the
        # gated pipeline (clear the source task's dispatch marker, capped per
        # task) and lets the dispatcher re-run it through the full quality gates
        # + Gate-2 — a watchdog must never generate content directly (the
        # regenerate_stock_images class of bug, #1904). Posts with no resolvable
        # ``pipeline_task_id`` seam can't be re-dispatched and only surface in
        # the finding (fail-loud, not silently healed). Only posts inside
        # ``lookback_days`` are acted on; older drift is surfaced, not healed.
        def _in_regen_window(r: dict[str, Any]) -> bool:
            pub = r.get("published_at")
            return pub is not None and pub >= regen_cutoff

        # Podcast re-dispatch (bounded per cycle by podcast_cap). Like video,
        # _redispatch_podcast returns False for the can't-re-dispatch cases (no
        # pipeline_task_id, or the per-task cap was hit) — surfaced, not failed.
        redispatch_podcast = [r for r in missing_podcast if _in_regen_window(r)]
        redispatched_podcast = 0
        podcast_attempts = 0
        for r in redispatch_podcast[:podcast_cap]:
            podcast_attempts += 1
            try:
                if await self._redispatch_podcast(pool, r):
                    redispatched_podcast += 1
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "media_reconciliation: podcast re-dispatch for %s raised: %s",
                    r["id"], e,
                )
        podcast_unresolved = podcast_attempts - redispatched_podcast

        # Podcast re-delivery (bounded per cycle by podcast_cap): an already-
        # delivered episode whose R2 object vanished is re-uploaded from the
        # durable local render — no re-render, no Gate-2 re-entry (it was already
        # approved). If the local file is also gone, fall back to a gated
        # re-dispatch (which re-runs it through Gate-2). Not window-bound — an
        # approved episode that lost its file is worth restoring at any age.
        redelivered_podcast = 0
        for r in redeliver_podcast[:podcast_cap]:
            try:
                if await self._redeliver_podcast(r):
                    redelivered_podcast += 1
                elif await self._redispatch_podcast(pool, r):
                    redispatched_podcast += 1
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "media_reconciliation: podcast re-deliver for %s raised: %s",
                    r["id"], e,
                )

        # Video re-dispatch (bounded per cycle by video_cap). _redispatch_video
        # returns False for the can't-re-dispatch cases (no pipeline_task_id, or
        # the per-task cap was hit) — surfaced in the finding, not job failures.
        redispatch_candidates = [r for r in missing_video if _in_regen_window(r)]
        redispatched_video = 0
        redispatch_attempts = 0
        for r in redispatch_candidates[:video_cap]:
            redispatch_attempts += 1
            try:
                if await self._redispatch_video(pool, r):
                    redispatched_video += 1
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "media_reconciliation: video re-dispatch for %s raised: %s",
                    r["id"], e,
                )
        redispatch_unresolved = redispatch_attempts - redispatched_video

        # Neither lane authors media, so nothing here marks the job failed —
        # drift that can't be re-dispatched (no task seam / capped) is surfaced
        # in the finding for the operator, mirroring the video lane.
        #
        # Change-gated (2026-07-01 audit): emit only when the drifted-post
        # SET differs from what the last finding reported. A chronic
        # unchanged drift (e.g. videos outside the regen window with no
        # task seam) gets ONE finding, not one per 15-min cycle; drift
        # that grows, shrinks, clears, or recurs re-fires because every
        # state change (including → empty) updates the fingerprint.
        drift_fp = _drift_fingerprint(
            missing_podcast, missing_video, redeliver_podcast,
        )
        drift_changed = drift_fp != await _read_last_drift_fingerprint(pool)
        if drift_changed:
            await _write_last_drift_fingerprint(pool, drift_fp)
        if alert_on_drift and drift_changed:
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
                severity="warning",
                title=(
                    f"Media drift: {len(missing_podcast)} missing podcast, "
                    f"{len(missing_video)} missing video"
                    + (
                        f", {len(redeliver_podcast)} podcast R2-lost"
                        if redeliver_podcast else ""
                    )
                ),
                body=(
                    f"## Missing podcast ({len(missing_podcast)})\n\n"
                    f"{preview_podcast}\n\n"
                    f"## Missing video ({len(missing_video)})\n\n"
                    f"{preview_video}\n\n"
                    f"## Podcast rows stamped this cycle (file present, row absent)\n"
                    f"- podcast: {stamped_podcast}\n\n"
                    f"## Self-heal this cycle (gated re-dispatch, regen-window only)\n"
                    f"- podcast re-dispatch: {redispatched_podcast}/{podcast_attempts} "
                    f"cleared, {podcast_unresolved} unresolved (no task / capped)\n"
                    f"- video re-dispatch: {redispatched_video}/{redispatch_attempts} "
                    f"cleared, {redispatch_unresolved} unresolved (no task / capped)\n"
                    f"- podcast re-deliver (R2 object lost): {redelivered_podcast}/"
                    f"{len(redeliver_podcast)} re-uploaded from local render\n\n"
                    f"## Likely causes\n"
                    f"1. Worker container UID/HOME mismatch wrote files to "
                    f"unmounted dir (fixed 2026-05-12 — see docker-compose "
                    f"pinpoint bind mounts).\n"
                    f"2. The source task never produced a podcast/video script, "
                    f"so the gated pipeline has nothing to render.\n"
                    f"3. Re-dispatch unresolved: the post has no pipeline_task_id "
                    f"seam, or it hit its {{podcast,media_pipeline}}_redispatch_max.\n"
                ),
                dedup_key="media_drift",
                extra={
                    "scanned": len(rows),
                    "missing_podcast": len(missing_podcast),
                    "missing_video": len(missing_video),
                    "stamped_podcast": stamped_podcast,
                    "redispatched_podcast": redispatched_podcast,
                    "podcast_unresolved": podcast_unresolved,
                    "redelivered_podcast": redelivered_podcast,
                    "redispatched_video": redispatched_video,
                    "redispatch_unresolved": redispatch_unresolved,
                },
            )

        return JobResult(
            ok=True,
            detail=(
                f"drift: -{len(missing_podcast)} podcast / "
                f"-{len(missing_video)} video; "
                f"stamped {stamped_total} podcast rows; "
                f"podcast re-dispatched={redispatched_podcast}, "
                f"podcast re-delivered={redelivered_podcast}, "
                f"video re-dispatched={redispatched_video}"
            ),
            changes_made=(
                stamped_total + redispatched_podcast
                + redelivered_podcast + redispatched_video
            ),
            metrics={
                "scanned": len(rows),
                "missing_podcast": len(missing_podcast),
                "missing_video": len(missing_video),
                "stamped_podcast": stamped_podcast,
                "redispatched_podcast": redispatched_podcast,
                "podcast_unresolved": podcast_unresolved,
                "redelivered_podcast": redelivered_podcast,
                "redispatched_video": redispatched_video,
                "redispatch_unresolved": redispatch_unresolved,
            },
        )

    async def _check_post_media(
        self,
        client: httpx.AsyncClient,
        r2_base: str,
        cdn_ver: str,
        row: dict[str, Any],
        existing_pairs: set[tuple[str, str]] | None = None,
        existing_assets: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Derive podcast + video presence from the DB row, not an R2 HEAD.

        Podcast presence (#1904) is "has a ``media_assets`` podcast row"
        (``existing_assets``, resolved via the post_id OR the task seam), exactly
        like video. A post is *genuinely missing* a podcast only when it has no
        row AND no R2 file — a file-present-row-absent post is healed by the
        cheap #560 stamp pass, and a rendered-but-pending podcast
        (``storage_provider='local'``, not yet on R2) is correctly waiting in the
        Gate-2 queue, not missing. The R2 HEAD is retained only to drive that
        stamp pass and to flag a *delivered* (cloudflare_r2) episode whose object
        has since vanished (``podcast_delivered_gone`` → re-deliver, not
        re-render). Video (#1460) is produced task-keyed by Stage-2 and
        back-stamped at distribution — presence is its ``existing_pairs`` row.

        Returns the row dict augmented with ``podcast_missing`` /
        ``podcast_delivered_gone`` / ``video_missing`` (plus the podcast
        file-present + resolved-asset fields the Pass-1/Pass-2 paths consume).
        """
        existing_pairs = existing_pairs or set()
        existing_assets = existing_assets or {}
        post_id = row["id"]
        podcast_url = f"{r2_base}/podcast/{cdn_ver}/{post_id}.mp3"

        async def _exists(url: str) -> bool:
            try:
                resp = await client.head(url, follow_redirects=True)
                return 200 <= resp.status_code < 300
            except Exception:
                return False

        # Per-type policy: only flag missing if the post's
        # ``media_to_generate`` array opts in to that media type. A
        # post whose array is empty (dev_diary etc.) has already been
        # filtered out by the SELECT above, but checking again here
        # keeps this method correct under direct callers / tests.
        media_policy = set(row.get("media_to_generate") or [])
        wants_podcast = "podcast" in media_policy
        wants_video = bool(
            {"video", "video_short"} & media_policy
        )

        # Podcast: HEAD only when wanted — saves a round-trip on text-only
        # posts and avoids spurious 404 noise in R2 access logs. File-present
        # drives the cheap row-stamp pass (#560).
        podcast_exists = await _exists(podcast_url) if wants_podcast else False
        podcast_asset = (
            existing_assets.get((post_id, "podcast")) if wants_podcast else None
        )
        has_podcast_row = podcast_asset is not None
        podcast_delivered = bool(
            podcast_asset
            and (podcast_asset.get("storage_provider") or "") == "cloudflare_r2"
        )
        # Presence is the DB row, not the R2 HEAD (#1904). "Genuinely missing"
        # = no row AND no R2 file: a file-present-row-absent post is healed by
        # the cheap #560 stamp pass below (never a re-dispatch), and a
        # rendered-but-pending podcast (row present, storage_provider='local',
        # not yet on R2) is correctly waiting in the Gate-2 queue.
        row["podcast_missing"] = (
            wants_podcast and not has_podcast_row and not podcast_exists
        )
        # A delivered (R2) episode whose object has since vanished → re-deliver
        # the durable local render (no re-render). The HEAD is retained only for
        # this integrity check + the stamp pass.
        row["podcast_delivered_gone"] = (
            wants_podcast and podcast_delivered and not podcast_exists
        )
        row["podcast_exists"] = wants_podcast and podcast_exists
        row["podcast_url"] = podcast_url if wants_podcast else ""
        # Stash the resolved podcast asset (storage_path/url/provider) so the
        # Pass-2 re-deliver path needn't re-query the DB.
        row["podcast_asset"] = podcast_asset or {}

        # Video: presence is a media_assets video row, not an R2 file. No HEAD,
        # no row-stamp pass for video (it's never on the {post_id}.mp4 path); a
        # wants-video post with no video row is drift → re-dispatched (Pass 2).
        row["video_missing"] = wants_video and (post_id, "video") not in existing_pairs
        row["video_exists"] = False
        row["video_url"] = ""
        return row

    async def _record_media_asset(
        self, pool: Any, *, post_id: str, asset_type: str, url: str,
    ) -> None:
        """Record a regenerated media URL in ``media_assets``.

        ``media_assets`` has no natural-key unique index, so this does
        a SELECT-then-UPDATE-or-INSERT instead of ON CONFLICT. The
        regen path is rare (only fires on a missing R2 object) so the
        extra round-trip is fine; the alternative (INSERT-only) would
        accumulate duplicate rows per regen.

        Non-load-bearing: if this fails, the file is already on R2 and
        the post is reachable via the deterministic URL pattern in
        ``_check_post_media``. We log and continue.
        """
        try:
            async with pool.acquire() as conn:
                updated = await conn.execute(
                    """
                    UPDATE media_assets
                       SET url = $1,
                           storage_provider = 'cloudflare_r2',
                           source = 'reconciliation',
                           updated_at = NOW()
                     WHERE post_id::text = $2 AND type = $3
                    """,
                    url, post_id, asset_type,
                )
                # asyncpg returns "UPDATE N" — N==0 means no row matched
                # and we should INSERT. Non-string returns (test mocks,
                # cursors that don't propagate the tag) get treated the
                # same as "no rows updated" so the INSERT still runs.
                rows_updated = (
                    isinstance(updated, str)
                    and not updated.endswith(" 0")
                )
                if not rows_updated:
                    await conn.execute(
                        """
                        INSERT INTO media_assets
                            (post_id, type, source, storage_provider, url)
                        VALUES ($1::uuid, $2, 'reconciliation',
                                'cloudflare_r2', $3)
                        """,
                        post_id, asset_type, url,
                    )
        except Exception as e:
            logger.warning(
                "media_reconciliation: media_assets stamp failed for "
                "post=%s type=%s: %s", post_id, asset_type, e,
            )
            # Stamp failed → the asset row may not exist; don't seed a gate
            # for an asset we couldn't record.
            return

        # Self-healing approval gate (feedback_approval_gate_all_media):
        # every reconciliation-stamped asset seeds its per-medium gate row
        # inline, so the asset can't reach a public feed un-reviewed. THIS
        # is the durable fix for the 2026-05-27→06-13 podcast-feed freeze —
        # reconciliation used to stamp media_assets without ever seeding
        # media_approvals (the only seeder, podcast_distribute, is dormant
        # behind podcast_pipeline_trigger_enabled), so reconciliation-made
        # podcasts never entered the approval queue and the gated feed
        # silently excluded them.
        await self._seed_approval_gate(pool, post_id, asset_type)

    async def _seed_approval_gate(
        self, pool: Any, post_id: str, asset_type: str,
    ) -> None:
        """Seed the per-medium approval gate for a freshly-stamped asset.

        Idempotent + non-fatal. ``record_pending`` is
        ``ON CONFLICT (post_id, medium) DO NOTHING`` so re-stamping never
        clobbers a prior operator decision, and it runs its own
        auto-approve tiers internally. A seed failure MUST NOT break
        reconciliation — the asset is already stamped + on R2, the gate is
        additive — so we log (a distinct message, so a seed failure isn't
        mistaken for a stamp failure) and continue.

        ``asset_type`` is the media_assets *type*; it maps to its
        media_approvals *medium* verbatim (``podcast`` → ``podcast``,
        ``video`` → ``video`` — post-#1460 the asset type and medium are
        identical), so no translation table is needed here. In the live
        reconciliation flow only ``podcast`` is stamped now — video is
        re-dispatched through Stage-2 rather than stamped here.
        """
        try:
            from services.media_approval_service import record_pending

            await record_pending(pool, post_id, asset_type)
        except Exception as e:  # noqa: BLE001 — gate seed is additive, never fatal
            logger.warning(
                "media_reconciliation: approval-gate seed failed for "
                "post=%s medium=%s: %s — asset is stamped, gate is additive",
                post_id, asset_type, e,
            )

    # Which (post_id, type) media_assets rows exist for the scanned posts, with
    # the storage columns the podcast integrity check (#1904) needs. Resolved via
    # BOTH the post_id and the task seam (``posts.metadata->>'pipeline_task_id'``)
    # so a freshly-rendered podcast row that is still task-keyed (post_id NULL,
    # not yet linked by ``podcast_distribute``) reads as present — otherwise the
    # watchdog would treat a pending render as missing and (pre-#1904) author a
    # Gate-2-bypassing duplicate. ``FROM media_assets`` stays the lead clause so
    # existing-pair membership semantics are unchanged for the video lane.
    _EXISTING_MEDIA_SQL = """
        SELECT p.id::text AS post_id,
               mas.type,
               mas.storage_provider,
               mas.storage_path,
               mas.url
          FROM media_assets mas
          JOIN posts p
            ON (mas.post_id = p.id
                OR (COALESCE(mas.task_id, '') <> ''
                    AND mas.task_id = p.metadata->>'pipeline_task_id'))
         WHERE p.id::text = ANY($1::text[])
    """

    # --- Podcast re-dispatch (#1904) ---------------------------------------
    # The gated ``podcast_pipeline`` is the SOLE podcast producer. On podcast
    # drift the watchdog re-dispatches it rather than authoring a podcast
    # directly (the old ``_regen_podcast`` path double-rendered AND bypassed
    # Gate-2 — the regenerate_stock_images class of bug). Resolve the source
    # task via the canonical ``posts.metadata->>'pipeline_task_id'`` seam, then
    # NULL its ``podcast_dispatched_at`` marker so ``dispatch_podcast_pipeline``
    # re-claims it and runs it through the full quality gates + Gate-2. Capped
    # per task by ``podcast_redispatch_count`` so a permanently-failing render
    # can't loop forever. Symmetric with the video re-dispatch below.
    _PODCAST_RESOLVE_TASK_SQL = """
        SELECT pt.task_id, pt.podcast_redispatch_count
          FROM pipeline_tasks pt
          JOIN posts p ON p.metadata->>'pipeline_task_id' = pt.task_id
         WHERE p.id = $1::uuid
         LIMIT 1
    """
    _PODCAST_CLEAR_MARKER_SQL = """
        UPDATE pipeline_tasks
           SET podcast_dispatched_at = NULL,
               podcast_redispatch_count = podcast_redispatch_count + 1
         WHERE task_id = $1
           AND podcast_redispatch_count < $2
    """

    async def _redispatch_podcast(self, pool: Any, post_row: dict[str, Any]) -> bool:
        """Re-run the gated ``podcast_pipeline`` for a drifted post by clearing
        its ``podcast_dispatched_at`` marker (capped via
        ``podcast_redispatch_count``). ``dispatch_podcast_pipeline`` re-claims
        the task next cycle and runs it through the full quality gates + Gate-2.
        Posts with no resolvable ``pipeline_task_id`` can't be re-dispatched —
        they only surface in the ``media_drift`` finding (fail-loud, not
        silently healed). The watchdog NEVER authors a podcast itself. Returns
        True iff the dispatch marker was cleared this call.
        """
        sc = getattr(self, "_site_config", None)
        cap = int(
            (sc.get("podcast_redispatch_max", "3") if sc is not None else "3")
            or 3
        )
        row = await pool.fetchrow(self._PODCAST_RESOLVE_TASK_SQL, post_row["id"])
        if not row or not row["task_id"]:
            logger.warning(
                "media_reconciliation: no pipeline_task_id for post %s — cannot "
                "re-dispatch podcast (surfaced in finding)", post_row["id"],
            )
            return False
        if row["podcast_redispatch_count"] >= cap:
            logger.warning(
                "media_reconciliation: post %s hit podcast re-dispatch cap (%d)",
                post_row["id"], cap,
            )
            return False
        result = await pool.execute(
            self._PODCAST_CLEAR_MARKER_SQL, row["task_id"], cap,
        )
        return str(result).strip().endswith(" 1")

    async def _redeliver_podcast(self, post_row: dict[str, Any]) -> bool:
        """Re-upload an already-delivered podcast whose R2 object vanished, from
        the durable local render — no re-render, no Gate-2 re-entry (the episode
        was already approved and shipped). The deterministic R2 key is unchanged,
        so the existing ``media_assets`` row stays valid; this only restores the
        object. Returns True iff the local render was found and re-uploaded;
        False (→ caller falls back to a gated re-dispatch) when there is no local
        file left to reuse.
        """
        sc = getattr(self, "_site_config", None)
        if sc is None:
            return False
        asset = post_row.get("podcast_asset") or {}
        storage_path = asset.get("storage_path") or ""
        if not storage_path or not os.path.exists(storage_path):
            return False  # nothing local to reuse → re-dispatch fallback
        post_id = post_row["id"]
        cdn_ver = sc.get("podcast_cdn_version", "v2") or "v2"
        key = f"podcast/{cdn_ver}/{post_id}.mp3"
        try:
            from services.r2_upload_service import R2UploadService

            r2 = R2UploadService(site_config=sc)
            url = await r2.upload_to_r2(storage_path, key, "audio/mpeg")
        except Exception as e:  # noqa: BLE001 — upload failure → re-dispatch fallback
            logger.warning(
                "media_reconciliation: podcast re-deliver upload failed for "
                "%s: %s", post_id, e,
            )
            return False
        if not url:
            return False
        logger.info(
            "media_reconciliation: re-delivered podcast for %s → %s (durable "
            "local render re-uploaded — no re-render)", post_id, url,
        )
        return True

    # --- Video re-dispatch (#1460) -----------------------------------------
    # The Stage-2 pipeline is the sole video producer. On video drift we
    # re-dispatch it rather than regenerating directly: resolve the source task
    # behind a post via the canonical posts.metadata->>'pipeline_task_id' seam,
    # then NULL its dispatch marker so dispatch_media_pipeline re-claims it.
    _RESOLVE_TASK_SQL = """
        SELECT pt.task_id, pt.media_pipeline_redispatch_count
          FROM pipeline_tasks pt
          JOIN posts p ON p.metadata->>'pipeline_task_id' = pt.task_id
         WHERE p.id = $1::uuid
         LIMIT 1
    """
    # Re-arm Stage-2 (NULL the marker) and bump the per-task counter, but only
    # while under the cap — so a permanently-failing render can't loop forever.
    _CLEAR_MARKER_SQL = """
        UPDATE pipeline_tasks
           SET media_pipeline_dispatched_at = NULL,
               media_pipeline_redispatch_count = media_pipeline_redispatch_count + 1
         WHERE task_id = $1
           AND media_pipeline_redispatch_count < $2
    """

    async def _redispatch_video(self, pool: Any, post_row: dict[str, Any]) -> bool:
        """Re-run Stage-2 for a drifted video post by clearing its dispatch
        marker (capped via ``media_pipeline_redispatch_count``).
        ``dispatch_media_pipeline`` re-claims the task next cycle. Posts with no
        resolvable ``pipeline_task_id`` can't be re-dispatched — they only
        surface in the ``media_drift`` finding (fail-loud, not silently healed).
        Returns True iff the dispatch marker was cleared this call.
        """
        sc = getattr(self, "_site_config", None)
        cap = int(
            (sc.get("media_pipeline_redispatch_max", "3") if sc is not None else "3")
            or 3
        )
        row = await pool.fetchrow(self._RESOLVE_TASK_SQL, post_row["id"])
        if not row or not row["task_id"]:
            logger.warning(
                "media_reconciliation: no pipeline_task_id for post %s — cannot "
                "re-dispatch video (surfaced in finding)", post_row["id"],
            )
            return False
        if row["media_pipeline_redispatch_count"] >= cap:
            logger.warning(
                "media_reconciliation: post %s hit video re-dispatch cap (%d)",
                post_row["id"], cap,
            )
            return False
        result = await pool.execute(self._CLEAR_MARKER_SQL, row["task_id"], cap)
        return str(result).strip().endswith(" 1")
