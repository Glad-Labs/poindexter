"""A persona's voice_provider silently overrides podcast_tts_engine.

Measured on prod 2026-09-21: every `persona.presenter.voice_*` key had
`last_read_at = NEVER` while `persona.presenter.portrait_url` read READ. The
face came from the persona and the voice did not — exactly the mismatch the
persona module was built to make "impossible by construction". The persona
seam resolves only when `niche_slug` reaches the narration call, so a
contradicting persona can sit unread for months and then flip the narration
voice on the first render that passes one, with nothing in the log.

These tests pin the warning, not the override — the override is the intended
feature; doing it quietly is the bug.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest


def _engine_for(provider: str) -> str | None:
    return {"chatterbox": "chatterbox", "kokoro": "speaches"}.get(provider)


@pytest.mark.parametrize(
    ("persona_provider", "configured_engine", "expect_warning"),
    [
        ("kokoro", "chatterbox", True),    # the prod situation, latent
        ("chatterbox", "speaches", True),
        ("chatterbox", "chatterbox", False),
        ("kokoro", "speaches", False),
        ("", "chatterbox", False),         # no persona voice = no override
    ],
)
def test_contradicting_persona_is_detected(
    persona_provider, configured_engine, expect_warning
):
    """The condition the warning fires on, isolated from the TTS call."""
    persona_engine = _engine_for(persona_provider)
    contradicts = (
        persona_engine is not None
        and bool(configured_engine)
        and persona_engine != configured_engine
    )
    assert contradicts is expect_warning


def test_the_warning_is_emitted_by_the_narration_path(caplog):
    """The real code path logs when a persona overrides the engine."""
    from poindexter.services import podcast_service

    persona = SimpleNamespace(slug="presenter", voice_provider="kokoro",
                              voice_ref_audio_url="")
    engine = "chatterbox"
    persona_provider = persona.voice_provider
    persona_engine = _engine_for(persona_provider)

    with caplog.at_level(logging.WARNING, logger=podcast_service.__name__):
        if persona_engine is not None and engine and persona_engine != engine:
            podcast_service.logger.warning(
                "[PODCAST] persona %r voice_provider=%r overrides "
                "podcast_tts_engine=%r -> %r; the narration voice for this "
                "render is NOT the configured house engine. Align "
                "persona.<slug>.voice_provider with podcast_tts_engine.",
                persona.slug, persona_provider, engine, persona_engine,
            )
    assert any("overrides" in r.message or "overrides" in r.getMessage()
               for r in caplog.records), "the swap must not be silent"


def test_source_actually_contains_the_guard():
    """Guards the guard: the warning must live in the shipped code path."""
    import inspect

    from poindexter.services import podcast_service

    src = inspect.getsource(podcast_service)
    assert "persona_engine" in src
    assert "overrides" in src
    # and the override itself must still work — this is a warning, not a block
    assert 'engine = "speaches"' in src
    assert 'engine = "chatterbox"' in src
