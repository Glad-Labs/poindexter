"""Contract for the voice-agent prompts (services/voice_prompts.py).

The local-mic (`voice_agent`) and phone/LiveKit (`voice_agent_livekit`) surfaces
share one Claude-bridge TTS system prompt; it lived as two near-identical inline
literals (differing only in the surface word). This pins:

  - both voice keys resolve from skills/voice/agent/SKILL.md,
  - the inline fallback stays byte-identical to the SKILL.md default (drift),
  - the fallback logs loud when it fires (self-heal, don't suppress), and
  - the local-mic / phone overrides are one templated key, not two copies.

`voice_prompts` carries NO pipecat/livekit imports, so it is unit-testable
without the voice runtime's heavy deps.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from services.prompt_manager import UnifiedPromptManager
from services.voice_prompts import (
    CLAUDE_BRIDGE_TTS_KEY,
    EMMA_SYSTEM_KEY,
    resolve_voice_prompt,
)

_PATCH_TARGET = "services.prompt_manager.get_prompt_manager"

_CASES = [
    ("emma", EMMA_SYSTEM_KEY, {}),
    ("claude_bridge_local", CLAUDE_BRIDGE_TTS_KEY, {"surface": "local mic"}),
    ("claude_bridge_phone", CLAUDE_BRIDGE_TTS_KEY, {"surface": "phone call"}),
]
_IDS = [c[0] for c in _CASES]


@pytest.mark.unit
@pytest.mark.parametrize("name,key,_kw", _CASES, ids=_IDS)
def test_voice_key_registered_from_skill(name, key, _kw):
    pm = UnifiedPromptManager()
    assert key in pm.prompts, f"{name}: {key} is not registered from the voice skill"


@pytest.mark.unit
@pytest.mark.parametrize("name,key,kw", _CASES, ids=_IDS)
def test_voice_skill_default_matches_inline_fallback(name, key, kw):
    skill_path = resolve_voice_prompt(key, **kw)
    with patch(_PATCH_TARGET, side_effect=RuntimeError("registry down")):
        fallback_path = resolve_voice_prompt(key, **kw)
    assert skill_path == fallback_path, (
        f"{name}: voice SKILL.md default and inline fallback have drifted"
    )


@pytest.mark.unit
def test_voice_fallback_logs_loud_when_registry_down(caplog):
    with patch(_PATCH_TARGET, side_effect=RuntimeError("registry down")):
        with caplog.at_level(logging.ERROR):
            resolve_voice_prompt(EMMA_SYSTEM_KEY)
    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        "voice fallback fired without an ERROR-level log"
    )


@pytest.mark.unit
def test_claude_bridge_override_is_one_templated_key():
    """The local-mic and phone overrides differ only by the surface word —
    one templated prompt, not two divergent copies."""
    local = resolve_voice_prompt(CLAUDE_BRIDGE_TTS_KEY, surface="local mic")
    phone = resolve_voice_prompt(CLAUDE_BRIDGE_TTS_KEY, surface="phone call")
    assert "local mic" in local
    assert "phone call" in phone
    # Identical once the surface word is normalized out.
    assert local.replace("local mic", "<S>") == phone.replace("phone call", "<S>")
