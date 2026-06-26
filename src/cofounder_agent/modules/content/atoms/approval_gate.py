"""``atoms.approval_gate`` — composable HITL pause-and-wait atom.

Phase 3 of the dynamic-pipeline-composition spec. Wraps the existing
:class:`modules.content.stages.approval_gate.ApprovalGateStage` logic in atom
shape so the architect-LLM can drop a gate at any point in a composed
graph without subclassing.

How "interrupt" works (true LangGraph interrupt(), #363)
--------------------------------------------------------

When the gate is open, this atom:

1. Persists the gate state via
   :func:`services.approval_service.pause_at_gate` (writes
   ``pipeline_tasks.awaiting_gate`` / ``gate_artifact`` / ``gate_paused_at``
   and fires the operator notification).
2. Sends a CRITICAL operator notification (Telegram) with the gate context
   so the pause reaches Matt's phone.
3. Calls :func:`langgraph.types.interrupt` — which raises ``GraphInterrupt``.
   LangGraph catches that bubble-up, **durably checkpoints the entire graph
   state** (via the Postgres checkpointer keyed on ``thread_id`` = task_id),
   and pauses ``ainvoke`` mid-execution.

The graph does NOT re-run earlier nodes on resume — that's the whole point
versus the old status-polling / re-run design. The operator approves
(``poindexter pipeline resume <task_id>`` or ``poindexter approve``), the
runner calls ``ainvoke(Command(resume=...), config)`` with the SAME
``thread_id``, and LangGraph re-enters THIS atom. On re-entry ``interrupt()``
returns the resume value instead of raising, so the atom passes through.

Two resume patterns are handled defensively:

- **Command(resume=...) re-entry** — ``interrupt()`` returns the resume
  payload; we treat any returned value as approval and pass through.
- **gate_history approved row** — even before re-reaching ``interrupt()``,
  the approved-row check below short-circuits to pass-through. This covers
  the legacy re-run path and any caller that records approval before
  resuming. A ``rejected`` row halts the graph instead.

Issue: Glad-Labs/poindexter#363.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import interrupt

from plugins.atom import AtomMeta, FieldSpec, RetryPolicy

logger = logging.getLogger(__name__)


ATOM_META = AtomMeta(
    name="atoms.approval_gate",
    type="approval_gate",
    version="2.0.0",
    description=(
        "Pause pipeline execution pending operator approval at a named gate "
        "via LangGraph interrupt(). On open: persists gate state, notifies the "
        "operator (critical/Telegram), and calls interrupt() so the graph "
        "durably checkpoints and pauses. On resume (Command(resume=...) or an "
        "approved gate_history row): passes through. A rejected row halts."
    ),
    inputs=(
        FieldSpec(
            name="task_id", type="str",
            description="Task UUID — required to write awaiting_gate column.",
            required=True,
        ),
        FieldSpec(
            name="gate_name", type="str",
            description=(
                "Stable gate slug, e.g. 'draft_gate', 'preview_approval'. "
                "Lands in pipeline_tasks.awaiting_gate. May be seeded from the "
                "spec node's static config."
            ),
            required=True,
        ),
        FieldSpec(
            name="database_service", type="DatabaseService",
            description="DB pool source — needed for pause_at_gate write.",
            required=True,
        ),
        FieldSpec(
            name="site_config", type="SiteConfig",
            description="Used to look up pipeline_gate_<gate_name> enable flag.",
            required=False,
        ),
    ),
    outputs=(
        FieldSpec(
            name="_halt", type="bool",
            description="Set to True only on rejection — TemplateRunner halts.",
        ),
        FieldSpec(
            name="awaiting_gate", type="str",
            description="Same gate_name, surfaced for operator dashboards.",
        ),
        FieldSpec(
            name="gate_artifact", type="dict",
            description="The artifact the operator reviewed.",
        ),
    ),
    requires=("task_id", "gate_name"),
    produces=("_halt", "awaiting_gate", "gate_artifact"),
    capability_tier=None,
    cost_class="free",
    idempotent=True,
    side_effects=(
        "writes pipeline_tasks.awaiting_gate",
        "calls notify_operator (critical)",
        "calls langgraph interrupt() — checkpoints + pauses the graph",
    ),
    retry=RetryPolicy(max_attempts=1),
    fallback=(),
    parallelizable=False,
)


async def run(state: dict[str, Any]) -> dict[str, Any]:
    """Atom entry point.

    Reads ``state['gate_name']`` (mandatory; may be seeded from the spec
    node's config) plus optional ``state['gate_artifact_keys']`` (list of
    state keys to surface in the operator review payload).

    Flow:
      1. Disabled gate → pass-through ``{}`` (prod-safe default).
      2. ``rejected`` gate_history row → ``{"_halt": True}``.
      3. ``approved`` gate_history row → pass-through (resume case).
      4. Otherwise → persist gate state, notify (critical), and
         ``interrupt()``. On resume, ``interrupt()`` returns the resume
         value and we pass through.
    """
    from services.approval_service import is_gate_enabled, pause_at_gate

    gate_name = state.get("gate_name") or ""
    if not gate_name:
        logger.warning("[atoms.approval_gate] missing gate_name in state — passthrough")
        return {}

    site_config = state.get("site_config")
    task_id = state.get("task_id")
    if not task_id:
        logger.warning("[atoms.approval_gate:%s] missing task_id — passthrough", gate_name)
        return {}

    # Master enable check — passthrough when the operator has the gate
    # turned off in app_settings (pipeline_gate_<gate_name>). This is what
    # keeps prod canonical_blog runs unaffected: the gate is seeded DISABLED.
    if not is_gate_enabled(gate_name, site_config):
        logger.info("[atoms.approval_gate:%s] disabled — passthrough", gate_name)
        return {}

    pool = _resolve_pool(state)

    # Gate-history short-circuit. ``rejected`` halts; ``approved`` passes
    # through (the legacy re-run resume path, and any caller that records
    # approval before resuming the graph).
    if pool is not None:
        decision = await _gate_decision(pool, str(task_id), gate_name)
        if decision == "rejected":
            logger.info(
                "[atoms.approval_gate:%s] rejected on prior pass — halting",
                gate_name,
            )
            return {
                "_halt": True,
                "_halt_reason": f"gate {gate_name!r} rejected by operator",
            }
        # Pending-regen short-circuit (preview_gate component regen). Read the
        # one-shot pending flag BEFORE pausing so a regen reroutes the graph's
        # backward loop edge WITHOUT re-paging the operator; clear the flag
        # (consume) so the loop-back finds it false and falls through to a
        # single fresh review pause. Outranks a stale approval — the operator
        # asking for a redo is newer intent than an earlier approve.
        pending = await _pending_regen(pool, str(task_id))
        if pending is not None:
            out = _regen_output(pending, state.get("regen_targets") or {})
            if "_goto" in out:
                # Read before consume — steering is in pipeline_gate_history
                # (independent of the pipeline_tasks flag), but reading first
                # is semantically cleaner.
                regen_steering = await _read_regen_steering(
                    pool, str(task_id), gate_name,
                )
                await _consume_regen(pool, str(task_id), pending)
                if regen_steering:
                    out["regen_steering"] = regen_steering
                logger.info(
                    "[atoms.approval_gate:%s] regen_%s consumed → _goto %s "
                    "(steering=%s)",
                    gate_name, pending, out["_goto"],
                    repr(regen_steering[:40]) if regen_steering else "none",
                )
            else:
                logger.error(
                    "[atoms.approval_gate:%s] regen_%s pending but no target "
                    "configured — halting",
                    gate_name, pending,
                )
            return out
        if decision == "approved":
            logger.info(
                "[atoms.approval_gate:%s] already approved — passthrough",
                gate_name,
            )
            return {}

    # Build the operator-review artifact. Keys to surface come from
    # ``state['gate_artifact_keys']`` (list[str]) when set; otherwise
    # we surface a minimal default summary.
    artifact_keys = state.get("gate_artifact_keys") or [
        "topic", "title", "excerpt", "quality_score", "featured_image_url",
    ]
    artifact: dict[str, Any] = {"task_id": str(task_id), "gate": gate_name}
    for key in artifact_keys:
        if key in state and state[key] not in (None, "", [], {}):
            artifact[key] = state[key]

    if pool is None:
        logger.error("[atoms.approval_gate:%s] no DB pool — cannot pause", gate_name)
        return {
            "_halt": True,
            "_halt_reason": (
                f"approval_gate {gate_name!r}: no DB pool on state — pipeline "
                "cannot be paused for review"
            ),
        }

    try:
        await pause_at_gate(
            task_id=str(task_id),
            gate_name=gate_name,
            artifact=artifact,
            site_config=site_config,
            pool=pool,
            notify=True,
        )
    except Exception as exc:
        logger.exception(
            "[atoms.approval_gate:%s] pause_at_gate raised: %s",
            gate_name, exc,
        )
        return {
            "_halt": True,
            "_halt_reason": (
                f"approval_gate {gate_name!r} pause_at_gate failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    # Critical (Telegram) page so the pause reaches Matt's phone — the
    # routine pause_at_gate notification above goes to Discord
    # (critical=False), so this adds the high-urgency hit.
    await _notify_critical(task_id=str(task_id), gate_name=gate_name, artifact=artifact, site_config=site_config)

    rendered_message = (
        f"Pipeline paused at gate {gate_name!r} (task {str(task_id)[:8]}). "
        f"Approve: poindexter pipeline resume {task_id}"
    )

    # The pivot: interrupt() raises GraphInterrupt, LangGraph durably
    # checkpoints the whole graph (keyed on thread_id=task_id) and pauses
    # ainvoke. On resume via ainvoke(Command(resume=...), config) the SAME
    # interrupt() call RETURNS the resume value instead of raising — so we
    # treat any returned value as approval and pass through. The
    # GraphInterrupt must NOT be caught here (or by _wrap_atom /
    # make_stage_node) — it is the pause signal, not a failure.
    resume_value = interrupt(
        {
            "gate_name": gate_name,
            "task_id": str(task_id),
            "message": rendered_message,
            "artifact": artifact,
        }
    )

    logger.info(
        "[atoms.approval_gate:%s] resumed (resume_value=%r) — passthrough",
        gate_name, resume_value,
    )
    return {}


def _resolve_pool(state: dict[str, Any]) -> Any:
    db = state.get("database_service")
    return getattr(db, "pool", None) if db else None


async def _notify_critical(
    *,
    task_id: str,
    gate_name: str,
    artifact: dict[str, Any],
    site_config: Any,
) -> None:
    """Best-effort critical (Telegram) page that the graph is paused.

    pause_at_gate already fires a Discord (critical=False) notification;
    this adds the phone hit per the gate's HITL intent. Never raises.
    """
    try:
        from services.integrations.operator_notify import notify_operator
        title = artifact.get("title") or artifact.get("topic") or ""
        msg = (
            f"[approval gate] Task {task_id[:8]} paused at gate '{gate_name}'.\n"
            f"{('title: ' + str(title)[:100]) if title else '(no title yet)'}\n"
            f"Resume: poindexter pipeline resume {task_id}"
        )
        await notify_operator(msg, critical=True, site_config=site_config)
    except Exception as exc:  # noqa: BLE001 — notifications are best-effort
        logger.debug("[atoms.approval_gate:%s] critical notify failed: %s", gate_name, exc)


async def _gate_decision(pool: Any, task_id: str, gate_name: str) -> str | None:
    """Return the latest gate decision for ``(task_id, gate_name)``.

    Reads ``pipeline_gate_history`` (not just ``pipeline_tasks.awaiting_gate``,
    which is also NULL for tasks that never hit the gate) for the most-recent
    typed event. Returns ``"approved"``, ``"rejected"``, or ``None`` when the
    gate has no recorded (or no *current*) decision yet.

    A ``rejected`` event_kind covers both ``rejected`` and any per-gate reject
    status (``dismissed`` etc.) — anything that is not ``approved`` and not a
    bare pause is treated as a halt signal.

    **Freshness check (resume-atomicity c2).** An ``approved`` row is only
    honored when it was granted for the task's *current* run attempt. The
    stale-inprogress sweep (``services.tasks_db.sweep_stale_tasks``) resets a
    crashed resume to ``pending`` and bumps ``retry_count`` while CLEARING the
    LangGraph checkpoint — but it does NOT touch ``pipeline_gate_history``. On
    the fresh re-run a *stale* approval (granted at a lower attempt) must NOT
    auto-pass the gate, or regenerated content would publish with no operator
    review. ``approve`` stamps the granting ``retry_count`` into the row's
    ``metadata.approved_at_retry_count``; we compare it to the task's current
    ``retry_count`` and treat a mismatch as "no decision" (→ re-pause). A
    legacy row with no tag (``NULL``) is honored for backcompat.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT gh.event_kind,
                       (gh.metadata ->> 'approved_at_retry_count')
                           AS approved_at_retry_count,
                       pt.retry_count AS current_retry_count
                  FROM pipeline_gate_history gh
                  JOIN pipeline_tasks pt ON pt.task_id::text = gh.task_id
                 WHERE gh.task_id = $1
                   AND gh.gate_name = $2
                   AND gh.event_kind IN ('approved', 'rejected', 'dismissed')
                 ORDER BY gh.created_at DESC
                 LIMIT 1
                """,
                str(task_id), gate_name,
            )
        if row is None:
            return None
        kind = row["event_kind"]
        if kind != "approved":
            return "rejected"

        approved_attempt = row.get("approved_at_retry_count")
        current_attempt = row.get("current_retry_count")
        if (
            approved_attempt is not None
            and current_attempt is not None
            and str(approved_attempt) != str(current_attempt)
        ):
            logger.info(
                "[atoms.approval_gate:%s] stale approval (granted at attempt %s, "
                "task now at attempt %s) — ignoring, will re-pause for review",
                gate_name, approved_attempt, current_attempt,
            )
            return None
        return "approved"
    except Exception as exc:  # noqa: BLE001
        logger.debug("[atoms.approval_gate] gate-decision check failed: %s", exc)
        return None


def _regen_output(component: str, regen_targets: dict[str, Any]) -> dict[str, Any]:
    """Map a pending regen component to this atom's output.

    A configured target → ``{"_goto": <node_id>}`` so the compiler's branch
    router routes the backward loop edge to the image/writer block. A MISSING
    target is a graph misconfiguration (the node's ``regen_targets`` config is
    absent/incomplete): fail loud with ``_halt`` rather than silently passing
    unreviewed content forward.
    """
    target = (regen_targets or {}).get(component)
    if not target:
        return {
            "_halt": True,
            "_halt_reason": (
                f"preview_gate: regen_{component} pending but no regen target "
                f"configured for {component!r} (node config 'regen_targets')"
            ),
        }
    return {"_goto": target}


async def _pending_regen(pool: Any, task_id: str) -> str | None:
    """Return ``"images"``/``"text"`` for an unconsumed regen request, else None.

    Reads the one-shot ``pipeline_tasks.regen_<c>_pending`` flags the operator
    surface (``approval_service.regen_at_gate``) sets. Images win a tie (the
    common "bad image, good text" case). Any error — the columns not present yet
    (pre-migration) or a bad id — means "no regen" → ``None`` → normal pause.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT regen_images_pending, regen_text_pending
                  FROM pipeline_tasks
                 WHERE task_id::text = $1
                """,
                str(task_id),
            )
        if row is None:
            return None
        if row.get("regen_images_pending"):
            return "images"
        if row.get("regen_text_pending"):
            return "text"
        return None
    except Exception as exc:  # noqa: BLE001 — missing columns / bad id → no regen
        logger.debug("[atoms.approval_gate] pending-regen check failed: %s", exc)
        return None


async def _consume_regen(pool: Any, task_id: str, component: str) -> None:
    """Clear the one-shot ``regen_<component>_pending`` flag (consume the regen).

    Once cleared, the loop-back re-entry sees ``pending=false`` and falls through
    to a single fresh review pause instead of re-honoring the same request. The
    monotonic ``regen_<c>_attempts`` counter is owned by the surface, not here.
    """
    col = "regen_images_pending" if component == "images" else "regen_text_pending"
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE pipeline_tasks SET {col} = false WHERE task_id::text = $1",
            str(task_id),
        )


async def _read_regen_steering(pool: Any, task_id: str, gate_name: str) -> str | None:
    """Read the operator's regen reason from the latest gate_history row (#149).

    ``approval_service.regen_at_gate`` writes the operator's ``--reason`` to
    ``pipeline_gate_history.feedback`` with event_kind ``'regen_text'`` or
    ``'regen_draft'``. Here we read it back so the regen backward-loop can
    inject it into ``PipelineState`` as ``'regen_steering'`` — a key the
    writer stage reads and prepends to its prompt override so the next draft
    directly addresses the operator's note.

    Best-effort: returns ``None`` on any error or when the feedback is empty
    (bare regen with no reason). A ``None`` result is safe — the writer falls
    back to the un-steered prompt, exactly as before #149.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT feedback FROM pipeline_gate_history
                 WHERE task_id = $1
                   AND gate_name = $2
                   AND event_kind IN ('regen_text', 'regen_draft')
                   AND feedback != ''
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                str(task_id), gate_name,
            )
        return row["feedback"] if row else None
    except Exception as exc:  # noqa: BLE001  # silent-ok: best-effort steering read — pool errors must not affect the regen path
        logger.debug("[atoms.approval_gate] regen steering read failed: %s", exc)
        return None


__all__ = ["ATOM_META", "run"]
