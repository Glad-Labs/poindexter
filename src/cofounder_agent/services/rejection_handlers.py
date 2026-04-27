"""Per-gate rejection handlers — turn operator rejections into learning signals (#148).

When an operator rejects a HITL gate (``poindexter reject <id>`` or
``poindexter reject-publish <id>``), the existing approval services
clear the gate columns, set a status, and write an audit row. That's
correct as a side-effect-free baseline. But the *meaning* of a
rejection is gate-specific:

- A rejected **topic** is just thrown out; the brain should learn
  not to suggest that kind of topic again.
- A rejected **preview** (early draft) means redo the draft with the
  operator's reason as steering — the topic is still good.
- A rejected **final publish** means the writing is fine but the
  *media* (featured image, video clips, podcast audio) needs a redo.

A single ``status='rejected'`` flag throws all three into the same
bucket. This module wires per-gate handlers that fire async after
the rejection commits so:

- Brain sees a weight-down signal it can use in topic ranking.
- Pipeline runner sees a regen event with the reason in its payload.
- Operator gets useful learning instead of a dead-end audit row.

Architecture
------------

A small registry of handlers, keyed by gate name. Default = "no
extra action" (today's behavior). Override by calling
:func:`register_handler` at module import time. Each handler:

- Is async and must not raise (failures log + continue).
- Receives ``RejectionContext`` (gate name, task_id/post_id, reason,
  artifact, pool, site_config).
- Decides what side effects to fire — usually one of:
  * insert a row into ``brain_knowledge``,
  * insert a row into ``pipeline_events`` with a regen event type,
  * insert a row into ``audit_log`` with a richer payload.

Retry caps are enforced *here*: each handler that triggers a regen
checks ``approval_gate_<gate>_max_retries`` (default 2) before
emitting the regen event. Without this, a degenerate "always
reject" loop would burn the GPU forever.

The dispatcher (:func:`dispatch_rejection`) is what ``approval_service``
and ``posts_approval_service`` call. Failures inside a handler get
logged and swallowed — never raised — because the rejection itself
already succeeded; a handler crash shouldn't make the operator's CLI
exit non-zero.

Why a separate module
---------------------

Keeps the approval services small and focused. They handle the gate
state machine; this module handles the *meaning* of a state
transition. New gate types add a handler here without touching the
state machine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from services.audit_log import audit_log_bg
from services.logger_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# RejectionContext — payload passed into each handler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RejectionContext:
    """Everything a handler might need.

    Frozen because handlers should never mutate the rejection facts —
    they only emit follow-up actions. If a handler wants per-call
    state, it stashes it in its own closure or a shared service.
    """

    gate_name: str
    task_id: str | None        # set for content_tasks gates (#145, #146)
    post_id: str | None        # set for posts gates (final_publish_approval)
    reason: str | None         # operator's free-text veto note
    artifact: dict[str, Any]   # the gate_artifact at time of rejection
    pool: Any                  # asyncpg pool
    site_config: Any           # SiteConfig (DI seam)

    def primary_id(self) -> str:
        """Return whichever ID is set — both ``task_id`` and ``post_id``
        won't be set in the same call."""
        return self.task_id or self.post_id or ""


# Async-callable signature: ctx → None. Must not raise.
RejectionHandler = Callable[[RejectionContext], Awaitable[None]]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, RejectionHandler] = {}


def register_handler(gate_name: str, handler: RejectionHandler) -> None:
    """Register a handler for a gate. Last write wins.

    Plugin authors should call this at module import time so the
    handler is in place before the first rejection fires. The
    registry is process-local — distributed deployments need each
    worker to register its own handlers (typically by importing this
    module and the handler module's side effects).
    """
    _HANDLERS[gate_name] = handler


def get_handler(gate_name: str) -> RejectionHandler | None:
    return _HANDLERS.get(gate_name)


def list_registered_handlers() -> list[str]:
    """Return the gate names that have registered handlers."""
    return sorted(_HANDLERS.keys())


# ---------------------------------------------------------------------------
# Dispatcher — what approval services call
# ---------------------------------------------------------------------------


async def dispatch_rejection(ctx: RejectionContext) -> None:
    """Fire the handler registered for ``ctx.gate_name``, if any.

    Safe to call from any approval-service path. Never raises;
    handler failures are logged and audited so the next operator can
    see what went wrong without the CLI exiting non-zero on the
    successful rejection.
    """
    handler = _HANDLERS.get(ctx.gate_name)
    if handler is None:
        logger.debug(
            "[rejection_handlers] no handler registered for gate=%s — "
            "rejection commits with default behavior only",
            ctx.gate_name,
        )
        return

    try:
        await handler(ctx)
    except Exception as exc:
        logger.exception(
            "[rejection_handlers] handler for gate=%s failed: %s",
            ctx.gate_name, exc,
        )
        audit_log_bg(
            event_type="rejection_handler_failed",
            source="rejection_handlers",
            details={
                "gate_name": ctx.gate_name,
                "primary_id": ctx.primary_id(),
                "error": f"{type(exc).__name__}: {exc}",
            },
            severity="error",
        )


# ---------------------------------------------------------------------------
# Helpers used by the bundled handlers
# ---------------------------------------------------------------------------


async def _retry_count(pool: Any, primary_id: str, event_type: str) -> int:
    """How many times has this regen event already fired for this row?

    Uses ``pipeline_events`` as the source of truth — every regen
    write goes there with the primary_id in the payload. A separate
    counter table would be more explicit but adds a schema +
    maintenance burden for one boolean question.
    """
    try:
        count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM pipeline_events
             WHERE event_type = $1
               AND payload ->> 'primary_id' = $2
            """,
            event_type, primary_id,
        )
        return int(count or 0)
    except Exception as exc:
        logger.warning(
            "[rejection_handlers] retry-count query failed (%s) — "
            "assuming 0",
            exc,
        )
        return 0


def _max_retries(site_config: Any, gate_name: str, default: int = 2) -> int:
    """Read ``approval_gate_<gate>_max_retries`` with a numeric fallback."""
    if site_config is None:
        return default
    try:
        raw = site_config.get(f"approval_gate_{gate_name}_max_retries", default)
        n = int(str(raw).strip())
        return n if n >= 0 else default
    except (ValueError, TypeError):
        return default


async def _emit_event(
    pool: Any, event_type: str, payload: dict[str, Any],
) -> None:
    """Insert a ``pipeline_events`` row. Logs + swallows failures."""
    try:
        await pool.execute(
            """
            INSERT INTO pipeline_events (event_type, payload)
            VALUES ($1, $2::jsonb)
            """,
            event_type,
            json.dumps(payload, default=str),
        )
    except Exception as exc:
        logger.warning(
            "[rejection_handlers] emit %s failed: %s", event_type, exc,
        )


async def _write_brain_signal(
    pool: Any,
    *,
    entity: str,
    attribute: str,
    value: str,
    confidence: float,
    source: str,
) -> None:
    """Write a row to ``brain_knowledge`` so the brain's topic-ranking
    logic picks it up on the next cycle. Best-effort — failure is
    logged but not raised."""
    try:
        await pool.execute(
            """
            INSERT INTO brain_knowledge
                (entity, attribute, value, confidence, source, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT DO NOTHING
            """,
            entity, attribute, value, confidence, source,
        )
    except Exception as exc:
        logger.warning(
            "[rejection_handlers] brain_knowledge write failed "
            "(entity=%s, attr=%s): %s",
            entity, attribute, exc,
        )


# ---------------------------------------------------------------------------
# Bundled handlers
# ---------------------------------------------------------------------------


async def topic_decision_handler(ctx: RejectionContext) -> None:
    """Topic rejected → brain learns to weight that topic-cluster down.

    Writes one ``brain_knowledge`` row tagged ``topic_rejection`` with
    the operator's reason so the brain's next topic-ranking pass can
    pull it via ``search_memory`` and bias against similar topics.
    No regen event — the topic is dead, not redo-able.
    """
    title = ctx.artifact.get("title") or ctx.artifact.get("topic") or ""
    if not title:
        logger.debug(
            "[topic_decision_handler] artifact has no title/topic — "
            "skipping brain write (id=%s)", ctx.primary_id(),
        )
        return

    reason_text = ctx.reason or "(no reason provided)"
    await _write_brain_signal(
        ctx.pool,
        entity=f"topic:{title[:200]}",
        attribute="rejected_by_operator",
        value=reason_text[:500],
        # Confidence high — operator rejection is direct signal, not inferred.
        confidence=0.9,
        source="rejection_handler.topic_decision",
    )

    audit_log_bg(
        event_type="rejection_handler_topic_decision",
        source="rejection_handlers",
        details={
            "gate_name": ctx.gate_name,
            "task_id": ctx.task_id,
            "title": title[:120],
            "reason": reason_text[:500],
        },
        task_id=ctx.task_id,
        severity="info",
    )


async def preview_approval_handler(ctx: RejectionContext) -> None:
    """Preview rejected → enqueue draft regen with reason as steering.

    Topic is still good; only the draft was off. Emits
    ``task.regen_draft`` with the operator's reason in the payload so
    the pipeline runner re-runs from the draft Stage with steering
    feedback. Honors ``approval_gate_preview_approval_max_retries``
    (default 2) so a stuck rewriting loop can't burn the GPU forever.
    """
    if not ctx.task_id:
        logger.warning(
            "[preview_approval_handler] no task_id in context — "
            "cannot emit regen event"
        )
        return

    cap = _max_retries(ctx.site_config, ctx.gate_name)
    prior = await _retry_count(ctx.pool, ctx.task_id, "task.regen_draft")
    if prior >= cap:
        logger.info(
            "[preview_approval_handler] task=%s already retried %d times "
            "(cap=%d) — escalating to dismissed instead of regen",
            ctx.task_id[:8], prior, cap,
        )
        audit_log_bg(
            event_type="rejection_handler_retry_cap_hit",
            source="rejection_handlers",
            details={
                "gate_name": ctx.gate_name,
                "task_id": ctx.task_id,
                "prior_retries": prior,
                "cap": cap,
            },
            task_id=ctx.task_id,
            severity="warning",
        )
        return

    payload = {
        "primary_id": ctx.task_id,
        "task_id": ctx.task_id,
        "gate_name": ctx.gate_name,
        "feedback": ctx.reason or "",
        "retry_n": prior + 1,
    }
    await _emit_event(ctx.pool, "task.regen_draft", payload)
    audit_log_bg(
        event_type="rejection_handler_preview_approval",
        source="rejection_handlers",
        details={
            "gate_name": ctx.gate_name,
            "task_id": ctx.task_id,
            "retry_n": prior + 1,
            "feedback": ctx.reason or "",
        },
        task_id=ctx.task_id,
        severity="info",
    )


async def final_publish_approval_handler(ctx: RejectionContext) -> None:
    """Final publish rejected → enqueue media regen.

    Text + SEO are fine; only the media (featured image, video clips,
    podcast audio) needs a redo. Emits ``task.regen_media`` with the
    operator's reason so the upload_to_platform Stage rebuilds from
    media_assets only — does NOT redo the writing pipeline.

    Honors ``approval_gate_final_publish_approval_max_retries``
    (default 2). After cap, leaves the post at status='rejected' so
    the operator can manually intervene.
    """
    if not ctx.post_id:
        logger.warning(
            "[final_publish_approval_handler] no post_id — "
            "cannot emit regen event"
        )
        return

    cap = _max_retries(ctx.site_config, ctx.gate_name)
    prior = await _retry_count(ctx.pool, ctx.post_id, "task.regen_media")
    if prior >= cap:
        logger.info(
            "[final_publish_approval_handler] post=%s already retried %d "
            "times (cap=%d) — leaving as rejected",
            ctx.post_id[:8], prior, cap,
        )
        audit_log_bg(
            event_type="rejection_handler_retry_cap_hit",
            source="rejection_handlers",
            details={
                "gate_name": ctx.gate_name,
                "post_id": ctx.post_id,
                "prior_retries": prior,
                "cap": cap,
            },
            severity="warning",
        )
        return

    payload = {
        "primary_id": ctx.post_id,
        "post_id": ctx.post_id,
        "gate_name": ctx.gate_name,
        "feedback": ctx.reason or "",
        "retry_n": prior + 1,
    }
    await _emit_event(ctx.pool, "task.regen_media", payload)
    audit_log_bg(
        event_type="rejection_handler_final_publish_approval",
        source="rejection_handlers",
        details={
            "gate_name": ctx.gate_name,
            "post_id": ctx.post_id,
            "retry_n": prior + 1,
            "feedback": ctx.reason or "",
        },
        severity="info",
    )


# ---------------------------------------------------------------------------
# Side-effect: register the bundled handlers at import time
# ---------------------------------------------------------------------------

register_handler("topic_decision", topic_decision_handler)
register_handler("preview_approval", preview_approval_handler)
register_handler("final_publish_approval", final_publish_approval_handler)


__all__ = [
    "RejectionContext",
    "RejectionHandler",
    "dispatch_rejection",
    "get_handler",
    "list_registered_handlers",
    "register_handler",
    # Bundled handlers — exported for direct test/import use.
    "topic_decision_handler",
    "preview_approval_handler",
    "final_publish_approval_handler",
]
