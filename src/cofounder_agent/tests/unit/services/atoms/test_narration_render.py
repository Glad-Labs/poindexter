"""Unit tests for the shared narration-TTS helper (poindexter#689).

Pins the contract media.render_narration + podcast.render both depend on:
empty script / no site_config → "" (fail-soft), CTA appended before synth, and
a TTS exception never raises.
"""

from __future__ import annotations

import pytest

from modules.content.atoms import _narration_render


class _SC:
    """Minimal SiteConfig stand-in."""

    def __init__(self, d: dict) -> None:
        self._d = d

    def get(self, k: str, default=None):
        return self._d.get(k, default)


@pytest.mark.asyncio
async def test_empty_script_returns_empty():
    out = await _narration_render.render_narration(
        script="  ", cta_key="media.cta.video", site_config=_SC({}),
        task_id="t1", key="t1_long",
    )
    assert out == ""


@pytest.mark.asyncio
async def test_no_site_config_returns_empty():
    out = await _narration_render.render_narration(
        script="hello", cta_key="media.cta.video", site_config=None,
        task_id="t1", key="t1_long",
    )
    assert out == ""


@pytest.mark.asyncio
async def test_appends_cta_and_synthesizes(monkeypatch):
    seen = {}

    class _PS:
        def __init__(self, *, site_config):
            pass

        async def synthesize(self, text, *, key):
            seen["text"], seen["key"] = text, key
            return "/tmp/out.mp3", 12.0

    monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
    out = await _narration_render.render_narration(
        script="Real narration content.", cta_key="media.cta.video",
        site_config=_SC({"media.cta.video": "Like and subscribe."}),
        task_id="t1", key="t1_long",
    )
    assert out == "/tmp/out.mp3"
    assert seen["text"].endswith("Like and subscribe.")
    assert seen["key"] == "t1_long"


@pytest.mark.asyncio
async def test_no_cta_synthesizes_bare_script(monkeypatch):
    seen = {}

    class _PS:
        def __init__(self, *, site_config):
            pass

        async def synthesize(self, text, *, key):
            seen["text"] = text
            return "/tmp/out.mp3", 1.0

    monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
    out = await _narration_render.render_narration(
        script="Body only.", cta_key="media.cta.video",
        site_config=_SC({}), task_id="t1", key="t1_long",
    )
    assert out == "/tmp/out.mp3"
    assert seen["text"] == "Body only."


@pytest.mark.asyncio
async def test_tts_exception_is_failsoft(monkeypatch):
    class _PS:
        def __init__(self, *, site_config):
            pass

        async def synthesize(self, text, *, key):
            raise RuntimeError("speaches down")

    monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
    out = await _narration_render.render_narration(
        script="Body.", cta_key="media.cta.video",
        site_config=_SC({}), task_id="t1", key="t1_long",
    )
    assert out == ""


class TestStripScriptLabels:
    """#media-render-fixes: structural section labels ('Hook', 'Outro',
    'Segment 2:') are stage directions, not narration — they must never be
    read aloud. The long video shipped with TTS speaking 'Hook' at the top.
    """

    def test_drops_label_only_line(self):
        out = _narration_render._strip_script_labels("Hook\nVRAM is the bottleneck.")
        assert out == "VRAM is the bottleneck."

    def test_drops_bracketed_opening_hook_line(self):
        # The real writer output that leaked "Hook" into the long video — a
        # whole-line bracketed stage direction with a qualifier word.
        out = _narration_render._strip_script_labels(
            "[Opening Hook]\nIn today's GPU world, VRAM is everything.",
        )
        assert out == "In today's GPU world, VRAM is everything."

    def test_strips_leading_bracket_annotation_prefix(self):
        # Bracket annotation and prose on the SAME line — drop the bracket,
        # keep the sentence.
        out = _narration_render._strip_script_labels(
            "[Opening Hook] In today's GPU world, VRAM is everything.",
        )
        assert out == "In today's GPU world, VRAM is everything."

    def test_drops_qualifier_prefixed_label_line(self):
        assert _narration_render._strip_script_labels("Opening Hook") == ""
        assert _narration_render._strip_script_labels("Closing Outro") == ""

    def test_strips_qualifier_prefixed_label_with_separator(self):
        out = _narration_render._strip_script_labels("Final CTA: Subscribe now.")
        assert out == "Subscribe now."

    def test_drops_bracketed_non_label_direction(self):
        # Square-bracket lines are stage directions regardless of content —
        # "[pause]", "[music swells]" must never be spoken.
        for raw in ("[pause]", "[music swells]", "[beat]"):
            assert _narration_render._strip_script_labels(raw) == ""

    def test_strips_label_prefix_keeps_sentence(self):
        out = _narration_render._strip_script_labels("Hook: VRAM is the bottleneck.")
        assert out == "VRAM is the bottleneck."

    def test_strips_marked_up_label_line(self):
        # Markdown emphasis / heading marks around a bare label still drop.
        for raw in ("**Outro**", "## Intro", "> Narrator:", "[Segment 2]"):
            assert _narration_render._strip_script_labels(raw) == ""

    def test_strips_numbered_segment_prefix(self):
        out = _narration_render._strip_script_labels(
            "Segment 2: Quantization shrinks the weights.",
        )
        assert out == "Quantization shrinks the weights."

    def test_preserves_prose_starting_with_label_word(self):
        # 'Body cameras...' must NOT be mistaken for a 'Body' label — a
        # label only matches when followed by a separator or alone.
        text = "Body cameras changed policing forever."
        assert _narration_render._strip_script_labels(text) == text

    def test_preserves_multi_paragraph_body(self):
        text = "First real sentence.\n\nSecond real sentence."
        assert _narration_render._strip_script_labels(text) == text

    @pytest.mark.asyncio
    async def test_label_stripped_before_synthesis(self, monkeypatch):
        """End-to-end: a script whose first line is a 'Hook' label gets the
        label stripped before the text reaches TTS."""
        seen = {}

        class _PS:
            def __init__(self, *, site_config):
                pass

            async def synthesize(self, text, *, key):
                seen["text"] = text
                return "/tmp/out.mp3", 5.0

        monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
        out = await _narration_render.render_narration(
            script="Hook\nLocal LLMs are eating the cloud's lunch.",
            cta_key="media.cta.video",
            site_config=_SC({}),
            task_id="t1",
            key="t1_long",
        )
        assert out == "/tmp/out.mp3"
        assert "Hook" not in seen["text"]
        assert seen["text"].startswith("Local LLMs")
