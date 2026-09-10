"""Shared voice-agent system prompts (Emma persona + Claude-bridge TTS) with a drift-guarded inline fallback.

Resolved through UnifiedPromptManager (Langfuse → SKILL.md), falling back
loudly to inline copies when the registry is unreachable.

Two voice surfaces share these prompts:

  - ``voice_agent`` (local-mic / Pipecat) and
  - ``voice_agent_livekit`` (phone / LiveKit).

Both import :func:`resolve_voice_prompt` from here rather than carrying their
own inline copies. Keeping the prompts in this *lightweight* module — no
pipecat / livekit imports — is what lets the resolver be unit-tested without
the voice runtime's heavy native deps, and lets the prompts resolve through the
same chain as every pipeline prompt: Langfuse production label →
``skills/voice/agent/SKILL.md`` baked default → the inline ``_*_FALLBACK``
below.

The inline fallbacks are a deliberate, *tested* resilience seam — the voice
agent must still get a system prompt if the prompt registry is unreachable.
They are pinned byte-identical to the SKILL.md defaults by
``test_voice_prompts.py`` and logged at ERROR when they actually fire:
self-heal, don't silently suppress (feedback_self_heal_not_suppress).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Prompt-registry keys (mirrored in skills/voice/agent/SKILL.md).
EMMA_SYSTEM_KEY = "voice.emma_system"
CLAUDE_BRIDGE_TTS_KEY = "voice.claude_bridge_tts"

# --- Inline fallbacks (drift-guarded against the SKILL.md defaults) ----------
# _extract_skill_section() appends exactly one trailing newline, so these end
# in "\n" to stay byte-identical on the fallback path (test_voice_prompts.py).
_EMMA_SYSTEM_FALLBACK = (
    "You are Emma, a concise voice assistant for the operator. "
    "Speak naturally — your output goes through text-to-speech, so "
    "avoid markdown, bullet lists, and code blocks. Use short "
    "sentences. If the operator asks a factual question you don't know the "
    "answer to, say so plainly rather than guessing. Default to "
    "responses under 30 seconds of speech (~80 words) unless they "
    "explicitly ask for a longer one.\n"
)

# One templated prompt for both bridge surfaces — ``{surface}`` is the only
# thing that differed between the old local-mic and phone copies.
_CLAUDE_BRIDGE_TTS_FALLBACK = (
    "You are speaking out loud to the operator over a {surface}. Keep replies "
    "short and natural — under 20 seconds of speech unless they ask for "
    "more. No markdown, no bullet lists, no code blocks; this goes "
    "through TTS. When you take an action (edit a file, run a command, "
    "push a PR), summarise the outcome in one sentence rather than "
    "narrating the steps.\n"
)

_FALLBACKS: dict[str, str] = {
    EMMA_SYSTEM_KEY: _EMMA_SYSTEM_FALLBACK,
    CLAUDE_BRIDGE_TTS_KEY: _CLAUDE_BRIDGE_TTS_FALLBACK,
}


def resolve_voice_prompt(key: str, **kwargs: Any) -> str:
    """Resolve a voice prompt via the prompt registry, falling back loudly.

    Mirrors the resolve-then-fallback pattern used across the pipeline atoms
    (#612): try the UnifiedPromptManager (Langfuse → SKILL.md), and on any
    failure log at ERROR and return the inline fallback so the voice agent
    still has a system prompt. ``**kwargs`` are formatted into the template
    (e.g. ``surface="local mic"``).
    """
    try:
        from services.prompt_manager import get_prompt_manager

        return get_prompt_manager().get_prompt(key, **kwargs)
    except Exception as exc:  # noqa: BLE001 — registry down must not silence voice
        logger.error(
            "[voice_prompts] prompt registry lookup for %r failed (%s) — "
            "using inline fallback",
            key,
            exc,
        )
        return _FALLBACKS[key].format(**kwargs)
