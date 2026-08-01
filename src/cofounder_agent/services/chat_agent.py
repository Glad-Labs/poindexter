"""Cofounder chat agent — the local tool-loop brain behind /api/chat.

``run_turn`` is an async generator yielding typed NDJSON events (the route
serializes one JSON object per line):

    turn_started  {message_id}
    tool_start    {name, args_digest}
    tool_result   {name, ok, ms, digest}
    task_linked   {task_id}
    text          {text}
    error         {reason, detail}
    done          {turn_status, prompt_tokens, completion_tokens, cost_usd}

Design constraints (poindexter#947, spec
``docs/superpowers/specs/2026-07-31-cofounder-conversation-surface-design.md``):

- **Persisted lifecycle.** The assistant row is inserted ``streaming``
  before the first LLM call and finalized ``complete`` / ``failed`` /
  ``interrupted`` in a ``finally`` — a crash between the two is what the
  store's lazy repair catches.
- **Loop guards.** Whole-turn deadline (``console_chat_turn_timeout_s``),
  max tool executions (``console_chat_max_tool_calls``), and repeat-call
  detection: the second identical (tool, args) call gets a corrective tool
  result instead of a re-execution; a third aborts the turn (the voice
  agent's lost-tool-result infinite loop is the precedent this guards).
- **Digested context.** Tool results are truncated to
  ``console_chat_tool_result_max_chars`` before entering model context —
  small local models share num_ctx between prompt and output, and a full
  draft in context is how they die.
- **Fail honest.** A provider without tool support (ollama_native), an
  exhausted daily token budget, or a dead LLM all surface as explicit
  ``error`` events + a non-``complete`` turn status — never a silent
  degraded answer.

Every LLM call routes through ``dispatch_complete`` (cost_logs + Langfuse
tracing + budget caps come free); every tool execution writes an
``audit_log`` ``chat_tool_call`` row (the reviewable trail of what the
agent DID).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from services import chat_conversation_store as store
from services.chat_prompts import CHAT_SYSTEM_KEY, resolve_chat_prompt
from services.chat_tools import (
    ChatToolContext,
    ChatToolError,
    get_tool,
    to_openai_tools,
    tool_names_csv,
)
from services.logger_config import get_logger

logger = get_logger(__name__)

# Corrective messages are the model's repair signal — written for LLM
# consumption (feedback_design_for_llm_consumers).
_REPEAT_ONCE = (
    "You already called {name} with identical arguments this turn; its result "
    "is above. Use it instead of repeating the call."
)
_UNKNOWN_TOOL = (
    "Unknown tool {name!r}. Available tools: {names}."
)
_BAD_ARGS = "Could not parse arguments for {name}: {error}. Send valid JSON."


def _textual_tool_calls(text: str) -> list[dict[str, Any]] | None:
    """Recover tool calls a model emitted as TEXT instead of tool_calls.

    Live failure mode (qwen2.5:7b, 2026-08-01 verification turn): the model
    printed ``{"id": "call_…", "type": "function", "function": {"name": …}}``
    in the content channel and the loop accepted it as the final answer —
    the visible reply was raw JSON. Same class as the writer-path envelope
    leak (``llm_text.maybe_unwrap_json`` / feedback_reasoning_models_empty_json);
    same cure: recover the intent deterministically. Only fires when the
    ENTIRE text (minus an optional ```json fence) parses as a tool-call
    shape — prose that merely mentions JSON stays prose. Recovered calls
    re-enter the normal per-call machinery (permission gates, repeat guard,
    caps, audit), so this changes robustness, never policy.
    """
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    if not t or t[0] not in "[{":
        return None
    try:
        payload = json.loads(t)
    except ValueError:
        return None
    items = payload if isinstance(payload, list) else [payload]
    calls: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return None
        fn = item.get("function") if isinstance(item.get("function"), dict) else item
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            return None
        arguments = fn.get("arguments", {})
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments)
        elif not isinstance(arguments, str):
            return None
        calls.append({
            "id": item.get("id") or f"textcall_{i}",
            "name": name,
            "arguments": arguments,
        })
    return calls or None


def _digest(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 22].rstrip() + f"\n…[truncated at {max_chars}]"


def _args_digest(raw_arguments: str, cap: int = 300) -> str:
    raw = (raw_arguments or "").strip()
    return raw[:cap]


def _content_of(message_row: dict[str, Any]) -> str:
    """Flatten a stored message's parts to plain text for model context."""
    chunks: list[str] = []
    for part in message_row.get("parts") or []:
        kind = part.get("type")
        if kind == "markdown":
            chunks.append(part.get("text") or "")
        elif kind == "tool_call":
            # Context tail keeps a one-line trace of past tool activity so the
            # model remembers WHAT it did without re-carrying full results.
            chunks.append(
                f"[used tool {part.get('name')} — "
                f"{'ok' if part.get('ok') else 'failed'}]"
            )
    return "\n".join(c for c in chunks if c).strip()


async def _resolve_provider_supports_tools(pool: Any, tier: str) -> tuple[Any, bool]:
    from services.llm_providers.dispatcher import get_provider

    provider = await get_provider(pool, tier)
    return provider, bool(getattr(provider, "supports_tools", False))


class _TurnAborted(Exception):
    """Internal: abort the loop with a reason already event-streamed."""

    def __init__(self, turn_status: str) -> None:
        super().__init__(turn_status)
        self.turn_status = turn_status


async def run_turn(
    *,
    pool: Any,
    db_service: Any,
    site_config: Any,
    conversation: dict[str, Any],
    user_text: str,
    user_id: str = "operator",
) -> AsyncIterator[dict[str, Any]]:
    """Run one chat turn; yield stream events. See module docstring."""
    conversation_id = str(conversation["id"])
    cfg_int = _make_cfg_int(site_config)
    turn_timeout = cfg_int("console_chat_turn_timeout_s", 120)
    max_tool_calls = cfg_int("console_chat_max_tool_calls", 8)
    result_max_chars = cfg_int("console_chat_tool_result_max_chars", 2000)
    recent_turns = cfg_int("console_chat_context_recent_turns", 12)
    daily_budget = cfg_int("console_chat_daily_token_budget", 200000)
    model = str(site_config.get("console_chat_model", "qwen2.5:7b") or "qwen2.5:7b")
    persona = str(site_config.get("agent_persona_name", "Poindexter") or "Poindexter")

    # Store the user message first — even a turn that dies leaves the thread
    # honest about what was asked.
    await store.add_message(
        pool, conversation_id, role="user",
        parts=[{"type": "markdown", "text": user_text}],
    )
    await store.set_title_if_empty(pool, conversation_id, user_text.strip()[:60])

    assistant = await store.add_message(
        pool, conversation_id, role="assistant", parts=[],
        turn_status="streaming", model=model,
    )
    message_id = assistant["id"]
    yield {"event": "turn_started", "message_id": message_id}

    parts: list[dict[str, Any]] = []
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    turn_status = "failed"
    turn_started_monotonic = time.monotonic()

    try:
        # Daily chat budget — independent of the global cost_guard caps.
        used_today = await store.tokens_used_today(pool, user_id=user_id)
        if used_today >= daily_budget:
            detail = (
                f"Daily chat token budget exhausted ({used_today:,} >= "
                f"{daily_budget:,}). Raise console_chat_daily_token_budget "
                "or continue tomorrow."
            )
            parts.append({"type": "markdown", "text": detail})
            yield {"event": "error", "reason": "daily_budget_exhausted", "detail": detail}
            raise _TurnAborted("failed")

        provider, supports_tools = await _resolve_provider_supports_tools(
            pool, "standard",
        )
        if not supports_tools:
            detail = (
                f"The configured LLM provider {provider.name!r} does not "
                "support tool calling, so the chat agent cannot act. Route "
                "the tier through litellm — `poindexter settings set "
                "plugin.llm_provider.primary.standard litellm` — or "
                "openai_compat pointed at Ollama's /v1 endpoint."
            )
            parts.append({"type": "markdown", "text": detail})
            yield {"event": "error", "reason": "provider_no_tools", "detail": detail}
            raise _TurnAborted("failed")

        system_prompt = resolve_chat_prompt(
            CHAT_SYSTEM_KEY, persona_name=persona, tool_names=tool_names_csv(),
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        history = await store.list_messages(pool, conversation_id, limit=recent_turns)
        for row in history:
            if row["id"] == message_id:
                continue
            content = _content_of(row)
            if not content:
                continue
            role = row["role"] if row["role"] in ("user", "assistant") else "user"
            messages.append({"role": role, "content": content})
        # The current user message is already in history (stored above);
        # ensure it is the last entry even if the tail window clipped it.
        if not messages or messages[-1].get("content") != user_text:
            messages.append({"role": "user", "content": user_text})

        ctx = ChatToolContext(
            db_service=db_service,
            site_config=site_config,
            pool=pool,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        openai_tools = to_openai_tools()

        deadline = time.monotonic() + turn_timeout
        executed = 0
        seen_calls: dict[str, int] = {}

        # Round cap: max_tool_calls executions can span at most that many
        # LLM rounds, +2 for the opening call and the closing answer. A
        # model that keeps requesting calls past the limit exhausts the
        # rounds and the turn fails loud instead of burning the deadline.
        for _round in range(max_tool_calls + 2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            completion = await asyncio.wait_for(
                _dispatch(pool, messages, model, openai_tools),
                timeout=remaining,
            )
            prompt_tokens += int(completion.prompt_tokens or 0)
            completion_tokens += int(completion.completion_tokens or 0)
            cost_usd += float((completion.raw or {}).get("response_cost") or 0.0)

            turn_tool_calls = completion.tool_calls
            if not turn_tool_calls:
                # Recover tool calls the model text-encoded (see
                # _textual_tool_calls) — they re-enter the normal gated
                # path below instead of leaking JSON into the thread.
                recovered = _textual_tool_calls(completion.text)
                if recovered and executed < max_tool_calls:
                    logger.warning(
                        "[chat] recovered %d textual tool call(s) from the "
                        "content channel (model=%s)", len(recovered), model,
                    )
                    turn_tool_calls = recovered
            if not turn_tool_calls:
                final_text = (completion.text or "").strip()
                if not final_text:
                    final_text = (
                        "(the model returned an empty reply — try rephrasing)"
                    )
                parts.append({"type": "markdown", "text": final_text})
                yield {"event": "text", "text": final_text}
                turn_status = "complete"
                break

            # Assistant tool-call message rides back into context in the
            # OpenAI shape so the follow-up call sees its own decision.
            messages.append({
                "role": "assistant",
                "content": completion.text or None,
                "tool_calls": [
                    {
                        "id": tc["id"] or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"] or "{}",
                        },
                    }
                    for i, tc in enumerate(turn_tool_calls)
                ],
            })

            for i, tc in enumerate(turn_tool_calls):
                call_id = tc["id"] or f"call_{i}"
                name = tc["name"]
                raw_args = tc["arguments"] or "{}"

                def _tool_reply(content: str, *, _call_id: str = call_id) -> None:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": _call_id,
                        "content": content,
                    })

                spec = get_tool(name)
                if spec is None:
                    detail = _UNKNOWN_TOOL.format(name=name, names=tool_names_csv())
                    _tool_reply(detail)
                    yield {"event": "tool_result", "name": name, "ok": False,
                           "ms": 0, "digest": detail}
                    continue

                repeat_key = f"{name}:{raw_args.strip()}"
                seen = seen_calls.get(repeat_key, 0)
                if seen >= 2:
                    detail = (
                        f"Aborting: {name} was requested with identical "
                        "arguments three times this turn."
                    )
                    parts.append({"type": "markdown", "text": detail})
                    yield {"event": "error", "reason": "repeat_tool_call",
                           "detail": detail}
                    raise _TurnAborted("failed")
                if seen == 1:
                    seen_calls[repeat_key] = 2
                    detail = _REPEAT_ONCE.format(name=name)
                    _tool_reply(detail)
                    yield {"event": "tool_result", "name": name, "ok": False,
                           "ms": 0, "digest": detail}
                    continue
                seen_calls[repeat_key] = 1

                if executed >= max_tool_calls:
                    detail = (
                        f"Tool-call limit reached ({max_tool_calls} per turn) "
                        "— answer with what you have."
                    )
                    _tool_reply(detail)
                    yield {"event": "tool_result", "name": name, "ok": False,
                           "ms": 0, "digest": detail}
                    continue

                # ── Write-tool approval gate (P3 poindexter#949) ──
                # A gated write tool is queued for the operator's click, NOT
                # executed. The model gets an honest tool result so the turn
                # wraps up gracefully; the card carries the action forward.
                if spec.tier == "write":
                    from services.chat_approvals import (
                        approval_policy,
                        create_approval,
                    )

                    policy = await approval_policy(
                        pool, name, default_card=spec.requires_approval,
                    )
                    if policy == "forbid":
                        detail = (
                            f"Tool {name} is forbidden for the chat agent "
                            "(agent_permissions allowed=false)."
                        )
                        _tool_reply(detail)
                        yield {"event": "tool_result", "name": name,
                               "ok": False, "ms": 0, "digest": detail}
                        continue
                    if policy == "card":
                        args_digest = _args_digest(raw_args)
                        try:
                            args = json.loads(raw_args) if raw_args.strip() else {}
                            if not isinstance(args, dict):
                                raise ValueError("arguments must be a JSON object")
                        except ValueError as exc:
                            detail = _BAD_ARGS.format(name=name, error=exc)
                            _tool_reply(detail)
                            yield {"event": "tool_result", "name": name,
                                   "ok": False, "ms": 0, "digest": detail}
                            continue
                        summary = f"{name} {json.dumps(args, default=str)[:300]}"
                        approval = await create_approval(
                            pool, conversation_id=conversation_id,
                            message_id=message_id, tool=name, args=args,
                            summary=summary,
                        )
                        parts.append({
                            "type": "card",
                            "card": {
                                "kind": "approval",
                                "approval_id": approval["id"],
                                "tool": name,
                                "summary": summary,
                                "args_digest": args_digest,
                                "state": "pending",
                            },
                        })
                        yield {"event": "approval_required",
                               "approval_id": approval["id"], "tool": name,
                               "summary": summary}
                        detail = (
                            f"{name} is queued for operator approval "
                            f"(id {approval['id'][:8]}). It runs only if the "
                            "operator clicks Approve on the card — tell them "
                            "it awaits their sign-off, then stop."
                        )
                        _tool_reply(detail)
                        yield {"event": "tool_result", "name": name,
                               "ok": True, "ms": 0, "digest": detail}
                        continue
                    # policy == 'inline': operator explicitly relaxed the
                    # gate via agent_permissions — fall through to execute.

                args_digest = _args_digest(raw_args)
                yield {"event": "tool_start", "name": name,
                       "args_digest": args_digest}
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                except ValueError as exc:
                    detail = _BAD_ARGS.format(name=name, error=exc)
                    _tool_reply(detail)
                    yield {"event": "tool_result", "name": name, "ok": False,
                           "ms": 0, "digest": detail}
                    continue

                executed += 1
                started = time.monotonic()
                ok = False
                error_detail: str | None = None
                try:
                    result_text = await spec.handler(ctx, **args)
                    ok = True
                except ChatToolError as exc:
                    result_text = f"Tool error: {exc}"
                    error_detail = str(exc)
                except TypeError as exc:
                    # Wrong/missing kwargs — repairable by the model.
                    result_text = _BAD_ARGS.format(name=name, error=exc)
                    error_detail = str(exc)
                except Exception as exc:  # noqa: BLE001 — surfaced + audited
                    logger.exception("[chat] tool %s crashed", name)
                    result_text = (
                        f"Tool {name} failed unexpectedly "
                        f"({type(exc).__name__}). Tell the operator."
                    )
                    error_detail = f"{type(exc).__name__}: {exc}"
                duration_ms = int((time.monotonic() - started) * 1000)

                digest = _digest(result_text, result_max_chars)
                _tool_reply(digest)
                parts.append({
                    "type": "tool_call", "name": name, "tier": spec.tier,
                    "ok": ok, "ms": duration_ms, "args_digest": args_digest,
                    "result_digest": digest,
                })
                yield {"event": "tool_result", "name": name, "ok": ok,
                       "ms": duration_ms, "digest": digest}
                await _audit_tool_call(
                    pool, conversation_id=conversation_id, message_id=message_id,
                    tool=name, tier=spec.tier, ok=ok, duration_ms=duration_ms,
                    args_digest=args_digest, error=error_detail,
                )

                for task_id in ctx.linked_task_ids:
                    await store.add_task_link(
                        pool, conversation_id, task_id, purpose="created",
                    )
                    parts.append({
                        "type": "card",
                        "card": {"kind": "task_link", "task_id": task_id},
                    })
                    yield {"event": "task_linked", "task_id": task_id}
                ctx.linked_task_ids.clear()
        else:
            # Round cap exhausted without a final text answer.
            detail = (
                "The model kept requesting tool calls past the per-turn "
                "round limit; the turn was stopped."
            )
            parts.append({"type": "markdown", "text": detail})
            yield {"event": "error", "reason": "round_limit", "detail": detail}
            raise _TurnAborted("failed")

    except _TurnAborted as aborted:
        turn_status = aborted.turn_status
    except asyncio.CancelledError:
        # Client disconnect or the composer's Stop button — Starlette cancels
        # the generator at a yield point. Finalize as 'interrupted' (best
        # effort in the finally; the store's lazy repair is the backstop if
        # even that write is cancelled) and let the cancellation propagate.
        turn_status = "interrupted"
        parts.append({
            "type": "markdown",
            "text": "Turn stopped by the operator (or the connection dropped).",
        })
        raise
    except asyncio.TimeoutError:
        turn_status = "interrupted"
        detail = (
            f"Turn hit the {turn_timeout}s deadline "
            "(console_chat_turn_timeout_s) and was interrupted."
        )
        parts.append({"type": "markdown", "text": detail})
        yield {"event": "error", "reason": "turn_timeout", "detail": detail}
    except Exception as exc:  # noqa: BLE001 — the stream must end honestly
        logger.exception("[chat] turn crashed (conversation=%s)", conversation_id)
        turn_status = "failed"
        detail = f"Turn failed: {type(exc).__name__}: {exc}"
        parts.append({"type": "markdown", "text": detail})
        yield {"event": "error", "reason": "turn_crashed", "detail": detail}
    finally:
        try:
            # Shielded: during a cancellation the surrounding task is being
            # torn down, and a bare await here would just re-raise before the
            # write lands. If even the shielded write dies, the store's lazy
            # interrupted-repair covers the stranded row.
            await asyncio.shield(store.finalize_message(
                pool, message_id, parts=parts, turn_status=turn_status,
                model=model, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, cost_usd=cost_usd,
            ))
        except BaseException:  # noqa: BLE001 — finalize is best-effort; repair covers it
            logger.exception("[chat] finalize failed (message=%s)", message_id)
        try:
            await asyncio.shield(_audit_turn_completed(
                pool, conversation_id=conversation_id, message_id=message_id,
                turn_status=turn_status, model=model, parts=parts,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                duration_ms=int((time.monotonic() - turn_started_monotonic) * 1000),
            ))
        except BaseException:  # noqa: BLE001 — telemetry must never mask the turn
            logger.exception("[chat] turn audit failed (message=%s)", message_id)

    yield {
        "event": "done",
        "turn_status": turn_status,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(cost_usd, 6),
    }


async def _dispatch(
    pool: Any, messages: list[dict[str, Any]], model: str,
    openai_tools: list[dict[str, Any]],
) -> Any:
    from services.llm_providers.dispatcher import dispatch_complete

    return await dispatch_complete(
        pool,
        messages,
        model,
        tier="standard",
        phase="console_chat",
        priority="operator",
        tools=openai_tools,
        tool_choice="auto",
        temperature=0.2,
    )


async def _audit_turn_completed(
    pool: Any,
    *,
    conversation_id: str,
    message_id: str,
    turn_status: str,
    model: str,
    parts: list[dict[str, Any]],
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    duration_ms: int,
) -> None:
    tool_calls = sum(1 for p in parts if p.get("type") == "tool_call")
    tool_errors = sum(
        1 for p in parts if p.get("type") == "tool_call" and not p.get("ok")
    )
    approvals_queued = sum(
        1 for p in parts
        if p.get("type") == "card"
        and (p.get("card") or {}).get("kind") == "approval"
    )
    from services.audit_event_schemas import validate_event_details
    from services.audit_log import AuditLogger

    details = validate_event_details("chat_turn_completed", {
        "schema_version": 1,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "turn_status": turn_status,
        "model": model,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "approvals_queued": approvals_queued,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(cost_usd, 6),
        "duration_ms": duration_ms,
    })
    await AuditLogger(pool).log(
        "chat_turn_completed", "chat_agent", details or {},
    )


async def _audit_tool_call(
    pool: Any,
    *,
    conversation_id: str,
    message_id: str,
    tool: str,
    tier: str,
    ok: bool,
    duration_ms: int,
    args_digest: str,
    error: str | None,
) -> None:
    try:
        from services.audit_event_schemas import validate_event_details
        from services.audit_log import AuditLogger

        details = validate_event_details("chat_tool_call", {
            "schema_version": 1,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "tool": tool,
            "tier": tier,
            "ok": ok,
            "duration_ms": duration_ms,
            "args_digest": args_digest,
            "error": error,
        })
        await AuditLogger(pool).log(
            "chat_tool_call", "chat_agent", details or {},
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the turn
        logger.exception("[chat] audit write failed for tool %s", tool)


def _make_cfg_int(site_config: Any):
    def cfg_int(key: str, default: int) -> int:
        try:
            return int(str(site_config.get(key, default)))
        except (TypeError, ValueError):
            return default
    return cfg_int


__all__ = ["run_turn"]
