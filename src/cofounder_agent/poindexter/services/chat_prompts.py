"""Cofounder chat-agent system prompt, resolved from ``skills/chat/agent/SKILL.md``.

UnifiedPromptManager (a Langfuse override wins when
``langfuse_prompt_overrides_enabled``). There is no in-code copy: a copy hid
prompt-registry bugs behind a stale duplicate (2026-10-09), so a missing key
raises instead.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Prompt-registry key (mirrored in skills/chat/agent/SKILL.md).
CHAT_SYSTEM_KEY = "chat.system"


def resolve_chat_prompt(key: str, **kwargs: Any) -> str:
    """Render a chat prompt from the SKILL.md pack.

    ``**kwargs`` are formatted into the template (``persona_name=``,
    ``tool_names=``). A missing key raises; there is no in-code copy.
    """
    from poindexter.services.prompt_manager import get_prompt_manager

    return get_prompt_manager().get_prompt(key, **kwargs)


__all__ = ["CHAT_SYSTEM_KEY", "resolve_chat_prompt"]
