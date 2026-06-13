"""
Scheduled Post Publisher

Background coroutine that publishes posts whose scheduled publication
time has arrived. Runs every 60 seconds.

Glad-Labs/poindexter#327: every promotion to ``status='published''``
now triggers ISR revalidation via the shared
``services.revalidation_service.trigger_isr_revalidate`` helper.
Previously this loop only flipped the row in postgres and relied on
the next ISR window (5 min) to surface the post on Vercel — which
left a window where new posts existed in the cloud DB but did not
appear on www.gladlabs.io.
"""

import asyncio

from services.logger_config import get_logger
from services.site_config import SiteConfig

logger = get_logger(__name__)


async def run_scheduled_publisher(get_pool, *, site_config: SiteConfig):
    """
    Background loop that checks for posts with status='scheduled'
    and published_at <= NOW(), then publishes them.

    Args:
        get_pool: Callable that returns the asyncpg connection pool
        site_config: SiteConfig (Phase H DI seam — GH#95). Used to read
            ``scheduled_publisher_poll_seconds`` and threaded down to
            ``_revalidate_for_row``. Phase-2 DI (#272): now required — the
            module global + ``set_site_config`` shim was retired.
    """
    _sc = site_config

    # Poll interval tunable via app_settings.scheduled_publisher_poll_seconds (#198)
    try:
        # app_settings values are strings, so the fallback is a string too —
        # int() coerces either form to 60.
        _poll_interval = int(
            _sc.get("scheduled_publisher_poll_seconds", "60")
        )
    except Exception as e:
        _poll_interval = 60
        # Don't let a typo'd setting (e.g. "" / "sixty") silently revert to
        # the default — the operator set a value expecting it to take effect.
        # Not fail-loud (the loop works fine on 60), just visible.
        logger.warning(
            "[scheduled_publisher] scheduled_publisher_poll_seconds is not a "
            "valid integer (%s); falling back to %ds. Set a numeric value to "
            "silence this.",
            e,
            _poll_interval,
        )
    logger.info(
        "[scheduled_publisher] Started (poll interval: %ds)", _poll_interval
    )
    first_run = True
    while True:
        try:
            if first_run:
                first_run = False
            else:
                await asyncio.sleep(_poll_interval)
            pool = await get_pool()
            if not pool:
                continue

            async with pool.acquire() as conn:
                async with conn.transaction():
                    # #327: pull the slug back too so we can revalidate
                    # the post-specific path. Previously only id/title
                    # were returned and the loop never triggered ISR
                    # busting, so promoted posts sat invisible for ≤5
                    # min. `distributed_at` gates both the RSS feed
                    # (app/feed.xml/route.ts) and the static R2 index
                    # export (static_export_service.export_posts_index).
                    # Posts promoted via this loop sat invisible from
                    # both surfaces because the original UPDATE only
                    # flipped status — see RSS staleness + missing-
                    # from-/posts bug, 2026-05-01. COALESCE preserves
                    # any pre-set value (re-promotion edge case).
                    #
                    # 2026-05-28: also RETURN metadata->>'pipeline_task_id'
                    # so we can sync the linked pipeline_tasks row in
                    # the same transaction. Per
                    # ``feedback_filter_on_seams_not_slugs``, the
                    # ``posts.metadata->>'pipeline_task_id'`` JSONB key
                    # is the canonical seam back to the source task —
                    # populated at insert by publish_service and
                    # backfilled for historical rows by migration
                    # 20260528_021920. Before this change, promoted
                    # posts left their pipeline_tasks row stuck at
                    # 'approved' forever, making `poindexter tasks list`
                    # lie to the operator.
                    rows = await conn.fetch("""
                        UPDATE posts
                        SET status = 'published',
                            updated_at = NOW(),
                            distributed_at = COALESCE(distributed_at, NOW())
                        WHERE status = 'scheduled' AND published_at <= NOW()
                        RETURNING id, title, slug,
                                  metadata ->> 'pipeline_task_id' AS pipeline_task_id
                        """)
                    if rows:
                        # Sync each promoted post's linked pipeline_tasks
                        # row to status='published'. Issued as a single
                        # batch UPDATE inside the same transaction so
                        # the two tables move together. Tasks missing
                        # the seam (NULL pipeline_task_id) get warned
                        # but don't crash the loop — Layer 1 + Layer 2
                        # should cover everything, so a NULL here
                        # indicates a publish path that slipped the
                        # stamp and deserves operator attention.
                        task_ids_to_sync: list[str] = []
                        for row in rows:
                            # asyncpg.Record supports __getitem__ only;
                            # wrap in a guarded fetch so dict-backed
                            # mocks without the field don't crash the
                            # loop and a real Record without the alias
                            # (shouldn't happen — the UPDATE...RETURNING
                            # aliases the JSONB extraction) degrades to
                            # a warning + skip.
                            try:
                                task_id = row["pipeline_task_id"]
                            except (KeyError, IndexError):
                                task_id = None
                            try:
                                row_slug = row["slug"]
                            except (KeyError, IndexError):
                                row_slug = "?"
                            if task_id:
                                task_ids_to_sync.append(task_id)
                            else:
                                logger.warning(
                                    "[scheduled_publisher] Promoted post %s "
                                    "(%s) has NULL metadata.pipeline_task_id — "
                                    "pipeline_tasks status not synced. Some "
                                    "publish path is skipping the seam stamp; "
                                    "operator should investigate.",
                                    row["id"],
                                    row_slug,
                                )
                        if task_ids_to_sync:
                            sync_result = await conn.execute(
                                """
                                UPDATE pipeline_tasks
                                   SET status = 'published',
                                       updated_at = NOW()
                                 WHERE task_id = ANY($1::text[])
                                   AND status IN ('approved', 'scheduled')
                                """,
                                task_ids_to_sync,
                            )
                            logger.info(
                                "[scheduled_publisher] Synced pipeline_tasks "
                                "to published for %d task(s): %s",
                                len(task_ids_to_sync), sync_result,
                            )
                if rows:
                    for row in rows:
                        logger.info(
                            "[scheduled_publisher] Published scheduled post: %s (%s)",
                            row["title"],
                            row["id"],
                        )
                        # Glad-Labs/poindexter#327: every promotion must
                        # bust the Vercel ISR cache, otherwise the post
                        # won't appear on www.gladlabs.io until the next
                        # 5-minute window.
                        await _revalidate_for_row(row, site_config=_sc)
        except asyncio.CancelledError:
            logger.info("[scheduled_publisher] Shutting down")
            break
        except Exception as e:
            logger.error("[scheduled_publisher] Error: %s", e, exc_info=True)


async def _revalidate_for_row(row, *, site_config: SiteConfig) -> None:
    """Trigger ISR revalidation for a freshly-promoted scheduled post.

    Pulled out as a helper so the main loop body stays readable and
    tests can patch a single symbol.

    Never raises — revalidation failure must not poison the loop or
    block subsequent rows in the same batch.

    ``site_config`` is threaded in by ``run_scheduled_publisher`` (Phase-2
    DI, #272 — now required; the module global + ``set_site_config`` shim
    was retired).
    """
    _sc = site_config
    try:
        slug = row["slug"]
    except (KeyError, TypeError):
        slug = None
    if not slug:
        try:
            row_id = row["id"]
        except (KeyError, TypeError):
            row_id = "?"
        logger.warning(
            "[scheduled_publisher] Skipping revalidation — no slug on row %s",
            row_id,
        )
        return
    try:
        # SiteConfig DI migration (#272 leaf batch 3): the revalidation
        # helpers now require an explicit site_config. Pass this module's
        # lifespan-bound instance (caller-bridge).
        from services.revalidation_service import trigger_isr_revalidate
        ok = await trigger_isr_revalidate(slug, site_config=_sc)
        if ok:
            logger.info(
                "[scheduled_publisher] ISR revalidation triggered for %s",
                slug,
            )
        else:
            logger.warning(
                "[scheduled_publisher] ISR revalidation returned failure for %s",
                slug,
            )
    except Exception as reval_err:
        logger.warning(
            "[scheduled_publisher] Revalidation raised for %s (non-fatal): %s",
            slug, reval_err,
        )
