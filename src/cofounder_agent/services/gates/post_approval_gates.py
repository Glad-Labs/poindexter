"""Per-medium approval gate engine for ``posts``.

Implements the spine of the gate-per-medium workflow described in
Glad-Labs/poindexter#24 and migration 0131. Each function takes an
asyncpg ``pool`` so the module is stateless and trivially testable
against the ``db_pool`` fixture in :mod:`tests.unit.conftest`.

State machine
-------------

A post's gate sequence is created up-front (in workflow order), then
walked one row at a time:

    pending  ── approve_gate ──>  approved  ── advance_workflow ──>  next
       │                                                              │
       ├─── reject_gate ──>  rejected   (post.status = 'rejected')    │
       │                                                              │
       └─── revise_gate ──>  revising   (regenerate stage, then       │
                                         the regen path resets        │
                                         this row back to 'pending')  │

The ``skipped`` state is reserved for gates the operator removed via
``--cascade=False`` but that the workflow no longer needs (e.g.
``podcast`` gate when ``podcast`` is no longer in
``posts.media_to_generate``). The current public API doesn't auto-skip
— callers explicitly mark state if needed.

What this module DOES NOT do
----------------------------

- Notification fan-out lives in callers (CLI / webhook handlers /
  publish_service). The service emits an audit_log_bg row on every
  decision but never reaches into Telegram/Discord directly. The
  ``notify_gate_pending`` helper here builds the message + deep links
  and calls the operator-notify shim — but it's a separate function
  callers explicitly invoke, not a side effect of state transitions.
- Triggering downstream stages (regen, media-gen, distribution) is the
  caller's job. ``advance_workflow`` returns a structured "what to do
  next" descriptor; the caller (typically a worker tick) wires it to
  the real subsystem.
- Per-target distribution sub-gating (``--distribute rss,linkedin``) is
  intentionally out of scope for v1 — see issue #24's locked decisions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from services.audit_log import audit_log_bg
from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Canonical gate names recognised by the workflow runner. ``final`` is
#: the post-everything-generated checkpoint before distribution.
#: ``media_generation_failed`` is auto-inserted when a per-medium retry
#: budget is exhausted.
CANONICAL_GATE_NAMES: tuple[str, ...] = (
    "topic",
    "draft",
    "podcast",
    "video",
    "short",
    "final",
    "media_generation_failed",
)

#: Gate names that map 1:1 to a medium (i.e. only inserted when the
#: corresponding medium is in ``posts.media_to_generate``).
MEDIUM_GATE_NAMES: tuple[str, ...] = ("podcast", "video", "short")

GATE_STATE_PENDING = "pending"
GATE_STATE_APPROVED = "approved"
GATE_STATE_REJECTED = "rejected"
GATE_STATE_REVISING = "revising"
GATE_STATE_SKIPPED = "skipped"

#: All states that are NOT ``pending`` and NOT ``revising``. A
#: pre-existing gate in any of these states must be invalidated by
#: ``--cascade`` before its predecessor can be re-opened.
_DOWNSTREAM_DECISIVE_STATES: frozenset[str] = frozenset(
    {GATE_STATE_APPROVED, GATE_STATE_REJECTED, GATE_STATE_SKIPPED}
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GateServiceError(Exception):
    """Base class for gate-engine errors."""


class GateNotFoundError(GateServiceError):
    """Raised when a (post_id, gate_name) lookup turns up nothing."""


class GateStateError(GateServiceError):
    """Raised when an operation is illegal for the gate's current state.

    Examples: approving a gate already in ``rejected``, reopening a
    gate that was never decided.
    """


class GateCascadeRequiredError(GateServiceError):
    """Raised by ``reopen_gate`` when cascade=False would silently
    invalidate downstream approvals. The caller should retry with
    cascade=True after confirming with the operator.
    """


# ---------------------------------------------------------------------------
# Datatypes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowAdvance:
    """What :func:`advance_workflow` decided should happen next.

    Exactly one of ``next_gate`` / ``ready_to_distribute`` / ``finished``
    is set per call:

    - ``next_gate`` = a pending gate row dict — caller should either
      run the automatic stage that satisfies it (media generation,
      etc.) or surface it to the operator.
    - ``ready_to_distribute`` = True when all gates are decided and
      none rejected — caller should fire the publish/distribute path.
    - ``finished`` = True when the post is in a terminal state (every
      gate decided, or post.status='rejected') and nothing else is
      needed.
    """

    next_gate: Optional[dict[str, Any]] = None
    ready_to_distribute: bool = False
    finished: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Coerce an asyncpg Record (or a dict in tests) to a plain dict
    with predictable string types for the JSON-shaped fields."""
    if row is None:
        return {}
    d = dict(row)
    # JSONB sometimes round-trips as str depending on codec config.
    meta = d.get("metadata")
    if isinstance(meta, str):
        try:
            d["metadata"] = json.loads(meta)
        except (TypeError, ValueError):
            d["metadata"] = {}
    elif meta is None:
        d["metadata"] = {}
    # IDs/UUIDs stringified for the public surface.
    for key in ("id", "post_id"):
        v = d.get(key)
        if v is not None and not isinstance(v, str):
            d[key] = str(v)
    return d


async def _fetch_post_status(pool: Any, post_id: str) -> Optional[str]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM posts WHERE id::text = $1", str(post_id)
        )
        return row["status"] if row else None


async def _find_gate_row(
    pool: Any, post_id: str, gate_name: str
) -> Optional[dict[str, Any]]:
    """Return the LATEST row for (post_id, gate_name) by ordinal.

    A given gate name appears at most once in a normal sequence, but the
    schema permits multiple ordinals for the same name (e.g. a ``draft``
    gate inserted again after a ``revise`` cycle). Latest ordinal wins.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, post_id, gate_name, ordinal, state,
                   created_at, decided_at, approver, notes, metadata
              FROM post_approval_gates
             WHERE post_id::text = $1
               AND gate_name = $2
             ORDER BY ordinal DESC
             LIMIT 1
            """,
            str(post_id),
            gate_name,
        )
        return _row_to_dict(row) if row else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_gates_for_post(
    pool: Any,
    post_id: str,
    gates: list[str],
) -> list[dict[str, Any]]:
    """INSERT one row per gate name in workflow order.

    Args:
        pool: asyncpg pool.
        post_id: UUID of the ``posts`` row.
        gates: Ordered list of gate names to insert. Empty list = the
            post is fully autonomous and never pauses.

    Returns:
        List of inserted row dicts (always in ordinal order).

    Raises:
        ValueError: a gate name isn't in :data:`CANONICAL_GATE_NAMES`.
            Loud-fail because a typo would silently make the workflow
            unreachable (no operator can ever clear an unknown gate).

    Note:
        This is a single-shot INSERT. To re-run after rejection (e.g.
        ``revise`` then a fresh ``draft`` gate), call ``reopen_gate``
        instead, which inserts new ordinals.
    """
    if not gates:
        logger.info(
            "[gates] No gates configured for post %s — fully autonomous workflow",
            post_id,
        )
        audit_log_bg(
            event_type="post_gates_created",
            source="post_approval_gates",
            details={"post_id": str(post_id), "gates": [], "autonomous": True},
            severity="info",
        )
        return []

    for name in gates:
        if name not in CANONICAL_GATE_NAMES:
            raise ValueError(
                f"Unknown gate name {name!r}. Valid names: "
                f"{', '.join(CANONICAL_GATE_NAMES)}"
            )

    inserted: list[dict[str, Any]] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for ordinal, name in enumerate(gates):
                row = await conn.fetchrow(
                    """
                    INSERT INTO post_approval_gates
                        (post_id, gate_name, ordinal, state, metadata)
                    VALUES ($1::uuid, $2, $3, 'pending', '{}'::jsonb)
                    RETURNING id, post_id, gate_name, ordinal, state,
                              created_at, decided_at, approver, notes, metadata
                    """,
                    str(post_id), name, ordinal,
                )
                inserted.append(_row_to_dict(row))

    audit_log_bg(
        event_type="post_gates_created",
        source="post_approval_gates",
        details={
            "post_id": str(post_id),
            "gates": gates,
            "count": len(inserted),
        },
        severity="info",
    )
    logger.info(
        "[gates] Created %d gate rows for post %s: %s",
        len(inserted), post_id, gates,
    )
    return inserted


async def get_gates_for_post(
    pool: Any, post_id: str
) -> list[dict[str, Any]]:
    """Return every gate row for a post, ordered by ordinal ascending.

    Used by the CLI ``post show`` command and by ``reopen_gate``'s
    cascade logic. Always returns a list — empty if the post has no
    gates configured.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, post_id, gate_name, ordinal, state,
                   created_at, decided_at, approver, notes, metadata
              FROM post_approval_gates
             WHERE post_id::text = $1
             ORDER BY ordinal ASC
            """,
            str(post_id),
        )
    return [_row_to_dict(r) for r in rows]


async def get_next_pending_gate(
    pool: Any, post_id: str
) -> Optional[dict[str, Any]]:
    """Return the first (lowest-ordinal) pending gate, or None.

    "First pending" is the canonical "what's the workflow waiting on"
    cursor. Workers and the CLI consult this to decide whether to fire
    an automatic stage, surface a notification, or do nothing.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, post_id, gate_name, ordinal, state,
                   created_at, decided_at, approver, notes, metadata
              FROM post_approval_gates
             WHERE post_id::text = $1
               AND state = 'pending'
             ORDER BY ordinal ASC
             LIMIT 1
            """,
            str(post_id),
        )
    return _row_to_dict(row) if row else None


async def approve_gate(
    pool: Any,
    post_id: str,
    gate_name: str,
    approver: str,
    notes: Optional[str] = None,
) -> dict[str, Any]:
    """Mark the named gate as approved.

    Only flips a row in state=``pending`` (or ``revising`` — accepting
    a revised draft is the same approval action). Other states raise
    :class:`GateStateError` so the operator gets a clear "no" rather
    than silently double-approving.
    """
    row = await _find_gate_row(pool, post_id, gate_name)
    if row is None:
        raise GateNotFoundError(
            f"No gate {gate_name!r} on post {post_id}"
        )
    if row["state"] not in {GATE_STATE_PENDING, GATE_STATE_REVISING}:
        raise GateStateError(
            f"Gate {gate_name!r} on post {post_id} is in state "
            f"{row['state']!r}, can't approve."
        )

    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            UPDATE post_approval_gates
               SET state = 'approved',
                   decided_at = NOW(),
                   approver = $3,
                   notes = COALESCE($4, notes)
             WHERE id = $1::uuid
               AND state = $2
            RETURNING id, post_id, gate_name, ordinal, state,
                      created_at, decided_at, approver, notes, metadata
            """,
            row["id"], row["state"], approver, notes,
        )

    if updated is None:
        # Race — another approver beat us between the SELECT and UPDATE.
        # Re-read so the caller gets the canonical current state.
        current = await _find_gate_row(pool, post_id, gate_name)
        raise GateStateError(
            f"Gate {gate_name!r} on post {post_id} changed state "
            f"concurrently — current state is "
            f"{current['state'] if current else 'unknown'!r}."
        )

    audit_log_bg(
        event_type="post_gate_approved",
        source="post_approval_gates",
        details={
            "post_id": str(post_id),
            "gate_name": gate_name,
            "ordinal": row["ordinal"],
            "approver": approver,
            "notes": notes or "",
        },
        severity="info",
    )
    logger.info(
        "[gates] Approved gate %s on post %s (approver=%s)",
        gate_name, post_id, approver,
    )
    return _row_to_dict(updated)


async def reject_gate(
    pool: Any,
    post_id: str,
    gate_name: str,
    approver: str,
    reason: Optional[str] = None,
) -> dict[str, Any]:
    """Mark the gate rejected and flip ``posts.status='rejected'``.

    Hard kill — the post is permanently out of the workflow. Use
    :func:`revise_gate` if you want a regen instead.
    """
    row = await _find_gate_row(pool, post_id, gate_name)
    if row is None:
        raise GateNotFoundError(
            f"No gate {gate_name!r} on post {post_id}"
        )
    if row["state"] not in {GATE_STATE_PENDING, GATE_STATE_REVISING}:
        raise GateStateError(
            f"Gate {gate_name!r} on post {post_id} is in state "
            f"{row['state']!r}, can't reject."
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                """
                UPDATE post_approval_gates
                   SET state = 'rejected',
                       decided_at = NOW(),
                       approver = $2,
                       notes = COALESCE($3, notes)
                 WHERE id = $1::uuid
                RETURNING id, post_id, gate_name, ordinal, state,
                          created_at, decided_at, approver, notes, metadata
                """,
                row["id"], approver, reason,
            )
            await conn.execute(
                "UPDATE posts SET status = 'rejected', updated_at = NOW() "
                "WHERE id::text = $1",
                str(post_id),
            )

    audit_log_bg(
        event_type="post_gate_rejected",
        source="post_approval_gates",
        details={
            "post_id": str(post_id),
            "gate_name": gate_name,
            "ordinal": row["ordinal"],
            "approver": approver,
            "reason": reason or "",
        },
        severity="warning",
    )
    logger.info(
        "[gates] Rejected gate %s on post %s — post status set to 'rejected'",
        gate_name, post_id,
    )
    return _row_to_dict(updated)


async def revise_gate(
    pool: Any,
    post_id: str,
    gate_name: str,
    approver: str,
    feedback: str,
) -> dict[str, Any]:
    """Bounce the gate back for regen.

    Sets ``state='revising'`` and stuffs the operator feedback into
    ``metadata.feedback`` so the regen stage can read it. Caller (the
    workflow runner) is responsible for actually scheduling the regen
    and, when the regen completes, calling
    :func:`reset_gate_to_pending` (or re-using ``approve_gate`` against
    the new draft).
    """
    if not feedback:
        raise ValueError("revise_gate requires non-empty feedback")

    row = await _find_gate_row(pool, post_id, gate_name)
    if row is None:
        raise GateNotFoundError(
            f"No gate {gate_name!r} on post {post_id}"
        )
    if row["state"] not in {GATE_STATE_PENDING, GATE_STATE_REVISING}:
        raise GateStateError(
            f"Gate {gate_name!r} on post {post_id} is in state "
            f"{row['state']!r}, can't revise."
        )

    # Merge feedback into the existing metadata jsonb. The ``revisions``
    # list grows monotonically so we keep a history (useful when the
    # operator bounces a draft 2-3 times).
    metadata = dict(row.get("metadata") or {})
    revisions = list(metadata.get("revisions") or [])
    revisions.append({"approver": approver, "feedback": feedback})
    metadata["revisions"] = revisions
    metadata["feedback"] = feedback  # convenience pointer to the latest

    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            UPDATE post_approval_gates
               SET state = 'revising',
                   decided_at = NOW(),
                   approver = $2,
                   notes = $3,
                   metadata = $4::jsonb
             WHERE id = $1::uuid
            RETURNING id, post_id, gate_name, ordinal, state,
                      created_at, decided_at, approver, notes, metadata
            """,
            row["id"], approver, feedback, json.dumps(metadata),
        )

    audit_log_bg(
        event_type="post_gate_revising",
        source="post_approval_gates",
        details={
            "post_id": str(post_id),
            "gate_name": gate_name,
            "ordinal": row["ordinal"],
            "approver": approver,
            "feedback": feedback,
            "revision_count": len(revisions),
        },
        severity="info",
    )
    logger.info(
        "[gates] Revising gate %s on post %s (revision #%d)",
        gate_name, post_id, len(revisions),
    )
    return _row_to_dict(updated)


async def reset_gate_to_pending(
    pool: Any,
    post_id: str,
    gate_name: str,
) -> dict[str, Any]:
    """Flip a ``revising`` gate back to ``pending`` after regen lands.

    Called by the workflow runner once the regenerated artifact is
    available for re-review. State must currently be ``revising`` —
    raises :class:`GateStateError` otherwise.
    """
    row = await _find_gate_row(pool, post_id, gate_name)
    if row is None:
        raise GateNotFoundError(
            f"No gate {gate_name!r} on post {post_id}"
        )
    if row["state"] != GATE_STATE_REVISING:
        raise GateStateError(
            f"Gate {gate_name!r} on post {post_id} is in state "
            f"{row['state']!r}, expected 'revising'."
        )

    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            """
            UPDATE post_approval_gates
               SET state = 'pending',
                   decided_at = NULL,
                   approver = NULL
             WHERE id = $1::uuid
            RETURNING id, post_id, gate_name, ordinal, state,
                      created_at, decided_at, approver, notes, metadata
            """,
            row["id"],
        )
    audit_log_bg(
        event_type="post_gate_reset_pending",
        source="post_approval_gates",
        details={"post_id": str(post_id), "gate_name": gate_name},
        severity="info",
    )
    return _row_to_dict(updated)


async def reopen_gate(
    pool: Any,
    post_id: str,
    gate_name: str,
    cascade: bool = False,
) -> dict[str, Any]:
    """Flip a previously-decided gate back to ``pending``.

    If any LATER gate (higher ordinal) is in a decisive state
    (``approved`` / ``rejected`` / ``skipped``):

    - cascade=False (default): raise :class:`GateCascadeRequiredError`.
      The caller is expected to surface this to the operator and only
      retry with cascade=True after confirmation.
    - cascade=True: ALSO flip every later decisive gate back to
      ``pending``. This is the "I want to redo everything from this
      point on" workflow.
    """
    target = await _find_gate_row(pool, post_id, gate_name)
    if target is None:
        raise GateNotFoundError(
            f"No gate {gate_name!r} on post {post_id}"
        )

    if target["state"] not in _DOWNSTREAM_DECISIVE_STATES and target[
        "state"
    ] != GATE_STATE_REVISING:
        raise GateStateError(
            f"Gate {gate_name!r} on post {post_id} is in state "
            f"{target['state']!r}, nothing to reopen."
        )

    # Find later gates that have ALSO been decided.
    all_gates = await get_gates_for_post(pool, post_id)
    downstream_decided = [
        g for g in all_gates
        if g["ordinal"] > target["ordinal"]
        and g["state"] in _DOWNSTREAM_DECISIVE_STATES
    ]
    if downstream_decided and not cascade:
        raise GateCascadeRequiredError(
            f"Gate {gate_name!r} on post {post_id} has "
            f"{len(downstream_decided)} downstream decided gate(s) "
            f"({', '.join(g['gate_name'] for g in downstream_decided)}). "
            "Re-run with cascade=True to invalidate them."
        )

    # Flip target + (optionally) downstream rows in one tx.
    ids_to_reset: list[str] = [target["id"]]
    ids_to_reset.extend(g["id"] for g in downstream_decided)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE post_approval_gates
                   SET state = 'pending',
                       decided_at = NULL,
                       approver = NULL,
                       notes = NULL
                 WHERE id = ANY($1::uuid[])
                """,
                ids_to_reset,
            )
            updated = await conn.fetchrow(
                """
                SELECT id, post_id, gate_name, ordinal, state,
                       created_at, decided_at, approver, notes, metadata
                  FROM post_approval_gates
                 WHERE id = $1::uuid
                """,
                target["id"],
            )

    audit_log_bg(
        event_type="post_gate_reopened",
        source="post_approval_gates",
        details={
            "post_id": str(post_id),
            "gate_name": gate_name,
            "cascade": cascade,
            "downstream_invalidated": [g["gate_name"] for g in downstream_decided],
        },
        severity="info",
    )
    logger.info(
        "[gates] Reopened gate %s on post %s (cascade=%s, %d downstream invalidated)",
        gate_name, post_id, cascade, len(downstream_decided),
    )
    return _row_to_dict(updated)


# ---------------------------------------------------------------------------
# Failure escalation — per-medium retry budget
# ---------------------------------------------------------------------------


async def record_media_failure(
    pool: Any,
    post_id: str,
    medium: str,
    error_message: str,
    retry_limit: int,
) -> dict[str, Any]:
    """Increment the per-medium failure counter on the matching gate row.

    When the counter exceeds ``retry_limit``, insert a brand-new
    ``media_generation_failed`` gate at the next ordinal so the
    operator gets asked what to do.

    Returns a descriptor:
        {"escalated": bool, "attempts": int, "gate_id": str | None}
    """
    if medium not in MEDIUM_GATE_NAMES:
        raise ValueError(
            f"Unknown medium {medium!r}. Expected one of "
            f"{', '.join(MEDIUM_GATE_NAMES)}."
        )

    row = await _find_gate_row(pool, post_id, medium)
    # Update the counter on the existing gate row if present, otherwise
    # write a synthetic row with ordinal=last+1 so we don't lose the
    # signal entirely.
    if row is None:
        all_gates = await get_gates_for_post(pool, post_id)
        next_ord = (max((g["ordinal"] for g in all_gates), default=-1)) + 1
        async with pool.acquire() as conn:
            row_record = await conn.fetchrow(
                """
                INSERT INTO post_approval_gates
                    (post_id, gate_name, ordinal, state, metadata)
                VALUES ($1::uuid, $2, $3, 'pending', $4::jsonb)
                RETURNING id, post_id, gate_name, ordinal, state,
                          created_at, decided_at, approver, notes, metadata
                """,
                str(post_id), medium, next_ord,
                json.dumps({"failures": [error_message], "attempts": 1}),
            )
        row = _row_to_dict(row_record)

    metadata = dict(row.get("metadata") or {})
    failures = list(metadata.get("failures") or [])
    failures.append(error_message)
    attempts = int(metadata.get("attempts", 0)) + 1
    metadata["failures"] = failures
    metadata["attempts"] = attempts

    escalated = False
    escalation_id: Optional[str] = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE post_approval_gates SET metadata = $2::jsonb "
                "WHERE id = $1::uuid",
                row["id"], json.dumps(metadata),
            )
            if attempts > retry_limit:
                # Insert a media_generation_failed gate that the
                # operator must clear. Ordinal goes after the failed
                # medium's ordinal so workers see it next.
                escalation_meta = {
                    "failed_medium": medium,
                    "attempts": attempts,
                    "last_error": error_message,
                }
                escalation_row = await conn.fetchrow(
                    """
                    INSERT INTO post_approval_gates
                        (post_id, gate_name, ordinal, state, metadata)
                    VALUES ($1::uuid, 'media_generation_failed',
                            $2, 'pending', $3::jsonb)
                    ON CONFLICT (post_id, gate_name, ordinal) DO NOTHING
                    RETURNING id
                    """,
                    str(post_id), int(row["ordinal"]) + 1,
                    json.dumps(escalation_meta),
                )
                if escalation_row is not None:
                    escalated = True
                    escalation_id = str(escalation_row["id"])

    audit_log_bg(
        event_type="post_gate_media_failure",
        source="post_approval_gates",
        details={
            "post_id": str(post_id),
            "medium": medium,
            "attempts": attempts,
            "retry_limit": retry_limit,
            "escalated": escalated,
            "error_message": error_message,
        },
        severity="warning" if not escalated else "error",
    )
    logger.warning(
        "[gates] Media %s failure for post %s (attempt %d/%d, escalated=%s)",
        medium, post_id, attempts, retry_limit, escalated,
    )
    return {
        "escalated": escalated,
        "attempts": attempts,
        "gate_id": escalation_id,
    }


# ---------------------------------------------------------------------------
# Notification helper (deep links)
# ---------------------------------------------------------------------------


async def notify_gate_pending(
    *,
    post_id: str,
    gate_name: str,
    site_config: Optional[Any] = None,
    critical: bool = False,
) -> None:
    """Send the operator a "gate is waiting on you" notification.

    Builds a message containing both the web admin deep link and the
    CLI command, then routes through the existing
    :func:`services.integrations.operator_notify.notify_operator` shim
    — **always to Discord only** (per Glad-Labs/poindexter#338's
    notification-batching demotion + Matt's
    ``feedback_telegram_vs_discord.md`` rule: per-flip gate pings are
    routine progress, not a phone-pushing emergency).

    The ``critical`` parameter is kept on the signature for backwards
    compatibility with existing callers but is intentionally IGNORED —
    we hard-pin ``critical=False`` so the dispatcher routes to
    ``discord_ops``. Telegram pages about the gate queue come from the
    coalesced ``brain/gate_pending_summary_probe.py`` instead, which
    fires at most once per ``gate_pending_summary_telegram_dedup_minutes``
    when the queue is non-empty AND past the grace window.

    Caller responsibility: invoke whenever a gate transitions INTO
    ``pending`` state (initial create, regen-complete, reopen). Not
    invoked from inside the service mutators because the v1 design
    intentionally keeps notification side effects out of the
    transactional path. Best-effort — never raises.
    """
    # `critical` is accepted for backwards compatibility but ignored —
    # see docstring. Reference it once so linters don't flag it as
    # unused, then drop on the floor.
    del critical

    site_url = ""
    if site_config is not None:
        try:
            site_url = site_config.get("site_url", "") or ""
        except Exception:
            site_url = ""

    base = (site_url or "https://www.gladlabs.io").rstrip("/")
    deep_link = f"{base}/admin/posts/{post_id}?gate={gate_name}"
    cli_cmd = f"poindexter post approve {post_id} --gate {gate_name}"

    msg_lines = [
        f"[gate] Post {post_id[:8]} waiting at gate '{gate_name}'.",
        f"Web:  {deep_link}",
        f"CLI:  {cli_cmd}",
    ]
    msg = "\n".join(msg_lines)

    try:
        from services.integrations.operator_notify import notify_operator
        # Hard-pinned critical=False — Discord only. See #338.
        await notify_operator(msg, critical=False)
    except Exception as exc:
        logger.warning(
            "[gates] notify_gate_pending failed for post %s gate %s: %s",
            post_id, gate_name, exc,
        )


# ---------------------------------------------------------------------------
# Workflow advancement
# ---------------------------------------------------------------------------


async def advance_workflow(
    pool: Any, post_id: str
) -> WorkflowAdvance:
    """Decide what should happen next for ``post_id``.

    Always safe to call — purely a read of the gate state, no side
    effects. Caller is expected to route on the returned descriptor:

    - ``next_gate is not None`` → either fire the matching automatic
      stage (media generation for a podcast/video/short gate when the
      medium is in ``posts.media_to_generate``) or surface to operator.
    - ``ready_to_distribute=True`` → fire publish_service's
      post-publish hooks.
    - ``finished=True`` → no-op, post is done.
    """
    status = await _fetch_post_status(pool, post_id)
    if status is None:
        return WorkflowAdvance(finished=True, reason="post_not_found")
    if status == "rejected":
        return WorkflowAdvance(finished=True, reason="post_rejected")

    next_gate = await get_next_pending_gate(pool, post_id)
    if next_gate is None:
        # All gates decided. If anything was rejected we would have
        # short-circuited above. So we're clear to distribute.
        return WorkflowAdvance(
            ready_to_distribute=True, reason="all_gates_decided"
        )

    return WorkflowAdvance(next_gate=next_gate, reason="pending_gate")
