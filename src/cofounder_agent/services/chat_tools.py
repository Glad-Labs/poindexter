"""Chat tool registry — the Cofounder agent's curated capability catalog.

A fourth thin adapter per the transport-adapter contract (ADR 2026-06-10):
every handler delegates to the same service functions the HTTP / CLI / MCP
adapters call — declarations here are bindings, never business logic and
never inline SQL against business tables.

Registry shape (poindexter#947): each tool declares an OpenAI-style JSON
schema (sent through ``dispatch_complete(tools=...)``), a permission
``tier``, and an async handler ``(ctx, **args) -> str``. Handlers return
operator-readable text — the agent loop digests it to
``console_chat_tool_result_max_chars`` before it enters model context
(cards/DB rows carry full payloads; small local models get summaries).

Tiers (P1): ``read`` tools auto-run. ``create_post`` is the one ``write``
tool and ALSO auto-runs — its output lands in the existing approval inbox,
so the human gate is already downstream. The in-chat approval-card gate for
other write tools (publish / settings / restart) arrives with P3; add new
write tools only alongside that machinery.

Error style: handlers raise ``ChatToolError`` with an operator/LLM-readable
message (feedback_design_for_llm_consumers — the message is the model's
repair signal). The loop catches it, surfaces it as a failed tool result,
and lets the model try again; any other exception is reported generically
and audited.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from services.logger_config import get_logger

logger = get_logger(__name__)


class ChatToolError(Exception):
    """Tool failure whose message is safe + useful to show the model."""


@dataclass
class ChatToolContext:
    """Per-turn context threaded into every handler.

    ``linked_task_ids`` is an out-band channel: a handler that created or
    touched a pipeline task appends its id, and the agent loop drains it
    after the call to write ``chat_task_links`` rows + emit ``task_linked``
    stream events (the P3 activity rail watches those links).
    """

    db_service: Any
    site_config: Any
    pool: Any
    user_id: str = "operator"
    conversation_id: str = ""
    linked_task_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChatToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    tier: str  # "read" | "write"
    handler: Callable[..., Awaitable[str]]
    # P3 (poindexter#949): write tools with requires_approval=True are NOT
    # executed by the agent loop. The loop queues a chat_approvals row +
    # renders an approval card; the operator's Approve click executes the
    # handler via services/chat_approvals.py. An agent_permissions row
    # (agent_name='console_chat', resource=<tool>, action='execute') can
    # relax this (requires_approval=false → run inline) or forbid the tool
    # outright (allowed=false); an indeterminate check fails CLOSED to the
    # card. create_post stays approval-free by design — its output already
    # lands in the operator's approval inbox downstream.
    requires_approval: bool = False


# ---------------------------------------------------------------------------
# Handlers — thin bindings onto the service layer
# ---------------------------------------------------------------------------


async def _list_tasks(ctx: ChatToolContext, *, status: str = "", limit: int = 10) -> str:
    limit = max(1, min(int(limit), 50))
    # ``get_tasks_paginated`` returns ``(rows, total)`` — the
    # ``PaginatedTasksResult`` type name "was a long-standing lie" per its
    # own docstring (#201). Reading ``.tasks`` off the tuple silently
    # yielded "No tasks found" against a 1,900-task table (caught by the
    # 2026-08-01 live verification once the ollama_chat transport fix
    # stopped masking tool results).
    rows, total = await ctx.db_service.get_tasks_paginated(
        offset=0, limit=limit, status=(status or None), light=True,
    )
    rows = rows or []
    if not rows:
        return f"No tasks found (status filter: {status or 'all'})."
    lines = []
    for t in rows:
        row = t if isinstance(t, dict) else dict(t)
        lines.append(
            f"- {str(row.get('task_id') or row.get('id') or '?')[:8]} "
            f"[{row.get('status', '?')}] {row.get('topic') or row.get('task_name') or ''}"
            f" (created {str(row.get('created_at') or '')[:16]})"
        )
    total_note = f" of {total} total" if total and total > len(lines) else ""
    return f"{len(lines)} task(s){total_note}:\n" + "\n".join(lines)


async def _get_task(ctx: ChatToolContext, *, task_id: str) -> str:
    from utils.uuid_prefix import resolve_task_id_prefix

    resolved = await resolve_task_id_prefix(ctx.pool, task_id.strip())
    record = await ctx.db_service.get_task(resolved)
    if record is None:
        raise ChatToolError(f"No task found for id {task_id!r}.")
    row = record if isinstance(record, dict) else getattr(record, "__dict__", {})
    keep = (
        "task_id", "status", "topic", "task_name", "task_type", "niche_slug",
        "category", "quality_score", "created_at", "updated_at",
    )
    summary = {k: str(row.get(k)) for k in keep if row.get(k) is not None}
    return json.dumps(summary, default=str)


async def _get_budget(ctx: ChatToolContext) -> str:
    from services.cost_aggregation_service import CostAggregationService

    svc = CostAggregationService(ctx.db_service)  # type: ignore[no-untyped-call]
    status = await svc.get_budget_status()
    keep = (
        "monthly_budget", "amount_spent", "amount_remaining", "percent_used",
        "daily_burn_rate", "projected_final_cost", "status",
    )
    slim = {k: status.get(k) for k in keep if k in status}
    alerts = status.get("alerts") or []
    if alerts:
        slim["alerts"] = alerts[:3]
    return json.dumps(slim, default=str)


async def _search_memory(ctx: ChatToolContext, *, query: str, limit: int = 5) -> str:
    return await _memory_search_text(query=query, limit=limit, source_table=None)


async def _find_similar_posts(ctx: ChatToolContext, *, topic: str, limit: int = 5) -> str:
    return await _memory_search_text(query=topic, limit=limit, source_table="posts")


async def _memory_search_text(
    *, query: str, limit: int, source_table: str | None,
) -> str:
    from poindexter.memory import MemoryClient

    limit = max(1, min(int(limit), 10))
    mem = MemoryClient()
    try:
        await mem.connect()
        hits = await mem.search(
            query, source_table=source_table, min_similarity=0.3, limit=limit,
        )
    finally:
        await mem.close()
    if not hits:
        scope = source_table or "memory"
        return f"No {scope} hits for {query!r} (min similarity 0.3)."
    lines = [
        f"- [{h.source_table}/{str(h.source_id)[:12]}] "
        f"(sim {h.similarity:.2f}) {h.text_preview[:180]}"
        for h in hits
    ]
    return f"{len(lines)} hit(s) for {query!r}:\n" + "\n".join(lines)


async def _get_audit_summary(ctx: ChatToolContext, *, hours: int = 24) -> str:
    from services.audit_log import query_summary

    hours = max(1, min(int(hours), 720))
    rows = await query_summary(ctx.pool, hours=hours)
    if not rows:
        return f"No audit events in the last {hours}h."
    lines = [
        f"- {r.get('event_type')}: {r.get('count')} ({r.get('severity', 'info')})"
        for r in rows[:20]
    ]
    return f"Audit events, last {hours}h:\n" + "\n".join(lines)


async def _get_setting(ctx: ChatToolContext, *, key: str) -> str:
    key = key.strip()
    value = ctx.site_config.get(key, None)
    if value is None:
        raise ChatToolError(
            f"Setting {key!r} is not set, does not exist, or is a secret — "
            "secrets are never exposed in chat."
        )
    return f"{key} = {value!r}"


async def _create_post(
    ctx: ChatToolContext,
    *,
    topic: str,
    category: str = "technology",
    target_audience: str = "developers and founders",
    niche_slug: str = "",
    force: bool = False,
) -> str:
    from schemas.task_schemas import UnifiedTaskRequest
    from services.blog_task_creation import (
        BlogTaskCreationError,
        create_blog_post_task,
    )

    fields: dict[str, Any] = {
        "task_type": "blog_post",
        "topic": topic,
        "category": category,
        "target_audience": target_audience,
        "niche_slug": niche_slug or None,
        "force": bool(force),
    }
    request = UnifiedTaskRequest(**fields)
    try:
        result = await create_blog_post_task(
            request,
            db_service=ctx.db_service,
            site_config=ctx.site_config,
            user_id=ctx.user_id,
        )
    except BlogTaskCreationError as e:
        # 409 duplicate-topic detail names the colliding post + the
        # force=true override — exactly the repair signal the model needs.
        raise ChatToolError(e.detail) from e
    task_id = str(result.get("task_id") or "")
    if task_id:
        ctx.linked_task_ids.append(task_id)
    return json.dumps(
        {k: result.get(k) for k in ("task_id", "topic", "status", "message")},
        default=str,
    )


async def _set_setting(ctx: ChatToolContext, *, key: str, value: str) -> str:
    from services.settings_service import SettingsService

    key = key.strip()
    row = await ctx.pool.fetchrow(
        "SELECT is_secret FROM app_settings WHERE key = $1", key,
    )
    if row is None:
        raise ChatToolError(
            f"Setting {key!r} does not exist — chat can update existing "
            "settings but never invent new keys (new keys land in "
            "settings_defaults.py via PR)."
        )
    if row["is_secret"]:
        raise ChatToolError(
            f"Setting {key!r} is a secret — secrets are managed via "
            "`poindexter setup` / set_secret, never through chat."
        )
    svc = SettingsService(ctx.pool)  # type: ignore[no-untyped-call]
    await svc.set(key, str(value))
    return f"{key} set to {value!r}. Live within ~60s (reload_site_config)."


async def _restart_service(ctx: ChatToolContext, *, container: str) -> str:
    from services.service_restart_requests import (
        InvalidContainerName,
        SelfDefeatingRestart,
        create_restart_request,
    )

    try:
        row = await create_restart_request(
            ctx.pool, container.strip(), requested_by="console_chat",
        )
    except InvalidContainerName as exc:
        raise ChatToolError(f"Invalid container name: {exc}") from exc
    except SelfDefeatingRestart as exc:
        raise ChatToolError(str(exc)) from exc
    return (
        f"Restart queued for {container!r} (request {str(row.get('id'))[:8]}) — "
        "the brain daemon claims and executes it within ~10s."
    )


async def _cancel_task(ctx: ChatToolContext, *, task_id: str) -> str:
    import json as _json
    from datetime import datetime, timezone

    from utils.uuid_prefix import resolve_task_id_prefix

    resolved = await resolve_task_id_prefix(ctx.pool, task_id.strip())
    task = await ctx.db_service.get_task(resolved)
    if task is None:
        raise ChatToolError(f"No task found for id {task_id!r}.")
    # Same soft-cancel the DELETE /api/tasks/{id} route performs.
    deleted_metadata = {
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        "deleted_by": "console_chat",
        "soft_delete": True,
    }
    await ctx.db_service.update_task_status(
        resolved, "cancelled", result=_json.dumps({"metadata": deleted_metadata}),
    )
    return f"Task {resolved[:8]} cancelled."


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_STR = {"type": "string"}
_INT = {"type": "integer"}


def _params(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": required or [],
    }


CHAT_TOOLS: tuple[ChatToolSpec, ...] = (
    ChatToolSpec(
        name="list_tasks",
        description=(
            "List content pipeline tasks, newest first. Optional status filter: "
            "pending, in_progress, awaiting_approval, published, rejected, "
            "failed, cancelled."
        ),
        parameters=_params({"status": _STR, "limit": _INT}),
        tier="read",
        handler=_list_tasks,
    ),
    ChatToolSpec(
        name="get_task",
        description="Get one pipeline task's detail by id (short prefixes ok).",
        parameters=_params({"task_id": _STR}, ["task_id"]),
        tier="read",
        handler=_get_task,
    ),
    ChatToolSpec(
        name="get_budget",
        description="Current AI spend vs the monthly budget cap, with alerts.",
        parameters=_params({}),
        tier="read",
        handler=_get_budget,
    ),
    ChatToolSpec(
        name="search_memory",
        description=(
            "Semantic search across the operator's memory (decisions, notes, "
            "issues, audit). Use for 'what do we know / what was decided "
            "about X'. NOT for published-post coverage questions — that is "
            "find_similar_posts."
        ),
        parameters=_params({"query": _STR, "limit": _INT}, ["query"]),
        tier="read",
        handler=_search_memory,
    ),
    ChatToolSpec(
        name="find_similar_posts",
        description=(
            "Find published posts semantically similar to a topic. Use for "
            "'have we written about X' / 'do we have coverage of X' "
            "questions. Not needed before create_post — the pipeline "
            "dedup-checks on its own."
        ),
        parameters=_params({"topic": _STR, "limit": _INT}, ["topic"]),
        tier="read",
        handler=_find_similar_posts,
    ),
    ChatToolSpec(
        name="get_audit_summary",
        description="System activity summary from the audit log (counts by event type).",
        parameters=_params({"hours": _INT}),
        tier="read",
        handler=_get_audit_summary,
    ),
    ChatToolSpec(
        name="get_setting",
        description="Read one non-secret app_settings value by key.",
        parameters=_params({"key": _STR}, ["key"]),
        tier="read",
        handler=_get_setting,
    ),
    ChatToolSpec(
        name="create_post",
        description=(
            "Create a blog post pipeline task on a topic (pass 'auto' to pick "
            "the best pooled topic). The draft goes through the full pipeline "
            "and waits for operator approval — nothing publishes directly. "
            "On a duplicate-topic conflict, relay the error and ask before "
            "retrying with force=true."
        ),
        parameters=_params(
            {
                "topic": _STR,
                "category": _STR,
                "target_audience": _STR,
                "niche_slug": _STR,
                "force": {"type": "boolean"},
            },
            ["topic"],
        ),
        tier="write",
        handler=_create_post,
    ),
    ChatToolSpec(
        name="set_setting",
        description=(
            "Update one existing non-secret app_settings value. Queues an "
            "approval card — the change happens only after the operator "
            "clicks Approve."
        ),
        parameters=_params({"key": _STR, "value": _STR}, ["key", "value"]),
        tier="write",
        handler=_set_setting,
        requires_approval=True,
    ),
    ChatToolSpec(
        name="restart_service",
        description=(
            "Queue a container restart (executed by the brain daemon). "
            "Queues an approval card — restarts happen only after the "
            "operator clicks Approve."
        ),
        parameters=_params({"container": _STR}, ["container"]),
        tier="write",
        handler=_restart_service,
        requires_approval=True,
    ),
    ChatToolSpec(
        name="cancel_task",
        description=(
            "Cancel a pipeline task (soft-cancel, same as the console's "
            "Kill). Queues an approval card — cancellation happens only "
            "after the operator clicks Approve."
        ),
        parameters=_params({"task_id": _STR}, ["task_id"]),
        tier="write",
        handler=_cancel_task,
        requires_approval=True,
    ),
)

_BY_NAME: dict[str, ChatToolSpec] = {t.name: t for t in CHAT_TOOLS}


def get_tool(name: str) -> ChatToolSpec | None:
    return _BY_NAME.get(name)


def tool_names_csv() -> str:
    return ", ".join(t.name for t in CHAT_TOOLS)


def to_openai_tools() -> list[dict[str, Any]]:
    """The ``tools=`` payload for ``dispatch_complete`` (OpenAI function shape)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in CHAT_TOOLS
    ]


__all__ = [
    "CHAT_TOOLS",
    "ChatToolContext",
    "ChatToolError",
    "ChatToolSpec",
    "get_tool",
    "to_openai_tools",
    "tool_names_csv",
]
