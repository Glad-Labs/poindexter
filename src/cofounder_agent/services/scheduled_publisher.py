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

            # HITL final-publish gate: when enabled, park due-but-unparked
            # scheduled posts at final_publish_approval instead of letting the
            # promote UPDATE publish them. Runs on its own connection BEFORE the
            # promote transaction (pause_post_at_gate acquires its own). Never
            # raises — a gate/notify failure must not poison the publish loop.
            await _maybe_park_due_posts_at_gate(pool, site_config=_sc)

            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Fire-time publish gate for veto-window auto-scheduled
                    # posts — runs FIRST so a vetoed row is demoted before
                    # the promote UPDATE below can see it (same transaction,
                    # sequential). See _demote_vetoed_auto_posts.
                    await _demote_vetoed_auto_posts(conn)
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
                          AND awaiting_gate IS NULL
                        RETURNING id, title, slug, excerpt,
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
                                   AND status = 'approved'
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
                        # Newsletter announce — this promote IS the
                        # go-live moment for scheduled posts, and until
                        # 2026-07-10 it never emailed subscribers (the
                        # only newsletter hook lived on publish_service's
                        # immediate-publish tail). Guarded like the
                        # pipeline_task_id fetch above so dict-backed
                        # mocks without the columns don't crash the loop;
                        # send_post_newsletter dedups per (slug,
                        # subscriber) so a re-promotion can't double-mail.
                        try:
                            _nl_slug = row["slug"]
                        except (KeyError, IndexError):
                            _nl_slug = None
                        try:
                            _nl_excerpt = row["excerpt"] or ""
                        except (KeyError, IndexError):
                            _nl_excerpt = ""
                        if _nl_slug:
                            try:
                                from services.publish_service import (
                                    _send_post_newsletter_bg,
                                    _spawn_background,
                                )
                                _spawn_background(
                                    _send_post_newsletter_bg(
                                        pool,
                                        _sc,
                                        row["title"] or _nl_slug,
                                        _nl_excerpt,
                                        _nl_slug,
                                    ),
                                    name=f"send_newsletter({_nl_slug})",
                                )
                            except Exception as nl_exc:  # noqa: BLE001
                                logger.warning(
                                    "[scheduled_publisher] newsletter spawn "
                                    "failed (non-fatal): %s",
                                    nl_exc,
                                )
        except asyncio.CancelledError:
            logger.info("[scheduled_publisher] Shutting down")
            break
        except Exception as e:
            logger.error("[scheduled_publisher] Error: %s", e, exc_info=True)


async def _demote_vetoed_auto_posts(conn) -> None:
    """Fire-time publish gate for veto-window auto-scheduled posts.

    A post the auto-publish gate staged with a veto window
    (``metadata.auto_publish_veto_window='true'``, set by
    ``modules.content.auto_publish._stage_with_veto_window``) must re-earn its
    promotion at fire time: if the source ``pipeline_tasks`` row has left the
    approved family — the operator vetoed via ``poindexter auto-publish veto``
    after a partial state, or rejected the task from ANY surface (CLI / MCP /
    Telegram) — the row is demoted back to ``status='approved'`` +
    ``published_at=NULL`` instead of publishing. Same pattern as the social
    scheduler, whose fire path re-runs ``approve_draft``'s publish gate so a
    slipped post can't promote itself at a 404.

    Scoped STRICTLY to rows carrying the veto-window marker — the operator's
    hand-picked slots (console slot picker / ``schedule batch``) are never
    touched, whatever their task status. Also deletes the stage-time
    ``published_post_edit_metrics`` row (``approver='auto_publish'`` only) so
    a vetoed run never counts as a clean run in the niche's trailing
    auto-publish window.

    Runs inside the promote transaction, before the promote UPDATE, on the
    caller's connection — sequential, so a demoted row is invisible to the
    promote in the same cycle.
    """
    rows = await conn.fetch(
        """
        UPDATE posts p
           SET status = 'approved', published_at = NULL, updated_at = NOW()
         WHERE p.status = 'scheduled'
           AND p.published_at <= NOW()
           AND COALESCE(p.metadata->>'auto_publish_veto_window', '') = 'true'
           AND EXISTS (
                 SELECT 1 FROM pipeline_tasks t
                  WHERE t.task_id::text = p.metadata->>'pipeline_task_id'
                    AND t.status NOT IN ('approved', 'published')
               )
        RETURNING id, title, metadata->>'pipeline_task_id' AS pipeline_task_id
        """
    )
    for row in rows:
        logger.warning(
            "[scheduled_publisher] Demoted veto-window post %s (%r) — source "
            "task %s is no longer approved; post parked at status='approved', "
            "not published.",
            row["id"], row["title"], row["pipeline_task_id"],
        )
        if row["pipeline_task_id"]:
            await conn.execute(
                "DELETE FROM published_post_edit_metrics "
                "WHERE task_id = $1 AND approver = 'auto_publish'",
                row["pipeline_task_id"],
            )


async def _maybe_park_due_posts_at_gate(pool, *, site_config: SiteConfig) -> None:
    """Pause due scheduled posts at ``final_publish_approval`` when enabled.

    When ``pipeline_gate_final_publish_approval`` is on, every post that is
    ``scheduled`` and due (``published_at <= NOW()``) but not already parked
    (``awaiting_gate IS NULL``) is handed to
    :func:`services.posts_approval_service.pause_post_at_gate`, which sets the
    gate columns, notifies the operator, and writes the audit row. The promote
    UPDATE (which now filters ``awaiting_gate IS NULL``) then skips them until
    the operator clears the gate via ``poindexter schedule approve <post_id>``.

    Best-effort: any failure is logged at WARNING and swallowed so the publish
    loop keeps running (a crash here would wedge every subsequent due post).
    """
    from services.approval_service import is_gate_enabled
    from services.posts_approval_service import (
        FINAL_PUBLISH_GATE,
        pause_post_at_gate,
    )

    if not is_gate_enabled(FINAL_PUBLISH_GATE, site_config):
        return

    try:
        async with pool.acquire() as conn:
            due = await conn.fetch(
                """
                SELECT id::text AS id, slug, title
                  FROM posts
                 WHERE status = 'scheduled'
                   AND published_at <= NOW()
                   AND awaiting_gate IS NULL
                """
            )
    except Exception as exc:
        logger.warning(
            "[scheduled_publisher] final_publish_approval: due-post query "
            "failed (non-fatal), skipping park this tick: %s",
            exc,
        )
        return

    site_url = ""
    try:
        site_url = str(site_config.get("site_url", "") or "")
    except Exception:
        site_url = ""

    for row in due:
        post_id = row["id"]
        slug = row["slug"]
        artifact = {"slug": slug, "title": row["title"]}
        if site_url and slug:
            artifact["permalink"] = f"{site_url.rstrip('/')}/posts/{slug}"
        try:
            await pause_post_at_gate(
                post_id=post_id,
                gate_name=FINAL_PUBLISH_GATE,
                artifact=artifact,
                site_config=site_config,
                pool=pool,
                notify=True,
            )
            logger.info(
                "[scheduled_publisher] final_publish_approval: parked post %s "
                "(%s) for operator sign-off",
                post_id,
                slug,
            )
        except Exception as exc:
            logger.warning(
                "[scheduled_publisher] final_publish_approval: pause_post_at_gate "
                "failed for post %s (non-fatal): %s",
                post_id,
                exc,
            )


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
