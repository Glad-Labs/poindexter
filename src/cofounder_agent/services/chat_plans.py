"""Architect plan cards — create, one-shot run (poindexter#950).

The chat agent's ``plan_pipeline`` tool calls
``pipeline_architect.compose`` and hands the validated spec here:

1. :func:`create_plan` **namespaces** the spec's name so the derived
   ``pipeline_templates`` slug always starts ``plan_`` — ``cache_template``
   upserts by slug, so an LLM naming its spec "canonical blog" would
   otherwise OVERWRITE the production template. Then it caches the spec
   (fingerprint-stamped, ``active=true`` — exactly what
   ``load_active_graph_def`` needs at run time) and inserts the durable
   card row.
2. :func:`run_plan` resolves atomically (``status='draft'`` → ``'ran'``,
   the ``chat_approvals`` one-shot pattern), creates the ``pipeline_tasks``
   row with ``template_slug`` = the cached slug (Prefect claims it like any
   other pending task; the runner loads the composed graph_def), links the
   task to the conversation (P3 watch machinery takes over), stamps the
   card part, and appends the outcome as a system message.

Adjust needs no server state: the operator's feedback goes back through
the conversation and the model composes a NEW plan.

Deliberately no semantic-dedup guard on plan runs: an architect-composed
run is an explicit, hand-designed operator action, not a bulk create path.
"""

from __future__ import annotations

import json
import re
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any

from services import chat_conversation_store as store
from services.logger_config import get_logger

logger = get_logger(__name__)

_SLUG_PREFIX = "plan_"


def namespace_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy whose name yields a ``plan_``-prefixed slug."""
    out = dict(spec)
    name = (out.get("name") or "architect plan").strip()
    if not name.lower().startswith(_SLUG_PREFIX):
        out["name"] = f"{_SLUG_PREFIX}{name}"
    return out


# A graph that never transitions the task off ``in_progress`` can NEVER
# complete: the run finishes its nodes, the task row stays in_progress,
# the 30-min stale sweep re-queues it, and the pipeline re-runs until the
# retry cap (first live plan batch: 2 of 3 architect graphs looped this
# way). Statuses the sweep leaves alone — a set_task_status node only
# counts as terminal when it TARGETS one of these (the first live media
# plan carried a mid-graph ``target_status='in_progress'`` marker that
# satisfied a name-only check while terminating nothing).
_TERMINAL_STATUSES = frozenset({
    "awaiting_approval", "completed", "approved", "published",
    "failed", "rejected", "rejected_final", "cancelled", "dismissed",
})


def _has_terminal_node(nodes: list[dict[str, Any]]) -> bool:
    for n in nodes:
        atom = n.get("atom") or ""
        if atom == "stage.finalize_task":
            return True
        if atom == "atoms.set_task_status":
            target = str((n.get("config") or {}).get("target_status") or "")
            if target in _TERMINAL_STATUSES:
                return True
    return False


def ensure_terminal(spec: dict[str, Any]) -> dict[str, Any]:
    """Return a copy guaranteed to end in a status-setting node.

    When the composed spec lacks a node that sets a TERMINAL status,
    append ``atoms.set_task_status`` (``target_status='awaiting_approval'``
    — the reviewable landing state; approve≠publish still applies) wired
    from every sink node. Deterministic code, not LLM trust: the architect
    is free to design any pipeline shape, but a plan run must always land
    somewhere the operator can see.
    """
    nodes = list(spec.get("nodes") or [])
    if not nodes:
        return spec
    if _has_terminal_node(nodes):
        return spec
    out = dict(spec)
    edges = [dict(e) for e in (spec.get("edges") or [])]
    node_ids = [n.get("id") for n in nodes if n.get("id")]
    sources = {e.get("from") for e in edges}
    sinks = [nid for nid in node_ids if nid not in sources] or node_ids[-1:]
    term_id = "ensure_terminal_status"
    nodes = nodes + [{
        "id": term_id,
        "atom": "atoms.set_task_status",
        "config": {"target_status": "awaiting_approval"},
    }]
    edges = edges + [{"from": sink, "to": term_id} for sink in sinks]
    out["nodes"] = nodes
    out["edges"] = edges
    return out


async def create_plan(
    pool: Any,
    *,
    conversation_id: str,
    message_id: str,
    intent: str,
    topic: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Cache the composed spec and insert the plan row; returns card fields."""
    from services.pipeline_architect import cache_template

    safe_spec = ensure_terminal(namespace_spec(spec))
    slug = await cache_template(pool, safe_spec)
    if not slug.startswith(_SLUG_PREFIX):
        # cache_template normalizes the name itself; a prefix that didn't
        # survive means the namespace guard is broken — refuse rather than
        # risk shadowing a seeded template.
        raise RuntimeError(
            f"plan slug {slug!r} escaped the {_SLUG_PREFIX!r} namespace"
        )
    nodes = [n.get("id") or n.get("atom") or "?" for n in safe_spec.get("nodes") or []]
    row = await pool.fetchrow(
        """
        INSERT INTO chat_plans
            (conversation_id, message_id, intent, topic, template_slug, spec)
        VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)
        RETURNING id
        """,
        conversation_id, message_id, intent[:2000], topic[:200], slug,
        json.dumps(safe_spec, default=str),
    )
    return {
        "plan_id": str(row["id"]),
        "slug": slug,
        "intent": intent[:500],
        "topic": topic[:200],
        "node_count": len(nodes),
        "nodes": nodes,
    }


async def get_plan(pool: Any, plan_id: str) -> dict[str, Any] | None:
    row = await pool.fetchrow(
        "SELECT * FROM chat_plans WHERE id = $1::uuid", plan_id,
    )
    if row is None:
        return None
    d = dict(row)
    d["id"] = str(d["id"])
    d["conversation_id"] = str(d["conversation_id"])
    d["message_id"] = str(d["message_id"])
    if isinstance(d.get("spec"), str):
        d["spec"] = json.loads(d["spec"])
    for k in ("created_at", "resolved_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


# Per-run parameter hygiene. Params ride task metadata, which
# content_router_service flattens onto the initial pipeline state (the
# seo_refresh post_id seam) — so a plan whose entry atom requires e.g.
# ``post_id`` receives it without the architect having to know a concrete
# id at design time. Reserved keys guard the task's own envelope.
_PARAM_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RESERVED_PARAM_KEYS = frozenset({
    "created_via", "chat_plan_id", "plan_intent", "conversation_id",
    "task_id", "template_slug", "topic", "status", "id", "user_id",
})
_MAX_PARAMS = 10
_MAX_PARAM_VALUE_CHARS = 500


def infer_task_type(spec: dict[str, Any] | None) -> str:
    """Derive the honest ``pipeline_tasks.task_type`` from a plan's atoms.

    P4 hardcoded ``blog_post`` for every plan run; the first podcast plan
    then rendered in the console as an empty blog draft with no way to
    reach the audio (live operator report). The composition already says
    what the run produces — read it. Precedence: video beats podcast
    (a video plan usually also narrates), podcast beats blog.
    """
    atoms = [
        str(n.get("atom") or "")
        for n in (spec or {}).get("nodes") or []
    ]
    if any(a in ("media.render_long_video", "media.render_short_video")
           for a in atoms):
        return "video"
    if any(a.startswith("podcast.") for a in atoms):
        return "podcast"
    return "blog_post"


def validate_run_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Return sanitized per-run params or raise ``ValueError`` (fail loud)."""
    if not params:
        return {}
    if len(params) > _MAX_PARAMS:
        raise ValueError(f"too many params (max {_MAX_PARAMS})")
    out: dict[str, Any] = {}
    for key, value in params.items():
        if not _PARAM_KEY_RE.match(str(key)):
            raise ValueError(f"invalid param key {key!r} (want [a-z][a-z0-9_]*)")
        if str(key) in _RESERVED_PARAM_KEYS:
            raise ValueError(f"param key {key!r} is reserved")
        if isinstance(value, bool) or isinstance(value, int):
            out[key] = value
        elif isinstance(value, str):
            if len(value) > _MAX_PARAM_VALUE_CHARS:
                raise ValueError(
                    f"param {key!r} too long (max {_MAX_PARAM_VALUE_CHARS} chars)"
                )
            out[key] = value
        else:
            raise ValueError(
                f"param {key!r} must be a string, int, or bool "
                f"(got {type(value).__name__})"
            )
    return out


async def run_plan(
    *,
    pool: Any,
    db_service: Any,
    plan_id: str,
    topic_override: str | None = None,
    params: dict[str, Any] | None = None,
    user_id: str = "operator",
) -> dict[str, Any]:
    """One-shot: draft → ran, create the task, link + stamp + report.

    ``params`` are operator-supplied per-run values (e.g. ``post_id`` for a
    plan built on ``content.load_existing_post``) merged into task metadata
    — the router flattens metadata onto the initial state, which is where
    entry atoms read run-time identifiers from. Validated BEFORE the
    one-shot resolve so a bad call never consumes the draft.

    Returns the resolved plan (with ``task_id``); ``already_resolved: True``
    when this call lost the race. Raises ``KeyError`` for an unknown id and
    ``ValueError`` for bad params.
    """
    clean_params = validate_run_params(params)
    row = await pool.fetchrow(
        """
        UPDATE chat_plans
           SET status = 'ran', resolved_at = now()
         WHERE id = $1::uuid AND status = 'draft'
        RETURNING id, conversation_id, message_id, intent, topic,
                  template_slug, spec
        """,
        plan_id,
    )
    if row is None:
        existing = await get_plan(pool, plan_id)
        if existing is None:
            raise KeyError(plan_id)
        existing["already_resolved"] = True
        return existing

    conversation_id = str(row["conversation_id"])
    message_id = str(row["message_id"])
    slug = row["template_slug"]
    topic = (topic_override or row["topic"] or row["intent"])[:200]
    spec = row["spec"]
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError:
            spec = None
    task_type = infer_task_type(spec)

    task_id = str(uuid_lib.uuid4())
    task_data = {
        "id": task_id,
        "task_name": f"Plan run: {topic}",
        "task_type": task_type,
        "topic": topic,
        "template_slug": slug,
        "status": "pending",
        "user_id": user_id,
        "metadata": {
            **clean_params,
            "created_via": "chat_plan",
            "chat_plan_id": plan_id,
            "plan_intent": row["intent"][:500],
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    returned_task_id = await db_service.add_task(task_data)
    await pool.execute(
        "UPDATE chat_plans SET task_id = $2 WHERE id = $1::uuid",
        plan_id, returned_task_id,
    )

    await store.add_task_link(pool, conversation_id, returned_task_id)
    await _stamp_plan_card(
        pool, message_id=message_id, plan_id=plan_id,
        state="ran", task_id=returned_task_id,
    )
    await store.add_message(
        pool, conversation_id, role="system",
        parts=[{
            "type": "markdown",
            "text": (
                f'Plan run started: "{topic}" — task {returned_task_id[:8]} '
                f"on template {slug}. Watching it here."
            ),
        }],
    )
    await _audit(
        pool, plan_id=plan_id, conversation_id=conversation_id,
        slug=slug, task_id=returned_task_id,
    )

    resolved = await get_plan(pool, plan_id)
    assert resolved is not None
    return resolved


async def _stamp_plan_card(
    pool: Any, *, message_id: str, plan_id: str, state: str, task_id: str | None,
) -> None:
    row = await pool.fetchrow(
        "SELECT parts FROM chat_messages WHERE id = $1::uuid", message_id,
    )
    if row is None:
        logger.error("[chat_plans] message %s vanished — card not stamped", message_id)
        return
    parts = row["parts"]
    if isinstance(parts, str):
        parts = json.loads(parts)
    changed = False
    for part in parts:
        card = part.get("card") if isinstance(part, dict) else None
        if (
            isinstance(card, dict)
            and card.get("kind") == "plan"
            and card.get("plan_id") == plan_id
        ):
            card["state"] = state
            card["task_id"] = task_id
            changed = True
    if not changed:
        logger.error(
            "[chat_plans] plan card %s not found on message %s", plan_id, message_id,
        )
        return
    await pool.execute(
        "UPDATE chat_messages SET parts = $2::jsonb WHERE id = $1::uuid",
        message_id, json.dumps(parts),
    )


async def _audit(
    pool: Any, *, plan_id: str, conversation_id: str, slug: str, task_id: str,
) -> None:
    try:
        from services.audit_event_schemas import validate_event_details
        from services.audit_log import AuditLogger

        details = validate_event_details("chat_plan_run", {
            "schema_version": 1,
            "plan_id": plan_id,
            "conversation_id": conversation_id,
            "template_slug": slug,
            "task_id": task_id,
        })
        await AuditLogger(pool).log("chat_plan_run", "chat_plans", details or {})
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        logger.exception("[chat_plans] audit write failed")


__all__ = ["create_plan", "get_plan", "namespace_spec", "run_plan"]
