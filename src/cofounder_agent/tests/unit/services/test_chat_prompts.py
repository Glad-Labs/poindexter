"""Contract for the Cofounder chat system prompt (services/chat_prompts.py).

Pins (mirrors test_voice_prompts.py — poindexter#947):

  - ``chat.system`` resolves from skills/chat/agent/SKILL.md,
  - the inline fallback stays byte-identical to the SKILL.md default (drift),
  - the fallback logs loud (ERROR) when it fires,
  - both placeholders render.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from services.chat_prompts import CHAT_SYSTEM_KEY, resolve_chat_prompt
from services.prompt_manager import UnifiedPromptManager

_PATCH_TARGET = "services.prompt_manager.get_prompt_manager"
_KW = {"persona_name": "Poindexter", "tool_names": "list_tasks, create_post"}


@pytest.mark.unit
def test_chat_system_key_registered_from_skill():
    pm = UnifiedPromptManager()
    assert CHAT_SYSTEM_KEY in pm.prompts, (
        f"{CHAT_SYSTEM_KEY} is not registered from skills/chat/agent/SKILL.md"
    )


@pytest.mark.unit
def test_chat_skill_default_matches_inline_fallback():
    skill_path = resolve_chat_prompt(CHAT_SYSTEM_KEY, **_KW)
    with patch(_PATCH_TARGET, side_effect=RuntimeError("registry down")):
        fallback_path = resolve_chat_prompt(CHAT_SYSTEM_KEY, **_KW)
    assert skill_path == fallback_path, (
        "chat SKILL.md default and inline fallback have drifted — update "
        "services/chat_prompts.py to match skills/chat/agent/SKILL.md"
    )


@pytest.mark.unit
def test_placeholders_render():
    rendered = resolve_chat_prompt(
        CHAT_SYSTEM_KEY, persona_name="Ada", tool_names="alpha, beta",
    )
    assert "Ada" in rendered
    assert "alpha, beta" in rendered
    assert "{persona_name}" not in rendered
    assert "{tool_names}" not in rendered


@pytest.mark.unit
def test_fallback_logs_error(caplog):
    with patch(_PATCH_TARGET, side_effect=RuntimeError("registry down")):
        with caplog.at_level(logging.ERROR, logger="services.chat_prompts"):
            resolve_chat_prompt(CHAT_SYSTEM_KEY, **_KW)
    assert any("inline fallback" in r.message for r in caplog.records), (
        "fallback fired silently — it must log at ERROR "
        "(feedback_self_heal_not_suppress)"
    )


@pytest.mark.unit
def test_unknown_key_with_registry_down_raises():
    with patch(_PATCH_TARGET, side_effect=RuntimeError("registry down")):
        with pytest.raises(RuntimeError):
            resolve_chat_prompt("chat.nonexistent")
