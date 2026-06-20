"""Contract for the voice-agent prompts (services/voice_prompts.py).

The local-mic (`voice_agent`) and phone/LiveKit (`voice_agent_livekit`) surfaces
share one Claude-bridge TTS system prompt; it lived as two near-identical inline
literals (differing only in the surface word). This pins:

  - both voice keys resolve from skills/voice/agent/SKILL.md,
  - the inline fallback stays byte-identical to the SKILL.md default (drift),
  - the fallback logs loud when it fires (self-heal, don't suppress),
  - the local-mic / phone overrides are one templated key, not two copies, and
  - the baseline-seeded `voice_agent_system_prompt` keeps its persona preamble
    byte-identical to the skill default while retaining its tool-calling section.

`voice_prompts` carries NO pipecat/livekit imports, so it is unit-testable
without the voice runtime's heavy deps.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import services
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


_SEEDS_SQL = (
    Path(services.__file__).resolve().parent / "migrations" / "0000_baseline.seeds.sql"
)


def _seeded_setting_value(key: str) -> str:
    """Return the un-escaped VALUE seeded for ``key`` in 0000_baseline.seeds.sql.

    Seed rows are ``INSERT ... VALUES ('key', 'value', 'category', ...)``; the
    value is single-quoted with SQL ``''`` escaping and may span lines. The
    value never contains a bare ``', '`` (its only single quotes are ``''``
    pairs), so a non-greedy capture up to ``', '<category>',`` is unambiguous.
    """
    sql = _SEEDS_SQL.read_text(encoding="utf-8")
    match = re.search(
        rf"\('{re.escape(key)}',\s*'(.*?)',\s*'[^']*',",
        sql,
        re.DOTALL,
    )
    assert match, f"{key} not found in {_SEEDS_SQL.name}"
    return match.group(1).replace("''", "'")


@pytest.mark.unit
def test_seeded_voice_system_prompt_persona_matches_skill():
    """The operator's seeded voice prompt and the OSS skill fallback share one
    persona. The seed is a *superset* — persona preamble + a tool-calling
    section the skill omits — so pin the preamble byte-identical to the skill
    default (no drift) AND assert the tool section survives, so a future "just
    match the skill" edit can't silently break voice tool-calling."""
    seeded = _seeded_setting_value("voice_agent_system_prompt")
    persona = resolve_voice_prompt(EMMA_SYSTEM_KEY).rstrip("\n")

    assert seeded.startswith(persona), (
        "seeded voice_agent_system_prompt persona preamble has drifted from the "
        "voice.emma_system skill default"
    )
    # Tool-calling section preserved (the reason the seed is a superset).
    assert "you SHOULD call them" in seeded
    for tool in (
        "check_pipeline_health",
        "get_published_post_count",
        "get_ai_spending_status",
    ):
        assert tool in seeded, f"seed lost the {tool} tool-calling instruction"
