"""Cofounder chat-agent system prompt with a drift-guarded inline fallback.

Resolved through UnifiedPromptManager (Langfuse → SKILL.md), falling back
loudly to the inline copy when the registry is unreachable — the same
resolve-then-fallback seam ``services/voice_prompts.py`` uses for the voice
surfaces (poindexter#612 pattern).

The inline fallback is pinned byte-identical to the
``skills/chat/agent/SKILL.md`` default by ``test_chat_prompts.py`` and logs
at ERROR when it actually fires: self-heal, don't silently suppress
(feedback_self_heal_not_suppress).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Prompt-registry key (mirrored in skills/chat/agent/SKILL.md).
CHAT_SYSTEM_KEY = "chat.system"

# _extract_skill_section() appends exactly one trailing newline, so this ends
# in "\n" to stay byte-identical on the fallback path (test_chat_prompts.py).
_CHAT_SYSTEM_FALLBACK = (
    "You are {persona_name}, the operator's AI cofounder for their content "
    "business. You chat in the operator console and you can act by calling "
    "tools. First decide: does this message ask for data or an action? If "
    "it is a greeting, thanks, or small talk, reply in plain text and call "
    "no tool. Otherwise ground every factual claim in a tool result: call "
    "the one tool that answers it, and never invent numbers, ids, or "
    "statuses. \"Write/draft a post about X\" means create_post. \"Have we "
    "written about X / do we have coverage of X\" means find_similar_posts, "
    "not search_memory. If a tool fails or you don't have the data, say so "
    "plainly. Markdown is fine here — the console renders it. Keep replies "
    "short and lead with the answer. When you take an action, state what "
    "you did and include the ids involved so the operator can follow up. "
    "Your tools: {tool_names}. If asked for something outside those tools, "
    "say you can't do it yet rather than improvising.\n"
)

_FALLBACKS: dict[str, str] = {
    CHAT_SYSTEM_KEY: _CHAT_SYSTEM_FALLBACK,
}


def resolve_chat_prompt(key: str, **kwargs: Any) -> str:
    """Resolve a chat prompt via the prompt registry, falling back loudly.

    ``**kwargs`` are formatted into the template (``persona_name=``,
    ``tool_names=``). Any registry failure logs at ERROR and renders the
    inline fallback so the chat agent always has a system prompt.
    """
    try:
        from services.prompt_manager import get_prompt_manager

        return get_prompt_manager().get_prompt(key, **kwargs)
    except Exception as exc:  # noqa: BLE001 — registry down must not silence chat
        fallback = _FALLBACKS.get(key)
        if fallback is None:
            raise
        logger.error(
            "[chat_prompts] prompt registry unavailable for %r (%s) — "
            "using the inline fallback. Fix the registry; the fallback is "
            "a resilience seam, not the normal path.",
            key,
            exc,
        )
        return fallback.format(**kwargs)


__all__ = ["CHAT_SYSTEM_KEY", "resolve_chat_prompt"]
