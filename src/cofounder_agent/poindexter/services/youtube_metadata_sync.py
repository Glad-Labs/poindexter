"""Re-push YouTube title/description/tags for videos already on the channel.

The upload path composes metadata once, at upload time, and never revisits it.
So when the composition rules change, every video already on the channel keeps
whatever the old rules produced — which is exactly what happened on 2026-08-31:
the description builder stopped dumping the whole article body, and the 12
videos uploaded before that kept their 4,800-character markdown wall.

This module closes that gap. It recomposes metadata from the post row using
**the same builders the upload path uses** (``services.jobs.youtube_payload``),
so a future change to the composition rules reaches new and old videos through
one code path rather than two that can disagree.

It also reconciles in one direction the upload path never did: a video the API
reports is not on the channel gets its ``pipeline_distributions`` row demoted to
``status='deleted'``. Nothing else ever revisits that row, so a deleted upload
otherwise claimed to be published forever — inflating the published count and
failing every subsequent ``--apply``.

Scope note: ``videos.update`` needs the ``youtube.force-ssl`` scope, and the
original consent was ``youtube.upload`` (insert-only). Until the operator
re-consents via ``poindexter integrations youtube setup --with-update`` every
call here fails with that remediation — deliberately, and loudly, rather than
appearing to work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from poindexter.services.jobs.youtube_payload import (
    _build_youtube_description,
    _build_youtube_title,
    _parse_seo_keywords,
    hashtags_for_short,
    short_hook_line,
    short_hook_title,
    twin_watch_url,
)
from poindexter.services.publish_adapters.youtube import STATUS_NOT_FOUND
from poindexter.services.short_hook import (
    content_defects,
    first_sentence,
    hook_limits,
    runaway_factor,
    strip_preamble,
)
from poindexter.utils.exception_format import describe_exception
from poindexter.utils.findings import emit_finding

logger = logging.getLogger(__name__)

# Videos we uploaded, joined to the post they promote. pipeline_distributions
# is the durable record of what actually landed on the platform (external_id =
# the YouTube video id), and media_assets.platform_video_ids carries the same
# handle — the join keys off the former because it is the row the dispatcher
# writes transactionally with the upload.
#
# ``pd.medium`` says which render each row is. Without it a re-sync would
# rebuild every title as long-form and strip the Short's distinguishing suffix
# back off, re-colliding the pair it was added to separate. It used to be
# recovered by a correlated subquery into media_assets because the column did
# not exist; that column is now the dispatcher's own record of what it sent, so
# there is one source rather than two that can disagree.
#
# ``status = 'published'`` is also the deleted-upload filter: a video the API
# reports as gone is demoted to 'deleted' (see _mark_distribution_deleted), so
# it drops out of this set instead of failing every run forever.
_TARGETS_SQL = """
    SELECT pd.external_id AS video_id,
           p.id::text     AS post_id,
           p.title,
           p.excerpt,
           p.content,
           p.seo_keywords,
           p.slug,
           pd.medium,
           pd.task_id,
           -- The Short's own narration (its title + description hook, 2026-09-22).
           pv.stage_data -> 'task_metadata' ->> 'short_summary_script' AS short_script,
           -- The pair's other render, only while it is LIVE: the cross-link is
           -- composed from this and recomposed when the twin lands or vanishes.
           twin.external_id AS twin_video_id,
           twin.medium      AS twin_medium
      FROM pipeline_distributions pd
      JOIN posts p ON p.id = pd.post_id
      LEFT JOIN LATERAL (
           SELECT v.stage_data FROM pipeline_versions v
            WHERE v.task_id = pd.task_id
            ORDER BY v.version DESC LIMIT 1
      ) pv ON TRUE
      LEFT JOIN LATERAL (
           SELECT t.external_id, t.medium FROM pipeline_distributions t
            WHERE t.task_id = pd.task_id
              AND t.target = 'youtube'
              AND t.status = 'published'
              AND t.external_id IS NOT NULL
              AND t.medium IN ('video', 'video_short')
              AND t.medium <> pd.medium
            ORDER BY t.published_at DESC NULLS LAST LIMIT 1
      ) twin ON TRUE
     WHERE pd.target = 'youtube'
       AND pd.status = 'published'
       AND pd.external_id IS NOT NULL
       AND ($1::text IS NULL OR p.id::text = $1 OR pd.external_id = $1)
     ORDER BY pd.created_at DESC
"""

# Completeness cross-check. media_assets.platform_video_ids is the OTHER record
# of what landed on YouTube — one handle per asset row, so it structurally cannot
# collide the way the distribution rows did. Anything it holds that this table
# does not is a video the sync cannot see.
#
# This should now be permanently empty: media_distribute writes both records in
# one transaction, and the key admits every render. But "should be impossible" is
# exactly what was believed about the old key, and the failure mode is silent —
# the sync covers N-1 videos and reports success. So the sync asks, every run.
_ORPHAN_HANDLES_SQL = """
    SELECT ma.platform_video_ids->>'youtube' AS video_id,
           ma.type   AS medium,
           ma.task_id,
           ma.post_id::text AS post_id
      FROM media_assets ma
     WHERE COALESCE(ma.platform_video_ids->>'youtube', '') <> ''
       AND NOT EXISTS (
             SELECT 1 FROM pipeline_distributions pd
              WHERE pd.target = 'youtube'
                AND pd.external_id = ma.platform_video_ids->>'youtube'
           )
     ORDER BY ma.created_at DESC
"""

# Demote an upload the platform says is gone. Keyed on the handle, not the task,
# so only the render that actually vanished is demoted — its twin on the same
# task stays published.
_MARK_DELETED_SQL = """
    UPDATE pipeline_distributions
       SET status = 'deleted',
           error_message = $2
     WHERE target = 'youtube'
       AND external_id = $1
       AND status <> 'deleted'
"""


@dataclass(frozen=True)
class SyncOutcome:
    """One video's result. ``applied`` is False for a dry run or a failure.

    ``reconciled_deleted`` marks the one failure that is not a failure of this
    run: YouTube said the video is not on the channel, so the distribution row
    was demoted to ``status='deleted'`` and will not be offered again. The
    caller should report it separately from a genuine error — nothing is left
    for an operator to fix.
    """

    video_id: str
    post_id: str
    title: str
    applied: bool
    error: str | None = None
    description_chars: int = 0
    tag_count: int = 0
    reconciled_deleted: bool = False
    #: CONTENT defects on the stored hook — the ones a shorten cannot fix.
    #: Populated on every run so a DRY run still names what --apply would
    #: repair, without spending a GPU call to find out.
    hook_defects: tuple[str, ...] = ()
    #: True when the corrective call ran, improved the hook, and the repaired
    #: script was written back to the task.
    hook_repaired: bool = False


async def _load_targets(pool: Any, selector: str | None) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(_TARGETS_SQL, selector)
    return [dict(r) for r in rows]


async def find_unrecorded_uploads(pool: Any) -> list[dict[str, Any]]:
    """YouTube handles in ``media_assets`` with no ``pipeline_distributions`` row.

    The completeness half of the sync's answer to "which videos exist?".
    ``pipeline_distributions`` stays the source it *reads* — it is the row the
    dispatcher writes transactionally with the upload, and the only one carrying
    a ``status``, so it is the only place a deleted upload can be recorded. But
    it is also the one that lost data: keyed ``(task_id, target)`` it could hold
    just one of a post's two renders, and five Shorts fell out of it without a
    single error anywhere.

    ``media_assets.platform_video_ids`` is the same fact stored per asset row,
    which is why it survived. Reading BOTH — one to act on, one to check the
    first is complete — costs one query and turns a silent subset back into a
    visible discrepancy.
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_ORPHAN_HANDLES_SQL)
    except Exception as exc:  # noqa: BLE001 — a cross-check must not break the sync
        logger.warning("[YOUTUBE_SYNC] orphan-handle check failed: %s", exc)
        return []
    return [dict(r) for r in rows]


async def _mark_distribution_deleted(pool: Any, video_id: str, reason: str) -> bool:
    """Demote a distribution row whose video is no longer on the channel.

    The upload path records ``status='published'`` and nothing ever revisited
    it, so a video deleted from the channel left a row claiming it was live
    forever: it inflated the published count, and every ``--apply`` re-attempted
    it and failed. Two of the twelve rows on prod were in that state, both
    uploaded 2026-06-15 and both returning oembed 404.

    The API's "not found" is the authority here — an oembed 404 could equally be
    a private video or a transient edge — which is why this fires only from the
    apply path, where a real ``videos.list`` has just come back empty. Returns
    whether a row was demoted so the caller can report it as reconciled rather
    than as a failure to retry.
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(_MARK_DELETED_SQL, video_id, reason[:2000])
    except Exception as exc:  # noqa: BLE001 — the sync must not die on bookkeeping
        logger.warning(
            "[YOUTUBE_SYNC] could not demote vanished video %s: %s", video_id, exc,
        )
        return False

    logger.warning(
        "[YOUTUBE_SYNC] %s is gone from the channel — distribution demoted to "
        "status='deleted'", video_id,
    )
    emit_finding(
        source="youtube_metadata_sync",
        kind="youtube_upload_vanished",
        severity="warn",
        title=f"YouTube video {video_id} is no longer on the channel",
        body=(
            f"pipeline_distributions claimed `{video_id}` was published, but the "
            f"YouTube API reports it is not on this channel. The row has been "
            f"demoted to `status='deleted'` so it stops being retried by "
            f"`poindexter integrations youtube sync-metadata --apply` and stops "
            f"counting as a published upload.\n\nAPI said: {reason}"
        ),
        dedup_key=f"youtube_upload_vanished:{video_id}",
        extra={"video_id": video_id},
    )
    return True


#: Persist a repaired narration back onto the task's latest version row, so
#: the fix outlives this sync instead of being recomputed (and re-paid for)
#: on every run. Scoped to the row the read came from —
#: ``_TARGETS_SQL`` joins the same ``ORDER BY version DESC LIMIT 1``.
_PERSIST_SCRIPT_SQL = """
    UPDATE pipeline_versions
       SET stage_data = jsonb_set(
               stage_data, '{task_metadata,short_summary_script}',
               to_jsonb($2::text), true)
     WHERE task_id = $1
       AND version = (SELECT MAX(version) FROM pipeline_versions WHERE task_id = $1)
       AND stage_data -> 'task_metadata' ->> 'short_summary_script' IS NOT NULL
"""


def stored_hook_defects(row: dict[str, Any], site_config: Any) -> tuple[str, ...]:
    """CONTENT defects on this row's stored hook; ``()`` for a long-form row.

    Length is NOT a defect here — the title builder shortens at a word
    boundary and that is the designed behaviour. Only the defects a shorten
    cannot fix (a question, a run-on, a restatement of the title) are worth a
    corrective call.
    """
    if row.get("medium") != "video_short":
        return ()
    script = str(row.get("short_script") or "")
    if not script.strip():
        return ()
    max_chars, max_words = hook_limits(site_config)
    hook = strip_preamble(first_sentence(script)).rstrip(".").strip()
    return content_defects(
        hook,
        max_chars=max_chars,
        max_words=max_words,
        article_title=str(row.get("title") or ""),
        runaway_factor=runaway_factor(site_config),
    )


async def _repair_stored_hook(
    pool: Any, row: dict[str, Any], site_config: Any, repair_hook: Any,
) -> bool:
    """Regenerate this Short's opening line and persist it. ``True`` if it changed.

    The gate that writes a good hook runs at SCRIPT-generation time, so a
    Short rendered before it existed keeps whatever the model first wrote —
    and this path can only SHORTEN, which turns an unfinished sentence into a
    stump. Measured 2026-09-23, that was 4 of the 9 live Shorts.

    ``repair_hook`` is INJECTED rather than imported: the repair lives in
    ``modules.content`` and this is kernel, so importing it here would put a
    module dependency in ``services/`` (``scripts/ci/kernel_purity_lint.py``,
    poindexter#666). The CLI owns the wiring and passes the callable in.

    Never raises: the metadata sync's job is the metadata, and a Short with
    its original opener is the status quo, not a regression.
    """
    script = str(row.get("short_script") or "")
    try:
        repaired, outcome = await repair_hook(
            script,
            title=str(row.get("title") or ""),
            article=str(row.get("content") or ""),
            site_config=site_config,
            pool=pool,
            task_id=row.get("task_id"),
        )
    except Exception as exc:  # noqa: BLE001 — a hook is never worth failing a sync over
        logger.warning(
            "[YOUTUBE_SYNC] hook repair raised for %s (%s) — keeping the original",
            row.get("video_id"), describe_exception(exc),
        )
        return False

    if not outcome.get("repaired") or repaired == script:
        logger.info(
            "[YOUTUBE_SYNC] hook NOT repaired for %s (%s); keeping the original",
            row.get("video_id"), outcome.get("skipped") or outcome.get("error") or "no improvement",
        )
        return False

    try:
        async with pool.acquire() as conn:
            await conn.execute(_PERSIST_SCRIPT_SQL, row.get("task_id"), repaired)
    except Exception as exc:  # noqa: BLE001 — the better hook still ships on this run
        logger.warning(
            "[YOUTUBE_SYNC] repaired hook for %s could not be persisted (%s) — "
            "this run uses it, the next will recompute it",
            row.get("video_id"), describe_exception(exc),
        )

    row["short_script"] = repaired
    logger.info(
        "[YOUTUBE_SYNC] repaired the hook for %s: %r -> %r",
        row.get("video_id"), first_sentence(script), first_sentence(repaired),
    )
    return True


def _compose(row: dict[str, Any], site_config: Any) -> tuple[str, str, list[str]]:
    """Recompose (title, description, tags) exactly as an upload would.

    Same builders, same inputs as ``media_distribute``: the Short's hook from
    its narration, the twin's live URL for the cross-link (``twin_video_id``
    is NULL when the other render is not published — the line is then
    omitted, so a re-sync after a twin vanishes drops the dead link).
    """
    shorts = row.get("medium") == "video_short"
    script = str(row.get("short_script") or "")
    tags = _parse_seo_keywords(row.get("seo_keywords") or "")
    twin_url = twin_watch_url(str(row.get("twin_video_id") or ""), twin_is_short=not shorts)
    description = _build_youtube_description(
        seo_description=row.get("excerpt") or "",
        body=row.get("content") or "",
        site_config=site_config,
        slug=row.get("slug") or "",
        shorts=shorts,
        hook=short_hook_line(script) if shorts else "",
        twin_url=twin_url,
        hashtags=hashtags_for_short(tags, site_config=site_config) if shorts else None,
    )
    title = _build_youtube_title(
        row.get("title") or "",
        shorts=shorts,
        site_config=site_config,
        hook=short_hook_title(script, site_config=site_config) if shorts else "",
    )
    return (title, description, tags)


async def sync_youtube_metadata(
    pool: Any,
    site_config: Any,
    *,
    selector: str | None = None,
    apply: bool = False,
    limit: int | None = None,
    repair_hook: Any = None,
) -> list[SyncOutcome]:
    """Recompose and (optionally) push metadata for published YouTube videos.

    ``selector`` narrows to one post id or one video id; ``None`` means every
    published upload. ``apply`` defaults to **False** — this writes to a public
    platform, so the caller has to say so explicitly and a mistake costs a
    printed diff rather than 12 rewritten videos.

    ``repair_hook`` opts the run into repairing a Short whose stored hook has
    a CONTENT defect — one local LLM call, and the improved narration is
    written back to the task so it is paid for once. It is injected rather
    than imported because the repair lives in ``modules.content`` and this is
    kernel; the CLI owns that wiring. Omit it (the default) and the run
    composes from whatever is stored, which is the pre-2026-09-23 behaviour.
    A DRY run never repairs and never writes; it still reports the defects on
    :attr:`SyncOutcome.hook_defects`, so the operator can see what ``--apply``
    would fix without spending a GPU call to find out.

    Returns one :class:`SyncOutcome` per video, failures included: a partial
    result is the useful one when a scope error stops the run at the first
    video, and swallowing that would look like "nothing needed changing".
    """
    rows = await _load_targets(pool, selector)
    if limit is not None:
        rows = rows[: max(0, limit)]
    if not rows:
        return []

    adapter = None
    if apply:
        from poindexter.services.publish_adapters.youtube import YouTubePublishAdapter

        adapter = YouTubePublishAdapter(site_config=site_config)

    outcomes: list[SyncOutcome] = []
    for row in rows:
        # Computed BEFORE any repair, so it reports what was stored.
        defects = stored_hook_defects(row, site_config)
        repaired = False
        if defects and apply and repair_hook is not None:
            repaired = await _repair_stored_hook(pool, row, site_config, repair_hook)

        title, description, tags = _compose(row, site_config)
        if not apply:
            outcomes.append(
                SyncOutcome(
                    video_id=row["video_id"],
                    post_id=row["post_id"],
                    title=title,
                    applied=False,
                    description_chars=len(description),
                    tag_count=len(tags),
                    hook_defects=defects,
                )
            )
            continue

        result = await adapter.update_metadata(  # type: ignore[union-attr]
            video_id=row["video_id"],
            title=title,
            description=description,
            tags=tags or None,
        )
        ok = bool(getattr(result, "success", False))
        reason = str(getattr(result, "error", None) or "unknown error")
        error = None if ok else reason
        vanished = False
        if getattr(result, "status", "") == STATUS_NOT_FOUND:
            vanished = await _mark_distribution_deleted(pool, row["video_id"], reason)
        outcomes.append(
            SyncOutcome(
                video_id=row["video_id"],
                post_id=row["post_id"],
                title=title,
                applied=ok,
                error=error,
                description_chars=len(description),
                tag_count=len(tags),
                reconciled_deleted=vanished,
                hook_defects=defects,
                hook_repaired=repaired,
            )
        )
        if ok:
            logger.info(
                "[YOUTUBE_SYNC] updated %s (post %s): description %d chars, %d tags",
                row["video_id"], row["post_id"][:8], len(description), len(tags),
            )
        elif not vanished:
            logger.warning(
                "[YOUTUBE_SYNC] update failed for %s: %s",
                row["video_id"], outcomes[-1].error,
            )

    return outcomes


__all__ = [
    "SyncOutcome",
    "find_unrecorded_uploads",
    "stored_hook_defects",
    "sync_youtube_metadata",
]
