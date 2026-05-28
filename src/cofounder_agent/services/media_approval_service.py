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

Auto-approve
============
Per-niche-per-medium auto-approve is an opt-in setting Matt flips once
he trusts a niche/medium combo. The setting key is::

    niche.<slug>.media.<medium>.auto_approve = true

When set, ``record_pending`` inserts the row with
``status='approved'``, ``decided_by='auto:niche.<slug>'`` directly,
skipping the human-review step. Defaults to ``false`` for every
combination — explicit opt-in, no silent default per
``feedback_no_silent_defaults``.
"""

from __future__ import annotations

import logging
from typing import Any

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

    Looks up the post's niche via ``posts.niche_slug``, then reads
    ``niche.<slug>.media.<medium>.auto_approve`` from app_settings.
    Missing setting → not enabled (the conservative default).

    ``db`` may be either an asyncpg Pool or Connection — both expose
    the same ``.fetchrow`` / ``.execute`` interface so we don't need
    to acquire a separate connection (which would deadlock if the
    caller already holds the pool's only free connection).
    """
    niche_row = await db.fetchrow(
        "SELECT niche_slug FROM posts WHERE id = $1::uuid",
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


async def record_pending(
    db: Any, post_id: str, medium: str,
) -> str:
    """Insert a row for a freshly generated medium.

    Returns the resulting ``status`` (``'pending'`` or ``'approved'``
    if auto-approve fired). Idempotent via ``ON CONFLICT DO NOTHING``
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
            "[media_approval] auto-approved %s for post %s (niche=%s)",
            medium, post_id, niche_slug,
        )
        return "approved"

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
