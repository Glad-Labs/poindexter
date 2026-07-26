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
        script="The pipeline shipped a silent video.", cta_key="media.cta.video",
        site_config=_SC({}), task_id="t1", key="t1_long",
    )
    assert out == ""
    # This used to pass `script="Body."` — but "Body" is a section label, so
    # _strip_script_labels reduced it to empty and render_narration returned at
    # the `not text` guard, never reaching the TTS call this test names. It
    # asserted the right value for the wrong reason. Real prose fixes it
    # (found while adding the #910 finding tests below).


class TestNarrationFailureFinding:
    """#910 — a narration miss ships a SILENT, caption-less video (ASR has
    nothing to transcribe), and the only trace used to be one WARNING log line.
    At least 4 published posts shipped that way over five weeks before anyone
    noticed. Every failure path must now emit a finding; the fail-soft ""
    return is unchanged (the graph must not halt).
    """

    @pytest.mark.asyncio
    async def test_tts_exception_emits_finding(self, monkeypatch):
        class _PS:
            def __init__(self, *, site_config):
                pass

            async def synthesize(self, text, *, key):
                raise RuntimeError("speaches down")

        monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
        seen = []
        monkeypatch.setattr(
            _narration_render, "emit_finding",
            lambda **kw: seen.append(kw),
        )

        out = await _narration_render.render_narration(
            script="The pipeline shipped a silent video.", cta_key="media.cta.video",
            site_config=_SC({}), task_id="t1", key="t1_long",
        )

        assert out == ""  # still fail-soft
        assert len(seen) == 1
        f = seen[0]
        assert f["kind"] == "narration_synthesis_failed"
        assert f["severity"] == "warn"
        assert "speaches down" in f["extra"]["reason"]
        assert f["extra"]["key"] == "t1_long"
        assert f.get("dedup_key")

    @pytest.mark.asyncio
    async def test_empty_path_without_exception_emits_finding(self, monkeypatch):
        """The provider can swallow an all-voices-failed error and return an
        empty path. Same silent-video outcome, so it needs the same finding —
        this is why the emit can't live only in the `except` block."""
        class _PS:
            def __init__(self, *, site_config):
                pass

            async def synthesize(self, text, *, key):
                return "", 0

        monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
        seen = []
        monkeypatch.setattr(
            _narration_render, "emit_finding",
            lambda **kw: seen.append(kw),
        )

        out = await _narration_render.render_narration(
            script="The pipeline shipped a silent video.", cta_key="media.cta.video",
            site_config=_SC({}), task_id="t1", key="t1_long",
        )

        assert out == ""
        assert len(seen) == 1
        assert seen[0]["kind"] == "narration_synthesis_failed"
        assert "no audio path" in seen[0]["extra"]["reason"]

    @pytest.mark.asyncio
    async def test_missing_site_config_emits_finding(self, monkeypatch):
        """There IS a script and we're dropping it — a misconfiguration, not
        'nothing to say'."""
        seen = []
        monkeypatch.setattr(
            _narration_render, "emit_finding",
            lambda **kw: seen.append(kw),
        )

        out = await _narration_render.render_narration(
            script="The pipeline shipped a silent video.", cta_key="media.cta.video",
            site_config=None, task_id="t1", key="t1_long",
        )

        assert out == ""
        assert len(seen) == 1
        assert "site_config missing" in seen[0]["extra"]["reason"]

    @pytest.mark.asyncio
    async def test_empty_script_stays_quiet(self, monkeypatch):
        """Nothing to narrate is a real no-op, not a degradation. Emitting here
        would put a finding on every post with no video script."""
        seen = []
        monkeypatch.setattr(
            _narration_render, "emit_finding",
            lambda **kw: seen.append(kw),
        )

        out = await _narration_render.render_narration(
            script="   ", cta_key="media.cta.video",
            site_config=_SC({}), task_id="t1", key="t1_long",
        )

        assert out == ""
        assert seen == []

    @pytest.mark.asyncio
    async def test_success_emits_nothing(self, monkeypatch):
        class _PS:
            def __init__(self, *, site_config):
                pass

            async def synthesize(self, text, *, key):
                return "/tmp/n.mp3", 12

        monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
        seen = []
        monkeypatch.setattr(
            _narration_render, "emit_finding",
            lambda **kw: seen.append(kw),
        )

        out = await _narration_render.render_narration(
            script="The pipeline shipped a silent video.", cta_key="media.cta.video",
            site_config=_SC({}), task_id="t1", key="t1_long",
        )

        assert out == "/tmp/n.mp3"
        assert seen == []


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


class TestComposeNarrationText:
    """compose_narration_text produces the EXACT text render_narration voices —
    labels stripped + CTA appended — so the caption_fidelity check can diff the
    ASR transcript against what was actually spoken, not the raw script. Sharing
    one composition is the whole point: the reference can never drift from the
    audio.
    """

    def test_strips_labels_and_appends_cta(self):
        out = _narration_render.compose_narration_text(
            script="Hook\nLocal LLMs are eating the cloud's lunch.",
            cta_key="media.cta.video",
            site_config=_SC({"media.cta.video": "Like and subscribe."}),
        )
        assert out == "Local LLMs are eating the cloud's lunch.\n\nLike and subscribe."

    def test_no_cta_returns_stripped_script_only(self):
        out = _narration_render.compose_narration_text(
            script="Hook\nBody only.",
            cta_key="media.cta.video",
            site_config=_SC({}),
        )
        assert out == "Body only."

    def test_empty_script_returns_empty(self):
        out = _narration_render.compose_narration_text(
            script="   ",
            cta_key="media.cta.video",
            site_config=_SC({"media.cta.video": "CTA text."}),
        )
        assert out == ""

    def test_none_site_config_returns_stripped_script(self):
        out = _narration_render.compose_narration_text(
            script="Hook\nLocal models keep winning.",
            cta_key="media.cta.video", site_config=None,
        )
        assert out == "Local models keep winning."

    @pytest.mark.asyncio
    async def test_render_voices_exactly_compose_output(self, monkeypatch):
        """render_narration synthesizes exactly compose_narration_text(...) — the
        single source of truth guaranteeing the fidelity reference matches the
        voiced audio."""
        seen = {}

        class _PS:
            def __init__(self, *, site_config):
                pass

            async def synthesize(self, text, *, key):
                seen["text"] = text
                return "/tmp/out.mp3", 5.0

        monkeypatch.setattr("services.podcast_service.PodcastService", _PS)
        sc = _SC({"media.cta.video": "Subscribe now."})
        script = "Opening Hook\nReal narration body."
        await _narration_render.render_narration(
            script=script, cta_key="media.cta.video", site_config=sc,
            task_id="t1", key="t1_long",
        )
        assert seen["text"] == _narration_render.compose_narration_text(
            script=script, cta_key="media.cta.video", site_config=sc,
        )
