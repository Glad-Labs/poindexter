"""HITL approval-gate service for the ``posts`` table — final-publish gate.

Sister module to :mod:`services.approval_service` (which handles the
mid-pipeline gates on ``pipeline_tasks``). This one operates on the
``posts`` table and exists for the ``final_publish_approval`` gate:
the operator's last chance to veto a scheduled post right before
``services.scheduled_publisher`` flips it from ``status='scheduled'``
to ``status='published'``.

Why a separate module
---------------------

``approval_service.approve()`` and friends hard-write to
``pipeline_tasks``. ``posts`` is a different table with a different
PK column (``id`` UUID, no ``task_id`` column) and isn't reachable
from the mid-pipeline gate machinery. Splitting keeps each module's
SQL focused — one table per file — and avoids polymorphic
"figure out which table this ID is in" indirection that would silently
flip the wrong status if a UUID happened to collide.

Design rules (mirror approval_service)
--------------------------------------

- DI seam: every public function takes ``pool`` and ``site_config``.
- DB-first config: gate enable lives at
  ``app_settings.pipeline_gate_final_publish_approval``. Reuses
  :func:`approval_service.is_gate_enabled` so toggling and listing
  flow through the same code path. The dedicated
  :func:`set_publish_gate_enabled` wrapper exists only as a label;
  it delegates to :func:`approval_service.set_gate_enabled`.
- Bail loudly: every approve/reject writes an ``audit_log`` row.
- No silent fallback: passing the wrong gate name raises
  :class:`PostGateMismatchError`.

Glossary
--------

The publish gate is a single named gate (``final_publish_approval``).
Architecturally there's no reason another publish-stage gate
couldn't reuse the same columns, so the helpers take ``gate_name``
explicitly rather than hard-coding the slug. New publish-side gates
can be added without a schema change.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from services.audit_log import audit_log_bg
from services.logger_config import get_logger

logger = get_logger(__name__)


# Canonical gate name for the final-publish approval. Wired to the same
# ``pipeline_gate_*`` app_settings prefix the mid-pipeline gates use,
# so a uniform "list gates" call surfaces both kinds.
FINAL_PUBLISH_GATE = "final_publish_approval"

# Status the post sits at while the gate is open. We keep
# ``status='scheduled'`` and use ``awaiting_gate`` as the orthogonal
# "halted" signal — the publisher's WHERE clause filters out anything
# with awaiting_gate set, so the row stays in the queue but won't
# transition until the operator clears the gate.
PAUSED_STATUS = "scheduled"

# Default status the row is moved to when the operator rejects the
# publish gate. Configurable per-gate via app_settings — see
# :func:`reject_publish` for the lookup.
DEFAULT_REJECT_STATUS = "rejected"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PostsApprovalServiceError(Exception):
    """Base class for posts-approval errors."""


class PostNotFoundError(PostsApprovalServiceError):
    """Raised when no row in ``posts`` matches the supplied id."""


class PostNotPausedError(PostsApprovalServiceError):
    """Raised when ``approve_publish`` / ``reject_publish`` is called on
    a row that isn't currently paused at any gate. Distinct from
    :class:`PostNotFoundError` so the operator can tell whether the
    gate already cleared (race with another operator) versus a typo."""


class PostGateMismatchError(PostsApprovalServiceError):
    """Raised when ``gate_name`` is supplied and doesn't match the
    active gate on the row. Loud — never silently approve / reject the
    wrong gate."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_artifact(raw: Any) -> dict[str, Any]:
    """Defensive parse — JSONB sometimes round-trips as str via asyncpg."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except (ValueError, TypeError):
            return {"raw": raw}
    return {"raw": str(raw)}


async def _fetch_post_row(pool: Any, post_id: str) -> dict[str, Any] | None:
    """Return the minimal ``posts`` row needed for gate decisions."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id::text       AS id,
                   slug,
                   title,
                   status,
                   published_at,
                   awaiting_gate,
                   gate_artifact,
                   gate_paused_at
              FROM posts
             WHERE id::text = $1
            """,
            str(post_id),
        )
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Pause — called by the scheduled publisher when the gate is enabled
# ---------------------------------------------------------------------------


async def pause_post_at_gate(
    *,
    post_id: str,
    gate_name: str,
    artifact: dict[str, Any],
    site_config: Any,
    pool: Any,
    notify: bool = True,
) -> dict[str, Any]:
    """Persist publish-gate state on a post and notify the operator.

    Idempotent — re-pausing at the same gate refreshes the artifact and
    timestamp without touching ``status`` or scheduling fields.

    Args:
        post_id: UUID-as-string of the ``posts`` row.
        gate_name: Stable slug. Typically ``FINAL_PUBLISH_GATE``.
        artifact: Operator-facing review payload. Should include slug,
            title, and ideally a preview URL.
        site_config: SiteConfig instance for notification routing.
        pool: asyncpg pool.
        notify: When False, skip the notification fan-out (used by tests).

    Returns:
        Dict with ``ok``, ``post_id``, ``gate_name``, ``paused_at``,
        ``notify`` (delivery state).

    Audit trail:
        Always emits an ``approval_gate_paused`` audit row with
        ``source='posts_approval_service'``.
    """
    paused_at = datetime.now(timezone.utc)
    artifact_json = json.dumps(artifact or {}, default=str)

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE posts
                   SET awaiting_gate = $1,
                       gate_artifact = $2::jsonb,
                       gate_paused_at = $3,
                       updated_at = NOW()
                 WHERE id::text = $4
                """,
                gate_name,
                artifact_json,
                paused_at,
                str(post_id),
            )
    except Exception:
        logger.exception(
            "[posts_approval_service] pause_post_at_gate DB write failed "
            "post=%s gate=%s",
            post_id, gate_name,
        )
        audit_log_bg(
            event_type="approval_gate_pause_failed",
            source="posts_approval_service",
            details={
                "gate_name": gate_name,
                "post_id": str(post_id),
                "error": "db_write_failed",
            },
            severity="error",
        )
        raise

    audit_log_bg(
        event_type="approval_gate_paused",
        source="posts_approval_service",
        details={
            "gate_name": gate_name,
            "post_id": str(post_id),
            "artifact_keys": sorted((artifact or {}).keys()),
            "paused_at": paused_at.isoformat(),
        },
        severity="info",
    )

    notify_result: dict[str, Any] = {"sent": False, "reason": "skipped"}
    if notify:
        notify_result = await _notify_publish_gate_tripped(
            post_id=str(post_id),
            gate_name=gate_name,
            artifact=artifact or {},
            site_config=site_config,
        )

    return {
        "ok": True,
        "post_id": str(post_id),
        "gate_name": gate_name,
        "paused_at": paused_at.isoformat(),
        "notify": notify_result,
    }


async def _notify_publish_gate_tripped(
    *,
    post_id: str,
    gate_name: str,
    artifact: dict[str, Any],
    site_config: Any,
) -> dict[str, Any]:
    """Reuse the same notification path the mid-pipeline gates use."""
    from services.task_executor import _notify_alert

    title = artifact.get("title") or artifact.get("slug") or "(untitled)"
    preview = artifact.get("preview_url") or artifact.get("permalink") or ""
    msg_lines = [
        f"[publish gate] Post {post_id[:8]} paused at gate '{gate_name}'.",
        f"Title: {title}",
    ]
    if preview:
        msg_lines.append(f"Preview: {preview}")
    msg_lines.extend(
        [
            f"Approve: poindexter approve-publish {post_id}",
            f"Reject:  poindexter reject-publish  {post_id} --reason '...'",
        ]
    )
    msg = "\n".join(msg_lines)

    try:
        await _notify_alert(msg, site_config, critical=False)
        return {"sent": True, "reason": "ok"}
    except Exception as exc:
        logger.warning(
            "[posts_approval_service] notify_publish_gate_tripped failed "
            "post=%s gate=%s: %s",
            post_id, gate_name, exc,
        )
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Approve — operator green-lights the publish
# ---------------------------------------------------------------------------


async def approve_publish(
    *,
    post_id: str,
    gate_name: Optional[str] = None,
    feedback: Optional[str] = None,
    site_config: Any,
    pool: Any,
) -> dict[str, Any]:
    """Clear the publish gate so the next scheduler tick can publish.

    Clears ``awaiting_gate`` / ``gate_artifact`` / ``gate_paused_at``
    and leaves ``status='scheduled'``. The
    :mod:`scheduled_publisher` background loop then picks the row up
    on its next poll and flips it to ``'published'`` (assuming
    ``published_at <= NOW()``, which is already true by virtue of the
    publisher having been the one to pause it).

    Args:
        post_id: UUID of the ``posts`` row.
        gate_name: Optional — assert which gate is being approved.
            None means "any active gate."
        feedback: Optional operator note recorded on the audit row.
        site_config: SiteConfig (DI).
        pool: asyncpg pool.

    Returns:
        Dict with ``ok``, ``post_id``, ``gate_name``, ``feedback``.

    Raises:
        PostNotFoundError: ID isn't in ``posts``.
        PostNotPausedError: row exists but ``awaiting_gate`` is NULL.
        PostGateMismatchError: ``gate_name`` doesn't match active gate.
    """
    row = await _fetch_post_row(pool, post_id)
    if row is None:
        logger.warning(
            "[posts_approval_service] approve_publish: post %s not found",
            post_id,
        )
        raise PostNotFoundError(f"Post {post_id} not found")

    active_gate = row.get("awaiting_gate")
    if not active_gate:
        raise PostNotPausedError(
            f"Post {post_id} is not paused at any gate "
            f"(current status={row.get('status')!r})"
        )

    if gate_name is not None and gate_name != active_gate:
        raise PostGateMismatchError(
            f"Post {post_id} is paused at gate {active_gate!r}, "
            f"not {gate_name!r}. Refusing to approve the wrong gate."
        )

    cleared_gate = active_gate

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE posts
               SET awaiting_gate = NULL,
                   gate_artifact = '{}'::jsonb,
                   gate_paused_at = NULL,
                   updated_at = NOW()
             WHERE id::text = $1
            """,
            str(post_id),
        )

    audit_log_bg(
        event_type="approval_gate_approved",
        source="posts_approval_service",
        details={
            "gate_name": cleared_gate,
            "post_id": str(post_id),
            "feedback": feedback or "",
        },
        severity="info",
    )

    # SiteConfig is part of the DI seam even though we don't use it
    # directly here. Keeps the call signature consistent across the
    # approve/reject helpers and lets future per-gate logic pick up
    # config without changing every call site.
    _ = site_config

    return {
        "ok": True,
        "post_id": str(post_id),
        "gate_name": cleared_gate,
        "feedback": feedback or "",
    }


# ---------------------------------------------------------------------------
# Reject — operator vetoes the publish
# ---------------------------------------------------------------------------


async def reject_publish(
    *,
    post_id: str,
    gate_name: Optional[str] = None,
    reason: Optional[str] = None,
    site_config: Any,
    pool: Any,
) -> dict[str, Any]:
    """Reject the publish at the named gate.

    Clears the gate columns and moves the row to a non-publishing
    status so the scheduler stops pursuing it. Default new status is
    ``'rejected'``; a per-gate override lives at
    ``approval_gate_<gate>_reject_status`` in app_settings — for
    example, set ``approval_gate_final_publish_approval_reject_status``
    to ``"draft"`` to bounce rejected posts back into the draft pool
    for re-work.

    Args:
        post_id: UUID of the ``posts`` row.
        gate_name: Optional — assert which gate is being rejected.
        reason: Optional operator-supplied veto note. Stored on the
            audit row; the ``posts`` table has no rejection column to
            persist it inline.
        site_config: SiteConfig (DI seam — used for per-gate overrides).
        pool: asyncpg pool.

    Returns:
        Dict with ``ok``, ``post_id``, ``gate_name``, ``new_status``,
        ``reason``.
    """
    row = await _fetch_post_row(pool, post_id)
    if row is None:
        logger.warning(
            "[posts_approval_service] reject_publish: post %s not found",
            post_id,
        )
        raise PostNotFoundError(f"Post {post_id} not found")

    active_gate = row.get("awaiting_gate")
    if not active_gate:
        raise PostNotPausedError(
            f"Post {post_id} is not paused at any gate "
            f"(current status={row.get('status')!r})"
        )

    if gate_name is not None and gate_name != active_gate:
        raise PostGateMismatchError(
            f"Post {post_id} is paused at gate {active_gate!r}, "
            f"not {gate_name!r}. Refusing to reject the wrong gate."
        )

    rejected_gate = active_gate

    new_status = DEFAULT_REJECT_STATUS
    if site_config is not None:
        custom = site_config.get(
            f"approval_gate_{rejected_gate}_reject_status", ""
        )
        if custom:
            new_status = str(custom).strip()

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE posts
               SET status = $2,
                   awaiting_gate = NULL,
                   gate_artifact = '{}'::jsonb,
                   gate_paused_at = NULL,
                   updated_at = NOW()
             WHERE id::text = $1
            """,
            str(post_id),
            new_status,
        )

    audit_log_bg(
        event_type="approval_gate_rejected",
        source="posts_approval_service",
        details={
            "gate_name": rejected_gate,
            "post_id": str(post_id),
            "reason": reason or "",
            "new_status": new_status,
        },
        severity="warning",
    )

    return {
        "ok": True,
        "post_id": str(post_id),
        "gate_name": rejected_gate,
        "new_status": new_status,
        "reason": reason or "",
    }


# ---------------------------------------------------------------------------
# List + show — read-side helpers for CLI / MCP / dashboard
# ---------------------------------------------------------------------------


async def list_pending_publish(
    *,
    pool: Any,
    gate_name: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return every post currently paused at any publish gate."""
    where = "WHERE awaiting_gate IS NOT NULL"
    args: list[Any] = []
    if gate_name:
        where += " AND awaiting_gate = $1"
        args.append(gate_name)
    args.append(limit)
    limit_param = f"${len(args)}"

    sql = f"""
        SELECT id::text     AS post_id,
               slug,
               title,
               status,
               published_at,
               awaiting_gate AS gate_name,
               gate_artifact,
               gate_paused_at
          FROM posts
          {where}
         ORDER BY gate_paused_at ASC NULLS LAST
         LIMIT {limit_param}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)

    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        d["artifact"] = _coerce_artifact(d.pop("gate_artifact", None))
        for ts_field in ("gate_paused_at", "published_at"):
            ts = d.get(ts_field)
            if ts is not None and hasattr(ts, "isoformat"):
                d[ts_field] = ts.isoformat()
        out.append(d)
    return out


async def show_pending_publish(
    *,
    pool: Any,
    post_id: str,
) -> dict[str, Any]:
    """Return the single post's gate state + artifact, or raise."""
    row = await _fetch_post_row(pool, post_id)
    if row is None:
        raise PostNotFoundError(f"Post {post_id} not found")
    if not row.get("awaiting_gate"):
        raise PostNotPausedError(
            f"Post {post_id} is not paused at any gate "
            f"(current status={row.get('status')!r})"
        )

    artifact = _coerce_artifact(row.get("gate_artifact"))
    paused_at = row.get("gate_paused_at")
    if paused_at is not None and hasattr(paused_at, "isoformat"):
        paused_at = paused_at.isoformat()
    published_at = row.get("published_at")
    if published_at is not None and hasattr(published_at, "isoformat"):
        published_at = published_at.isoformat()

    return {
        "post_id": row["id"],
        "slug": row.get("slug"),
        "title": row.get("title"),
        "status": row.get("status"),
        "published_at": published_at,
        "gate_name": row["awaiting_gate"],
        "artifact": artifact,
        "gate_paused_at": paused_at,
    }


__all__ = [
    "PostsApprovalServiceError",
    "PostNotFoundError",
    "PostNotPausedError",
    "PostGateMismatchError",
    "FINAL_PUBLISH_GATE",
    "PAUSED_STATUS",
    "DEFAULT_REJECT_STATUS",
    "pause_post_at_gate",
    "approve_publish",
    "reject_publish",
    "list_pending_publish",
    "show_pending_publish",
]
