"""Cofounder chat routes — conversation CRUD + the streamed turn endpoint.

Thin adapters over ``services/chat_conversation_store.py`` +
``services/chat_agent.py`` (poindexter#947). The turn endpoint streams
NDJSON — one JSON event object per line — over a plain chunked response so
the console's fetch-stream reader works same-origin with the OAuth JWT
(no EventSource header limitation).

The whole surface is gated by ``app_settings.console_chat_enabled``
(default false): disabled → 403 with the exact remediation command, per
feedback_no_silent_defaults.
"""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from middleware.api_token_auth import verify_api_token
from services.database_service import DatabaseService
from services.logger_config import get_logger
from services.site_config import SiteConfig
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency, get_site_config_dependency

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/chat",
    tags=["chat"],
    dependencies=[Depends(verify_api_token)],
)


class CreateConversationRequest(BaseModel):
    title: str = Field("", max_length=200)
    brain: str = Field("local", pattern="^(local|claude_code)$")


class SendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


def _require_enabled(site_config: SiteConfig) -> None:
    raw = str(site_config.get("console_chat_enabled", "false") or "false")
    if raw.strip().lower() not in ("true", "1", "yes", "on"):
        raise HTTPException(
            status_code=403,
            detail=(
                "The Cofounder chat surface is disabled. Enable it with "
                "`poindexter settings set console_chat_enabled true` "
                "(and route the LLM tier through a tool-capable provider)."
            ),
        )


def _stale_after_seconds(site_config: SiteConfig) -> int:
    try:
        timeout = int(str(site_config.get("console_chat_turn_timeout_s", "120")))
    except (TypeError, ValueError):
        timeout = 120
    # A turn is definitively dead 2x past its own deadline.
    return max(60, timeout * 2)


@router.get("/tools")
async def list_tools(
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """The agent's capability catalog — name, description, tier per tool.

    The console's empty state answers "what can you do?" from this read so
    the UI never drifts from the registry (AI-first: the catalog IS the
    capability list). Same gate as the rest of the surface.
    """
    _require_enabled(site_config)
    from services.chat_tools import CHAT_TOOLS

    persona = str(site_config.get("agent_persona_name", "Poindexter") or "Poindexter")
    try:
        watch_poll_seconds = int(
            str(site_config.get("console_chat_watch_poll_seconds", "5"))
        )
    except (TypeError, ValueError):
        watch_poll_seconds = 5
    return {
        "persona": persona,
        "watch_poll_seconds": watch_poll_seconds,
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "tier": t.tier,
                "requires_approval": t.requires_approval,
            }
            for t in CHAT_TOOLS
        ],
    }


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: CreateConversationRequest,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    _require_enabled(site_config)
    from services import chat_conversation_store as store

    return await store.create_conversation(
        db_service.pool, title=body.title, brain=body.brain,
    )


@router.get("/conversations")
async def list_conversations(
    status: str = Query("active", pattern="^(active|archived)$"),
    limit: int = Query(50, ge=1, le=200),
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    _require_enabled(site_config)
    from services import chat_conversation_store as store

    await store.repair_stale_turns(
        db_service.pool, None, stale_after_seconds=_stale_after_seconds(site_config),
    )
    conversations = await store.list_conversations(
        db_service.pool, status=status, limit=limit,
    )
    return {"conversations": conversations, "count": len(conversations)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    _require_enabled(site_config)
    from services import chat_conversation_store as store

    conversation = await _load_conversation(store, db_service, conversation_id)
    await store.repair_stale_turns(
        db_service.pool, conversation_id,
        stale_after_seconds=_stale_after_seconds(site_config),
    )
    messages = await store.list_messages(db_service.pool, conversation_id)
    task_links = await store.list_task_links(db_service.pool, conversation_id)
    return {
        "conversation": conversation,
        "messages": messages,
        "task_links": task_links,
    }


@router.post("/conversations/{conversation_id}/archive")
async def archive_conversation(
    conversation_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    _require_enabled(site_config)
    from services import chat_conversation_store as store

    await _load_conversation(store, db_service, conversation_id)
    archived = await store.archive_conversation(db_service.pool, conversation_id)
    return {"archived": archived, "conversation_id": conversation_id}


@router.post("/conversations/{conversation_id}/messages")
@limiter.limit("20/minute")
async def send_message(
    request: Request,
    conversation_id: str,
    body: SendMessageRequest,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> StreamingResponse:
    """Run one agent turn; the response body is NDJSON stream events.

    Errors after the stream starts ride the stream as ``error`` events (the
    HTTP status is already committed); errors before it start as normal
    HTTP errors (403 disabled, 404 unknown conversation, 409 busy).
    """
    _require_enabled(site_config)
    from services import chat_conversation_store as store
    from services.chat_agent import run_turn

    conversation = await _load_conversation(store, db_service, conversation_id)
    if conversation.get("status") != "active":
        raise HTTPException(status_code=409, detail="Conversation is archived.")
    if conversation.get("brain") != "local":
        raise HTTPException(
            status_code=409,
            detail=(
                "This conversation's brain is not available yet — the "
                "claude_code Deep mode ships in a later phase (P6). Create "
                "a conversation with brain='local'."
            ),
        )

    async def _ndjson() -> AsyncIterator[str]:
        import json as _json

        try:
            async for event in run_turn(
                pool=db_service.pool,
                db_service=db_service,
                site_config=site_config,
                conversation=conversation,
                user_text=body.text,
            ):
                yield _json.dumps(event, default=str) + "\n"
        except Exception:  # noqa: BLE001 — the stream must end, never hang
            logger.exception("[chat] turn stream crashed")
            yield _json.dumps({
                "event": "error", "reason": "stream_crashed",
                "detail": "internal error — see worker logs",
            }) + "\n"

    return StreamingResponse(_ndjson(), media_type="application/x-ndjson")


@router.post("/approvals/{approval_id}/approve")
async def approve_approval(
    approval_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Resolve an approval card → execute the stored call (one-shot)."""
    return await _resolve(approval_id, True, db_service, site_config)


@router.post("/approvals/{approval_id}/deny")
async def deny_approval(
    approval_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Resolve an approval card as denied — nothing executes (one-shot)."""
    return await _resolve(approval_id, False, db_service, site_config)


async def _resolve(
    approval_id: str, approve: bool,
    db_service: DatabaseService, site_config: SiteConfig,
) -> dict[str, Any]:
    _require_enabled(site_config)
    from services.chat_approvals import resolve_approval

    try:
        return await resolve_approval(
            pool=db_service.pool, db_service=db_service,
            site_config=site_config, approval_id=approval_id, approve=approve,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown approval: {approval_id!r}",
        ) from exc
    except Exception as exc:  # asyncpg DataError on malformed uuid, etc.
        if "invalid input" in str(exc).lower():
            raise HTTPException(
                status_code=404, detail=f"Unknown approval: {approval_id!r}",
            ) from exc
        raise


class RunPlanRequest(BaseModel):
    topic: str = Field("", max_length=200)
    # Per-run values (e.g. post_id for a load_existing_post entry) merged
    # into task metadata → flattened onto the initial pipeline state.
    # Sanitized by chat_plans.validate_run_params (fail loud → 422).
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/plans/{plan_id}/run")
async def run_plan_route(
    plan_id: str,
    body: RunPlanRequest | None = None,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """One-shot: run an architect plan card (creates the pipeline task)."""
    _require_enabled(site_config)
    from services.chat_plans import run_plan

    try:
        return await run_plan(
            pool=db_service.pool, db_service=db_service, plan_id=plan_id,
            topic_override=(body.topic if body and body.topic else None),
            params=(body.params if body else None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown plan: {plan_id!r}",
        ) from exc
    except Exception as exc:
        if "invalid input" in str(exc).lower():
            raise HTTPException(
                status_code=404, detail=f"Unknown plan: {plan_id!r}",
            ) from exc
        raise


@router.get("/watch/{task_id}")
async def watch(
    task_id: str,
    db_service: DatabaseService = Depends(get_database_dependency),
    site_config: SiteConfig = Depends(get_site_config_dependency),
) -> dict[str, Any]:
    """Slim run-progress snapshot the activity rail polls (~5s while live)."""
    _require_enabled(site_config)
    from services.chat_watch import watch_task

    snapshot = await watch_task(db_service.pool, task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Unknown task: {task_id!r}")
    return snapshot


async def _load_conversation(
    store: Any, db_service: DatabaseService, conversation_id: str,
) -> dict[str, Any]:
    try:
        conversation = await store.get_conversation(db_service.pool, conversation_id)
    except Exception as exc:  # asyncpg rejects malformed uuids with a DataError
        raise HTTPException(
            status_code=404, detail=f"Unknown conversation: {conversation_id!r}",
        ) from exc
    if conversation is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown conversation: {conversation_id!r}",
        )
    return conversation
