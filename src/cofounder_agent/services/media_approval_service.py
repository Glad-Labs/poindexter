"""Media Approval Service — per-medium distribution gate.

Single source of truth for whether a generated podcast / video / short
is allowed to leave the operator's machine.

Workflow
========
1. Pipeline generates a post (no media yet)
2. Operator approves the post → post publishes immediately
3. Background jobs (or inline post-publish hooks) generate media
4. Each generated medium calls ``record_pending`` here, which inserts
   a ``status='pending'`` row in ``media_approvals``
5. Operator reviews + calls ``approve(post_id, medium)`` or
   ``reject(post_id, medium)`` (typically via ``poindexter media`` CLI)
6. Distribution paths (RSS feed query, YouTube publish adapter) call
   ``is_approved(post_id, medium)`` before exposing the media

Auto-approve tiers
==================
``record_pending`` checks three tiers in order; the first match wins:

**Tier 1 — manual opt-in** (per-niche-per-medium):
``niche.<slug>.media.<medium>.auto_approve = true``
When set, the row is inserted with ``status='approved'``,
``decided_by='auto:niche.<slug>'``. Operator flips this once they
unconditionally trust a niche/medium combo.

**Tier 2 — earned autonomy** (#531):
When ``media.gate2.earned_autonomy_enabled = true`` AND the last N
successfully-dispatched rows for the same (niche_slug, medium) were all
``dispatch_success=true`` (N = ``media.gate2.earned_autonomy_min_dispatches``,
default 5), the new medium is approved automatically with
``decided_by='auto:earned_autonomy:<niche_slug>'``. Observability: an
``audit_log`` finding (kind=``media_earned_autonomy_granted``) is emitted.

**Tier 3 — manual review** (default):
Row is inserted with ``status='pending'``; operator reviews via
``poindexter media pending``.

All tiers default to the conservative path (Tier 3) on any missing
setting or query error — explicit opt-in, no silent default per
``feedback_no_silent_defaults``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from utils.findings import emit_finding  # noqa: E402 — audit observability

logger = logging.getLogger(__name__)


_VALID_MEDIA = frozenset({"podcast", "video", "video_short"})


class InvalidMediumError(ValueError):
    """Raised when a caller passes a medium not in ``_VALID_MEDIA``.

    Fail loud per ``feedback_no_silent_defaults`` — a typo'd medium
    silently dropping a generated podcast or video is exactly the
    silent-failure mode the operator playbook calls out.
    """


def _validate_medium(medium: str) -> None:
    if medium not in _VALID_MEDIA:
        raise InvalidMediumError(
            f"Unknown medium {medium!r}; expected one of "
            f"{sorted(_VALID_MEDIA)}",
        )


async def _niche_auto_approve_enabled(
    db: Any, post_id: str, medium: str,
) -> tuple[bool, str | None]:
    """Return ``(enabled, niche_slug)`` for the per-niche-per-medium
    auto-approve setting.

    Looks up the post's niche via the canonical
    ``posts.metadata->>'pipeline_task_id'`` → ``pipeline_tasks.niche_slug``
    seam (``posts`` has no ``niche_slug`` column — the niche lives on the
    source task), then reads ``niche.<slug>.media.<medium>.auto_approve``
    from app_settings. Missing setting → not enabled (the conservative
    default).

    ``db`` may be either an asyncpg Pool or Connection — both expose
    the same ``.fetchrow`` / ``.execute`` interface so we don't need
    to acquire a separate connection (which would deadlock if the
    caller already holds the pool's only free connection).
    """
    niche_row = await db.fetchrow(
        """
        SELECT pt.niche_slug
        FROM posts p
        LEFT JOIN pipeline_tasks pt
            ON pt.task_id = (p.metadata ->> 'pipeline_task_id')
        WHERE p.id = $1::uuid
        """,
        post_id,
    )
    if not niche_row or not niche_row["niche_slug"]:
        # No niche on the post (legacy data, dev_diary edge case) —
        # default to manual approval. Conservative + safe.
        return (False, None)
    niche_slug = str(niche_row["niche_slug"])

    setting_key = f"niche.{niche_slug}.media.{medium}.auto_approve"
    setting_row = await db.fetchrow(
        "SELECT value FROM app_settings WHERE key = $1 AND is_active = true",
        setting_key,
    )
    if not setting_row:
        return (False, niche_slug)

    # Match the app_settings convention: 'true' / '1' / 'yes' all
    # count as enabled, anything else (including empty string)
    # disables. Same shape as ``site_config.get_bool``.
    raw = str(setting_row["value"] or "").strip().lower()
    enabled = raw in {"true", "1", "yes", "on"}
    return (enabled, niche_slug)


async def _earned_autonomy_check(
    db: Any, niche_slug: str, medium: str,
) -> bool:
    """Return True iff the (niche_slug, medium) combo has earned autonomous
    Gate-2 approval based on its dispatch track record (#531).

    Reads the global master switch (``media.gate2.earned_autonomy_enabled``)
    and the consecutive-success threshold
    (``media.gate2.earned_autonomy_min_dispatches``; per-niche override
    ``niche.<slug>.media.<medium>.earned_autonomy_min_dispatches`` takes
    precedence when set).

    Returns ``False`` conservatively on any missing setting, insufficient
    history, or any failure in the recent dispatch record.
    """
    # Master switch — off by default, explicit opt-in.
    enabled_row = await db.fetchrow(
        "SELECT value FROM app_settings "
        "WHERE key = 'media.gate2.earned_autonomy_enabled' AND is_active = true",
    )
    if not enabled_row:
        return False
    raw = str(enabled_row["value"] or "").strip().lower()
    if raw not in ("true", "1", "yes", "on"):
        return False

    # Per-niche threshold override → global default.
    override_key = (
        f"niche.{niche_slug}.media.{medium}.earned_autonomy_min_dispatches"
    )
    threshold_row = await db.fetchrow(
        "SELECT value FROM app_settings WHERE key = $1 AND is_active = true",
        override_key,
    )
    if not threshold_row:
        threshold_row = await db.fetchrow(
            "SELECT value FROM app_settings "
            "WHERE key = 'media.gate2.earned_autonomy_min_dispatches' "
            "AND is_active = true",
        )

    try:
        min_dispatches = int(threshold_row["value"]) if threshold_row else 5
    except (TypeError, ValueError):
        min_dispatches = 5

    if min_dispatches <= 0:
        return False

    # Fetch the last N dispatched rows for this niche + medium.
    rows = await db.fetch(
        """
        SELECT ma.dispatch_success
        FROM media_approvals ma
        JOIN posts p ON p.id = ma.post_id
        JOIN pipeline_tasks pt
            ON pt.task_id = (p.metadata ->> 'pipeline_task_id')
        WHERE pt.niche_slug = $1
          AND ma.medium = $2
          AND ma.dispatched_at IS NOT NULL
        ORDER BY ma.dispatched_at DESC
        LIMIT $3
        """,
        niche_slug, medium, min_dispatches,
    )

    if len(rows) < min_dispatches:
        return False  # Not enough history yet — conservative.

    return all(r["dispatch_success"] is True for r in rows)


async def record_pending(
    db: Any, post_id: str, medium: str,
) -> str:
    """Insert a row for a freshly generated medium.

    Returns the resulting ``status`` (``'pending'`` or ``'approved'``
    if any auto-approve tier fired). Idempotent via ``ON CONFLICT DO NOTHING``
    on the ``(post_id, medium)`` PK — re-generation events don't
    clobber prior decisions.

    Call this from the generation path (backfill jobs + inline
    post-publish hooks) as soon as the media file is on disk.

    ``db`` accepts either an asyncpg Pool or Connection.
    """
    _validate_medium(medium)

    auto_enabled, niche_slug = await _niche_auto_approve_enabled(
        db, post_id, medium,
    )

    # Tier 1: per-niche manual opt-in.
    if auto_enabled:
        await db.execute(
            """
            INSERT INTO media_approvals
                (post_id, medium, status, decided_at, decided_by)
            VALUES ($1::uuid, $2, 'approved', now(), $3)
            ON CONFLICT (post_id, medium) DO NOTHING
            """,
            post_id, medium, f"auto:niche.{niche_slug}",
        )
        logger.info(
            "[media_approval] tier-1 auto-approved %s for post %s (niche=%s)",
            medium, post_id, niche_slug,
        )
        return "approved"

    # Tier 2: earned autonomy (#531) — only when niche is known.
    if niche_slug and await _earned_autonomy_check(db, niche_slug, medium):
        decided_by = f"auto:earned_autonomy:{niche_slug}"
        await db.execute(
            """
            INSERT INTO media_approvals
                (post_id, medium, status, decided_at, decided_by)
            VALUES ($1::uuid, $2, 'approved', now(), $3)
            ON CONFLICT (post_id, medium) DO NOTHING
            """,
            post_id, medium, decided_by,
        )
        logger.info(
            "[media_approval] tier-2 earned-autonomy approved %s for post %s "
            "(niche=%s, decided_by=%s)",
            medium, post_id, niche_slug, decided_by,
        )
        # Observability: emit a finding so Grafana / findings-dispatcher can see
        # when autonomy is exercised (audit trail, not an alert).
        emit_finding(
            source="media_approval",
            kind="media_earned_autonomy_granted",
            title=(
                f"Earned-autonomy Gate-2 approved: {medium} for "
                f"niche={niche_slug}"
            ),
            body=(
                f"post {post_id}: Gate-2 approval bypassed via earned-autonomy "
                f"track record (niche={niche_slug}, medium={medium}). "
                f"decided_by={decided_by}."
            ),
            severity="info",
            dedup_key=f"media_earned_autonomy:{post_id}:{medium}",
            extra={
                "post_id": post_id,
                "niche_slug": niche_slug,
                "medium": medium,
            },
        )
        return "approved"

    # Tier 3: manual review.
    await db.execute(
        """
        INSERT INTO media_approvals (post_id, medium, status)
        VALUES ($1::uuid, $2, 'pending')
        ON CONFLICT (post_id, medium) DO NOTHING
        """,
        post_id, medium,
    )
    logger.info(
        "[media_approval] pending: %s for post %s (awaiting operator)",
        medium, post_id,
    )
    return "pending"


async def notify_pending_for_review(
    db: Any, post_id: str, medium: str,
) -> bool:
    """Ping Discord ops that a new medium has Layer 1 signals + needs review.

    Called by ``media_quality_service.evaluate_*`` after the quality
    eval has populated ``quality_score`` / ``quality_signals`` /
    ``quality_evaluated_at`` on the row. Pure observability — failure
    here MUST NOT roll back the eval or the gate, so the helper wraps
    its own dispatch in ``try/except``.

    Skips when:
    - The row was already auto-approved (niche fast path) — operator
      doesn't need to act, Discord notification would be noise.
    - The row was auto-rejected by Layer 1 — same reason; the file
      will never reach the public surface and the operator can find
      it via ``poindexter media pending`` if they want to inspect.
    - The row is missing (race: notify called before record_pending
      landed). The conservative default is to skip; the next
      generation pass will trip the notification.

    Returns ``True`` when a notification was dispatched, ``False`` when
    it was skipped (any reason: status not pending, row missing,
    notification disabled).

    Medium-approval is ROUTINE traffic per
    ``feedback_telegram_vs_discord`` — Discord only, never Telegram.
    """
    _validate_medium(medium)

    # Discord notification is opt-in via app_settings — defaults to
    # ENABLED so the first time the operator generates a podcast they
    # see the ping in Discord without flipping a switch first. They
    # can turn it off if it becomes noise.
    enabled_row = await db.fetchrow(
        "SELECT value FROM app_settings "
        "WHERE key = 'media_approval_discord_notify_enabled' "
        "AND is_active = true",
    )
    if enabled_row:
        raw = str(enabled_row["value"] or "").strip().lower()
        if raw not in ("true", "1", "yes", "on", ""):
            return False

    row = await db.fetchrow(
        """
        SELECT
            ma.status,
            ma.quality_score,
            ma.quality_signals,
            p.title,
            p.slug
        FROM media_approvals ma
        LEFT JOIN posts p ON p.id = ma.post_id
        WHERE ma.post_id = $1::uuid AND ma.medium = $2
        """,
        post_id, medium,
    )
    if row is None:
        logger.debug(
            "[media_approval] notify skipped — no row for post=%s medium=%s",
            post_id[:8], medium,
        )
        return False

    if row["status"] != "pending":
        # Auto-approved (niche fast path) OR auto-rejected (Layer 1).
        # Either way, the operator doesn't have a pending decision —
        # no notification needed.
        logger.debug(
            "[media_approval] notify skipped — status=%s for post=%s medium=%s",
            row["status"], post_id[:8], medium,
        )
        return False

    signals_raw = row.get("quality_signals")
    signals: dict[str, Any] = {}
    if isinstance(signals_raw, str):
        try:
            signals = json.loads(signals_raw)
        except (json.JSONDecodeError, TypeError):
            signals = {}
    elif isinstance(signals_raw, dict):
        signals = signals_raw

    score = row.get("quality_score")
    dur = signals.get("duration_seconds")
    sil = signals.get("silence_ratio")
    size = signals.get("file_size_bytes")
    title = (row.get("title") or "(untitled)")[:80]
    post_id_short = post_id[:8]

    score_str = f"{float(score):.2f}" if score is not None else "—"
    dur_str = f"{dur:.0f}s" if dur is not None else "—"
    sil_str = f"{sil:.0%}" if sil is not None else "—"
    size_str = f"{int(size) // 1024}KB" if size is not None else "—"

    medium_emoji = {"podcast": "\U0001F3A7", "video": "\U0001F3AC", "video_short": "\U0001F39E"}
    emoji = medium_emoji.get(medium, "\U0001F3AC")

    message = (
        f"{emoji} New {medium} awaiting approval\n"
        f"post: {post_id_short} — {title}\n"
        f"quality: score={score_str}  duration={dur_str}  "
        f"silence={sil_str}  size={size_str}\n"
        f"review: poindexter media pending --medium {medium}\n"
        f"preview: poindexter media open {post_id_short} {medium}"
    )

    try:
        # Lazy-import to avoid pulling the integrations framework into
        # the approval service's import path (keeps the test surface
        # small + lets callers mock notify_operator at one seam).
        from services.integrations.operator_notify import notify_operator

        await notify_operator(message, critical=False)
        logger.info(
            "[media_approval] notified ops: %s pending for post %s",
            medium, post_id_short,
        )
        return True
    except Exception as e:  # noqa: BLE001
        # Pure observability — never bubble a notification failure up
        # into the eval / approval path. Matt explicitly called this
        # out in the task spec.
        logger.warning(
            "[media_approval] Discord notify failed for %s/%s: %s",
            medium, post_id_short, e,
        )
        return False


async def is_approved(db: Any, post_id: str, medium: str) -> bool:
    """Return True iff the row exists AND status is ``'approved'``.

    Distribution paths (RSS feed query, YouTube publish adapter) call
    this before exposing the media. Missing row → not approved —
    means generation never reached ``record_pending`` (race) or the
    medium isn't in the post's niche policy (shouldn't have been
    generated in the first place).

    ``db`` accepts either an asyncpg Pool or Connection.
    """
    _validate_medium(medium)
    row = await db.fetchrow(
        """
        SELECT status FROM media_approvals
        WHERE post_id = $1::uuid AND medium = $2
        """,
        post_id, medium,
    )
    return bool(row and row["status"] == "approved")


async def decide(
    db: Any,
    post_id: str,
    medium: str,
    *,
    approved: bool,
    decided_by: str,
    notes: str | None = None,
) -> None:
    """Operator decision — set status to ``approved`` or ``rejected``.

    ``decided_by`` should be the operator identity (``operator:<user>``
    in production). Tests + CLI default to ``operator:cli`` /
    ``operator:test`` so the provenance still records the surface.

    Raises ``ValueError`` if the row doesn't exist — the operator is
    deciding on something that was never generated. Fail loud per
    ``feedback_no_silent_defaults``; a silent INSERT-on-decide would
    let an operator pre-approve a not-yet-generated medium, which
    bypasses the whole gate.

    ``db`` accepts either an asyncpg Pool or Connection.
    """
    _validate_medium(medium)
    new_status = "approved" if approved else "rejected"

    row = await db.fetchrow(
        """
        UPDATE media_approvals
        SET status = $3, decided_at = now(),
            decided_by = $4, notes = $5
        WHERE post_id = $1::uuid AND medium = $2
        RETURNING status
        """,
        post_id, medium, new_status, decided_by, notes,
    )
    if row is None:
        raise ValueError(
            f"No media_approvals row for post={post_id!r} medium={medium!r}; "
            f"the medium hasn't been generated yet — decide() can't "
            f"pre-approve un-generated media.",
        )
    logger.info(
        "[media_approval] %s by %s: %s for post %s",
        new_status, decided_by, medium, post_id,
    )


async def record_dispatched(
    db: Any, post_id: str, medium: str, *, success: bool,
) -> None:
    """Stamp a dispatch attempt on the media_approvals row.

    ``dispatched_at`` is set to ``now()`` on the first successful call;
    subsequent calls update ``dispatch_success`` but leave the original
    timestamp intact (the column records when the FIRST successful
    delivery happened, not the most recent attempt).

    ``success=False`` records the failed attempt so operators can see
    retry history, but does NOT set ``dispatched_at`` — the row remains
    eligible for re-dispatch on the next BackfillVideosJob cycle.
    """
    _validate_medium(medium)
    if success:
        await db.execute(
            """
            UPDATE media_approvals
               SET dispatched_at    = COALESCE(dispatched_at, now()),
                   dispatch_success = true
             WHERE post_id = $1::uuid AND medium = $2
            """,
            post_id, medium,
        )
    else:
        await db.execute(
            """
            UPDATE media_approvals
               SET dispatch_success = false
             WHERE post_id = $1::uuid AND medium = $2
            """,
            post_id, medium,
        )
    logger.info(
        "[media_approval] dispatch %s: %s for post %s",
        "OK" if success else "FAILED", medium, post_id,
    )


async def list_approved_undispatched(
    db: Any, *, medium: str | None = None, limit: int = 100,
) -> list[dict[str, Any]]:
    """Return approved rows that have never been successfully dispatched.

    Used by BackfillVideosJob to find videos approved before dispatch
    tracking was introduced (poindexter#558), or for any retry pass
    on transiently-failed uploads.
    """
    if medium is not None:
        _validate_medium(medium)
    rows = await db.fetch(
        """
        SELECT
            ma.post_id::text AS post_id,
            ma.medium,
            p.title,
            p.content,
            p.excerpt,
            p.seo_keywords,
            p.slug
        FROM media_approvals ma
        JOIN posts p ON p.id = ma.post_id
        WHERE ma.status = 'approved'
          AND ma.dispatched_at IS NULL
          AND ($1::text IS NULL OR ma.medium = $1)
        ORDER BY ma.created_at ASC
        LIMIT $2
        """,
        medium, limit,
    )
    return [dict(r) for r in rows]


async def list_pending(
    db: Any, *, medium: str | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    """Operator listing — pending media awaiting review.

    ``medium`` filter is optional (None = all media). Joined with
    ``posts`` so the operator sees the post title in the listing
    rather than just a uuid.

    ``db`` accepts either an asyncpg Pool or Connection.
    """
    if medium is not None:
        _validate_medium(medium)

    rows = await db.fetch(
        """
        SELECT
            ma.post_id::text AS post_id,
            ma.medium,
            ma.created_at,
            ma.quality_score,
            ma.quality_signals,
            ma.quality_evaluated_at,
            p.title,
            p.slug
        FROM media_approvals ma
        JOIN posts p ON p.id = ma.post_id
        WHERE ma.status = 'pending'
          AND ($1::text IS NULL OR ma.medium = $1)
        ORDER BY ma.created_at DESC
        LIMIT $2
        """,
        medium, limit,
    )
    return [dict(r) for r in rows]
