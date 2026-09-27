"""Upload approved long-form videos to the object store so the video feed's enclosures resolve.

The video RSS feed (``routes/video_routes.py::video_feed``) gives each approved,
published long-form video one ``<enclosure>``: ``media_assets.url`` when it is
stamped, else the deterministic key from
:func:`~poindexter.services.r2_upload_service.video_episode_key`
(``video/{post_id}.mp4``). Since the task-keyed cutover (#1460) nothing wrote
that key or stamped the URL. ``media.persist`` records the render as a local
file, and ``media_distribute`` uploads it to YouTube only. So every long-form
video rendered since then shipped a dead enclosure: 11 of 69 on 2026-09-25
(Glad-Labs/poindexter#1085). The podcast lane never had the gap, because
``podcast_distribute._deliver_podcast`` uploads the MP3 and stamps its URL.

:func:`run_video_r2_mirror` is the missing step. ``media_distribute`` runs it
every cycle, after its YouTube pass.

**Keyed on state, not on an event.** Candidates are "items the feed lists whose
asset has no stamped URL", selected with the feed's own gates. So an upload
that fails is retried next cycle, and so is one whose YouTube upload already
succeeded (the YouTube pass never revisits a dispatched row). The backlog heals
on the first cycle after deploy, and a delivery path added later can't strand a
video either.

Per candidate, the object store decides, via an authenticated HEAD
(:meth:`R2UploadService.object_size`, not the rate-limited public URL):

=====================  =================  =====================================
object at the key      local render       action
=====================  =================  =====================================
absent                 present            upload, stamp → ``uploaded``
same size as render    either             stamp only → ``stamped``
different size         present            upload over it, stamp → ``uploaded``
absent                 gone               mark ``source_missing`` → ``blocked``
different size         gone               mark ``mismatch`` → ``blocked``
=====================  =================  =====================================

"Stamp" sets ``url`` and ``storage_provider='cloudflare_r2'``. The URL is the
one the feed already advertised through its fallback, so the rendered feed
doesn't change: the dead link starts resolving where it is. An object of a
different size under a present render is overwritten because the approved
render is the one the operator reviewed at Gate 2.

**Blocked rows are recorded, not retried hot.** The mark lives at
``media_assets.metadata->'r2_mirror'`` and parks the row for
``video_r2_mirror_recheck_hours``. A render restored to its ``storage_path`` is
then picked up on the next re-check with no one clearing anything. A
``video_r2_mirror_blocked`` finding fires once per newly blocked set, not every
cycle.

**Not mirrored: ``video_short``.** Shorts go to YouTube Shorts and have no RSS
surface, so a copy in the bucket would have no reader.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from poindexter.services.r2_upload_service import (
    ObjectStoreUnavailable,
    R2UploadService,
    video_episode_key,
)
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

#: A render is gone and nothing is in the bucket under its key.
SOURCE_MISSING = "source_missing"
#: A render is gone and the key holds a file whose size doesn't match the
#: approved render's recorded size (in prod: a pre-cutover render of the post).
MISMATCH = "mismatch"

_BLOCKED_REASON_TEXT = {
    SOURCE_MISSING: "local render gone, nothing in the bucket",
    MISMATCH: "local render gone, the bucket holds a file of a different size",
}
# What a subscriber gets from the enclosure meanwhile.
_BLOCKED_EFFECT_TEXT = {
    SOURCE_MISSING: "its feed enclosure 404s",
    MISMATCH: "its feed enclosure serves that other file",
}

# One pass at a time, cluster-wide. The candidate SELECT runs under the lock,
# so an overlapping media_distribute fire (idempotent jobs run
# max_instances=3) skips the pass instead of uploading the same video twice.
# Two-arg int4 form; distinct from dispatch_handles._MEDIA_DISPATCH_LOCK_NS
# (0x4D44) and social_drafts._SOCIAL_POST_LOCK_NS (0x50AC).
_VIDEO_MIRROR_LOCK_NS: int = 0x5652  # "VR": video R2 mirror pass
_VIDEO_MIRROR_LOCK_KEY = "video_r2_mirror"

# Feed items whose enclosure has no stamped copy. The inner query is the video
# feed's own selection (published post, 'video' in its media policy, approved
# `video` medium, newest video asset per post) so the pass mirrors exactly what
# the feed advertises. The outer filter keeps unstamped rows, skips blocked rows
# until their re-check is due, and orders by newest approval so an old backlog
# can't starve a fresh one.
_CANDIDATES_SQL = """
    SELECT * FROM (
        SELECT DISTINCT ON (p.id)
               mas.id::text AS asset_id,
               p.id::text AS post_id,
               COALESCE(p.title, '') AS title,
               mas.storage_path,
               mas.file_size_bytes,
               mas.url,
               mas.metadata -> 'r2_mirror' AS mirror_state,
               COALESCE(ma.decided_at, ma.created_at) AS approved_at
          FROM posts p
          JOIN media_assets mas
            ON mas.post_id = p.id
           AND mas.type = 'video'
          JOIN media_approvals ma
            ON ma.post_id = p.id
           AND ma.medium = 'video'
           AND ma.status = 'approved'
         WHERE p.status = 'published'
           AND 'video' = ANY(p.media_to_generate)
         ORDER BY p.id, mas.created_at DESC NULLS LAST
    ) feed
     WHERE COALESCE(feed.url, '') = ''
       AND (feed.mirror_state IS NULL
            OR COALESCE((feed.mirror_state ->> 'checked_at')::timestamptz,
                        'epoch'::timestamptz)
               < NOW() - make_interval(hours => $1))
     ORDER BY feed.approved_at DESC NULLS LAST
     LIMIT $2
"""

# Record the delivered copy and clear any earlier blocked mark.
_STAMP_SQL = """
    UPDATE media_assets
       SET url = $2,
           storage_provider = 'cloudflare_r2',
           metadata = COALESCE(metadata, '{}'::jsonb) - 'r2_mirror',
           updated_at = NOW()
     WHERE id = $1::uuid
       AND type = 'video'
"""

# Park a row the pass could not deliver until its re-check is due.
_MARK_SQL = """
    UPDATE media_assets
       SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object(
               'r2_mirror', jsonb_build_object(
                   'status', $2::text,
                   'key', $3::text,
                   'object_bytes', $4::bigint,
                   'expected_bytes', $5::bigint,
                   'checked_at', NOW())),
           updated_at = NOW()
     WHERE id = $1::uuid
       AND type = 'video'
"""


@dataclass(frozen=True)
class MirrorOutcome:
    """What the pass did with one feed item.

    ``status`` is ``uploaded`` / ``stamped`` (delivered), ``blocked`` (marked,
    ``reason`` is :data:`SOURCE_MISSING` or :data:`MISMATCH`) or ``error``
    (nothing recorded, retried next cycle; ``reason`` says what failed).
    """

    post_id: str
    status: str
    reason: str = ""
    title: str = ""
    newly_blocked: bool = False


@dataclass
class MirrorPassResult:
    """One pass: per-item outcomes, plus why it didn't run or stopped early (``skipped``)."""

    outcomes: list[MirrorOutcome] = field(default_factory=list)
    skipped: str = ""

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    @property
    def delivered(self) -> int:
        """Items now in the bucket AND recorded on their row: uploaded this pass,
        or found already there and stamped (those enclosures resolved before;
        the row just didn't say so)."""
        return self.count("uploaded") + self.count("stamped")

    def summary(self) -> str:
        if self.skipped and not self.outcomes:
            return f"skipped ({self.skipped})"
        counts = (
            f"uploaded {self.count('uploaded')}, stamped {self.count('stamped')}, "
            f"blocked {self.count('blocked')}, errors {self.count('error')}"
        )
        return f"{counts}; stopped: {self.skipped}" if self.skipped else counts


def _local_bytes(path: str) -> int | None:
    """Size of the local render, or ``None`` when it is missing or empty.

    An empty file counts as missing: it can't be the render the operator
    approved, and publishing it would swap one dead enclosure for another.
    """
    if not path:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    return size if size > 0 else None


def _prior_status(mirror_state: Any) -> str:
    """The ``status`` of an existing ``r2_mirror`` mark, or ``""``.

    asyncpg returns ``jsonb`` as text here (no codec is registered), so both
    shapes are accepted.
    """
    if isinstance(mirror_state, str):
        try:
            mirror_state = json.loads(mirror_state)
        except ValueError:
            return ""
    if isinstance(mirror_state, dict):
        return str(mirror_state.get("status") or "")
    return ""


async def _stamp(pool: Any, row: dict[str, Any], url: str, status: str) -> MirrorOutcome:
    post_id = row["post_id"]
    try:
        result = await pool.execute(_STAMP_SQL, row["asset_id"], url)
    except Exception as exc:  # noqa: BLE001 — the object is up; next cycle re-stamps it
        logger.warning(
            "[VIDEO_R2_MIRROR] stamp failed for post %s (%s): %s — the object is "
            "in the bucket, the next pass stamps it",
            post_id, url, describe_exception(exc),
        )
        return MirrorOutcome(post_id, "error", reason=f"stamp: {describe_exception(exc)}")
    if not str(result).strip().endswith(" 1"):
        # The row was deleted or re-typed under us. Nothing to record.
        return MirrorOutcome(post_id, "error", reason=f"stamp matched no row ({result})")
    logger.info("[VIDEO_R2_MIRROR] %s post %s → %s", status, post_id, url)
    return MirrorOutcome(post_id, status, title=row.get("title") or "")


async def _mark(
    pool: Any,
    row: dict[str, Any],
    *,
    reason: str,
    key: str,
    object_bytes: int | None,
    expected_bytes: int | None,
) -> MirrorOutcome:
    post_id = row["post_id"]
    prior = _prior_status(row.get("mirror_state"))
    try:
        await pool.execute(
            _MARK_SQL, row["asset_id"], reason, key, object_bytes, expected_bytes,
        )
    except Exception as exc:  # noqa: BLE001 — unmarked rows are simply re-examined
        logger.warning(
            "[VIDEO_R2_MIRROR] could not mark post %s %s: %s",
            post_id, reason, describe_exception(exc),
        )
        return MirrorOutcome(post_id, "error", reason=f"mark: {describe_exception(exc)}")
    logger.warning(
        "[VIDEO_R2_MIRROR] post %s cannot be mirrored (%s) — %s until the "
        "render is restored, re-rendered, or the video is rejected",
        post_id, _BLOCKED_REASON_TEXT[reason], _BLOCKED_EFFECT_TEXT[reason],
    )
    return MirrorOutcome(
        post_id, "blocked", reason=reason, title=row.get("title") or "",
        newly_blocked=prior != reason,
    )


async def mirror_video_asset(
    pool: Any, r2: R2UploadService, row: dict[str, Any],
) -> MirrorOutcome:
    """Deliver one feed item's render to its key, or record why it can't be.

    ``row`` is a :data:`_CANDIDATES_SQL` row. Raises
    :class:`ObjectStoreUnavailable` when the store can't answer the HEAD; the
    pass stops there, since every other item would fail the same way.
    """
    post_id = row["post_id"]
    key = video_episode_key(post_id)
    path = row.get("storage_path") or ""
    local_bytes = _local_bytes(path)
    object_bytes = await r2.object_size(key)

    if object_bytes is not None:
        expected = local_bytes if local_bytes is not None else row.get("file_size_bytes")
        if expected and object_bytes == int(expected):
            # Already in the bucket: an earlier upload whose stamp didn't land,
            # or the same bytes from a pre-cutover producer. Record it, no upload.
            return await _stamp(pool, row, r2.object_url(key), "stamped")
        if local_bytes is None:
            return await _mark(
                pool, row, reason=MISMATCH, key=key,
                object_bytes=object_bytes,
                expected_bytes=int(expected) if expected else None,
            )
        logger.warning(
            "[VIDEO_R2_MIRROR] %s holds %d bytes but post %s's approved render "
            "is %d — replacing it with the approved render",
            key, object_bytes, post_id, local_bytes,
        )
    elif local_bytes is None:
        size = row.get("file_size_bytes")
        return await _mark(
            pool, row, reason=SOURCE_MISSING, key=key,
            object_bytes=None, expected_bytes=int(size) if size else None,
        )

    url = await r2.upload_to_r2(path, key, "video/mp4")
    if not url:
        # upload_to_r2 already logged why. Nothing recorded: next cycle retries.
        return MirrorOutcome(post_id, "error", reason="upload failed")
    return await _stamp(pool, row, url, "uploaded")


# Operator instructions for the blocked-rows finding. Plain text, never run:
# the SQL in it is for a human to paste.
_RECOVERY_STEPS = (
    "Pick one per video:\n"
    "1. **Restore the render** to the asset row's `storage_path` (the YouTube "
    "upload is the same approved render; YouTube Studio can download it). The "
    "mirror pass re-checks blocked rows every `video_r2_mirror_recheck_hours` "
    "and uploads it then. To re-check on the next cycle, clear the mark: "
    "`UPDATE media_assets SET metadata = metadata - 'r2_mirror' WHERE post_id "
    "= '<post_id>' AND type = 'video'`.\n"
    "2. **Take it out of the feed**: `poindexter media reject <post_id> "
    "video`. The YouTube upload is not touched.\n"
    "3. **Re-render** through Stage 2. The existing `video` row has to be "
    "deleted first (the one-video-per-post guard skips a second render), and "
    "the new render inherits the current approval unless you reset it to "
    "pending."
)


def _emit_blocked_finding(blocked: list[MirrorOutcome]) -> None:
    """One finding per newly blocked set. Re-checks that stay blocked are quiet."""
    ids = sorted(o.post_id for o in blocked)
    lines = "\n".join(
        f"- {o.post_id} — {o.title[:70] or '(untitled)'} "
        f"({_BLOCKED_REASON_TEXT.get(o.reason, o.reason)})"
        for o in blocked[:25]
    )
    more = f"\n- …and {len(blocked) - 25} more" if len(blocked) > 25 else ""
    emit_finding(
        source="video_r2_mirror",
        kind="video_r2_mirror_blocked",
        severity="warn",
        title=(
            f"{len(blocked)} approved video(s) can't be put in the bucket — "
            f"their RSS enclosures are broken"
        ),
        body=(
            "These videos are approved and listed in the video RSS feed, but "
            "their enclosure (`video/{post_id}.mp4`) can't be delivered: the "
            "local render is gone, and the bucket holds either nothing (the "
            "enclosure 404s) or a file whose size doesn't match the approved "
            "render (the enclosure serves that file instead).\n\n"
            f"{lines}{more}\n\n{_RECOVERY_STEPS}"
        ),
        dedup_key="video_r2_mirror_blocked:" + hashlib.sha256(
            ",".join(ids).encode("utf-8"),
        ).hexdigest()[:16],
        extra={
            "post_ids": ids,
            "reasons": {o.post_id: o.reason for o in blocked},
        },
    )


async def run_video_r2_mirror(
    pool: Any, site_config: Any, *, limit: int,
) -> MirrorPassResult:
    """Mirror up to ``limit`` unstamped feed items. Never raises."""
    if not site_config.get_bool("video_r2_mirror_enabled", True):
        return MirrorPassResult(skipped="video_r2_mirror_enabled=false")
    # The feed needs storage_public_url to render an enclosure at all
    # (video_routes._r2_url returns 503 without it), so without it no
    # mirrored object would have a reader.
    if not (site_config.get("storage_public_url", "") or "").strip():
        return MirrorPassResult(skipped="storage_public_url unset — no video feed")

    recheck_hours = max(1, site_config.get_int("video_r2_mirror_recheck_hours", 24))
    r2 = R2UploadService(site_config=site_config)
    result = MirrorPassResult()

    try:
        async with pool.acquire() as lock_conn:
            got = await lock_conn.fetchval(
                "SELECT pg_try_advisory_lock($1, hashtext($2))",
                _VIDEO_MIRROR_LOCK_NS, _VIDEO_MIRROR_LOCK_KEY,
            )
            # Only a literal False is contention; a mock pool answers None.
            if got is False:
                return MirrorPassResult(skipped="another mirror pass is running")
            try:
                await _mirror_candidates(pool, r2, result, recheck_hours, limit)
            finally:
                try:
                    await lock_conn.fetchval(
                        "SELECT pg_advisory_unlock($1, hashtext($2))",
                        _VIDEO_MIRROR_LOCK_NS, _VIDEO_MIRROR_LOCK_KEY,
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "[VIDEO_R2_MIRROR] advisory unlock failed (the lock "
                        "frees when the connection drops)", exc_info=True,
                    )
    except Exception as exc:  # noqa: BLE001 — one pass must never fail media_distribute
        logger.warning(
            "[VIDEO_R2_MIRROR] pass failed: %s", describe_exception(exc),
        )
        result.skipped = result.skipped or f"pass failed: {describe_exception(exc)}"

    newly_blocked = [o for o in result.outcomes if o.newly_blocked]
    if newly_blocked:
        _emit_blocked_finding(newly_blocked)
    return result


async def _mirror_candidates(
    pool: Any,
    r2: R2UploadService,
    result: MirrorPassResult,
    recheck_hours: int,
    limit: int,
) -> None:
    rows = await pool.fetch(_CANDIDATES_SQL, recheck_hours, limit)
    for raw in rows or []:
        row = dict(raw)
        try:
            outcome = await mirror_video_asset(pool, r2, row)
        except ObjectStoreUnavailable as exc:
            # Every remaining item would fail the same HEAD. Stop; next cycle retries.
            logger.warning(
                "[VIDEO_R2_MIRROR] object store unavailable, stopping this pass: %s",
                exc,
            )
            result.skipped = f"object store unavailable: {exc}"
            return
        except Exception as exc:  # noqa: BLE001 — one item must not halt the pass
            logger.warning(
                "[VIDEO_R2_MIRROR] post %s raised: %s",
                row.get("post_id"), describe_exception(exc),
            )
            outcome = MirrorOutcome(
                str(row.get("post_id")), "error", reason=describe_exception(exc),
            )
        result.outcomes.append(outcome)


__all__ = [
    "MISMATCH",
    "SOURCE_MISSING",
    "MirrorOutcome",
    "MirrorPassResult",
    "mirror_video_asset",
    "run_video_r2_mirror",
]
