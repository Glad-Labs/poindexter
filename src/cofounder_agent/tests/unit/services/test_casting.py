"""Casting (poindexter#1134): a request in, a drawn and voiced cast out.

Pins the contract the writer and director rely on: a casting reply is checked
in code (reused ids exist, voices come from the catalog, new characters have
looks) with one FIX repair; new characters become personas; each portrait is
checked for exactly that one character and redrawn; a voice that cannot be
cloned stays on Kokoro; and the outputs land on declared PipelineState
channels, so LangGraph does not drop them before the writer runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from poindexter.modules.content.atoms import story_cast_characters as atom
from poindexter.services import casting
from poindexter.services import screenplay as sp
from poindexter.services.site_config import SiteConfig

CATALOG = {"am_echo": "clear young man", "bf_emma": "gentle woman", "bm_george": "deep older man"}


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex",
        "persona.dex.appearance": "a young fox engineer in goggles",
        "persona.dex.portrait_url": "https://cdn.example/personas/dex.png",
        "persona.dex.enabled": "true",
        "persona.off.display_name": "Off",
        "persona.off.enabled": "false",
        "story_voice_catalog": "; ".join(f"{k}: {v}" for k, v in CATALOG.items()),
        "site_name": "Glad Labs",
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


def _new(cid="wren", voice="bf_emma", **extra):
    return {"id": cid, "name": cid.title(), "role": "the hero", "appearance": "a small girl in a red coat",
            "personality": "brave", "voice": voice, "energy": "high", "sample_line": "Who goes there?", **extra}


def _reply(*chars, style="soft watercolor", narrator=None):
    return {"style": style, "characters": list(chars), "narrator": narrator or {"use": False}}


class FakeSettings:
    def __init__(self):
        self.rows: dict[str, str] = {}

    async def set(self, key, value, category=None, description=None):
        self.rows[key] = value


def _check(reply, required=(), max_new=4, roster=("dex",)):
    return casting.check_cast(reply, roster=dict.fromkeys(roster), catalog=CATALOG,
                              required=tuple(required), max_new=max_new)


class TestCheckCast:
    def test_a_clean_reply_passes(self):
        assert _check(_reply({"reuse": "dex"}, _new())) == []

    def test_reusing_an_unknown_character_is_named(self):
        errors = _check(_reply({"reuse": "ghost"}))
        assert any("'ghost' is not an existing character" in e for e in errors)

    def test_a_voice_outside_the_catalog_is_rejected(self):
        assert any('"voice" must be one of' in e for e in _check(_reply(_new(voice="af_made_up"))))

    def test_a_new_character_needs_looks(self):
        assert any('"appearance"' in e for e in _check(_reply(_new(appearance=""))))

    def test_a_required_character_must_be_reused(self):
        assert any("'dex' must be in the cast" in e for e in _check(_reply(_new()), required=["dex"]))

    def test_new_characters_are_capped(self):
        reply = _reply(_new("a"), _new("b"), _new("c"))
        assert any("at most 2 new characters" in e for e in _check(reply, max_new=2))

    def test_a_style_is_required(self):
        assert any('"style"' in e for e in _check(_reply(_new(), style="")))

    def test_a_new_narrator_needs_a_catalog_voice(self):
        errors = _check(_reply(_new(), narrator={"use": True, "reuse": "", "voice": "nope"}))
        assert any(e.startswith("FIX narrator") for e in errors)

    def test_an_empty_cast_is_one_fix(self):
        assert _check({"style": "x", "characters": []}) == ['FIX: add a "characters" list with at least one character']


class TestHelpers:
    def test_the_default_catalog_parses(self):
        catalog = casting.voice_catalog(SiteConfig(initial_config={}))
        assert len(catalog) >= 10 and all(catalog.values())
        assert "bm_george" in catalog

    def test_a_clashing_id_gets_a_suffix(self):
        assert casting._unique_slug("dex", {"dex", "dex-2"}) == "dex-3"

    def test_ids_are_slugged(self):
        assert casting._new_id({"name": "Old Tom!"}) == "old-tom"

    def test_unknown_energy_is_medium(self):
        assert casting._energy("frantic") == "medium"

    def test_the_prompt_renders_with_every_variable(self):
        text = sp._prompt(
            "story.casting", site_name="S", topic="t", brief="b", format_name="story",
            target_seconds=90, roster_block="- dex", required="dex", style_hint="ink",
            voice_catalog="- am_echo: x", max_new=3,
        )
        assert "at most 3 new ones" in text and '"characters"' in text

    def test_the_request_lowercases_required_slugs(self):
        req = casting.build_casting_request(
            {"topic": "a fable", "screenplay_cast": "Dex, Nova", "screenplay_narrator": "None"}, _sc())
        assert req.required == ("dex", "nova") and req.narrator == "none"

    def test_a_topic_is_required(self):
        with pytest.raises(casting.CastingError):
            casting.build_casting_request({}, _sc())


@pytest.fixture
def chat(monkeypatch):
    import poindexter.services.llm_text as llm_text

    mock = AsyncMock()
    monkeypatch.setattr(llm_text, "ollama_chat_text", mock)
    monkeypatch.setattr(sp, "_resolve_model", lambda sc: "ollama/test-writer")
    return mock


@pytest.fixture
def studio(monkeypatch, tmp_path):
    """Stub the GPU and storage side of casting; record what was drawn."""
    drawn: list[dict] = []

    async def fake_draw(**kw):
        drawn.append(kw)
        path = tmp_path / f"{kw['slug']}.png"
        path.write_bytes(b"png")
        return str(path), f"{kw['style']}, portrait of {kw['appearance']}"

    async def fake_upload(persona, local, *, site_config):
        return f"https://cdn.example/personas/{persona.slug}.png"

    async def fake_voice(slug, voice_id, energy, sample, *, site_config):
        return {"voice_provider": "chatterbox", "voice_id": voice_id,
                "voice_ref_audio_url": f"/app/voices/cast/{slug}.wav", "voice_exaggeration": "0.9"}

    async def fake_fetch(url, dest):
        Path(dest).write_bytes(b"png")
        return str(dest)

    import poindexter.services.persona_service as ps
    import poindexter.services.video_renderers.shot_list_renderer as slr

    monkeypatch.setattr(casting, "draw_portrait", fake_draw)
    monkeypatch.setattr(casting, "build_voice", fake_voice)
    monkeypatch.setattr(casting, "_fetch", fake_fetch)
    monkeypatch.setattr(ps, "upload_portrait", fake_upload)
    monkeypatch.setattr(slr, "_clear_image_gen_for_hero", AsyncMock())
    return SimpleNamespace(drawn=drawn)


class TestCastCharacters:
    @pytest.mark.asyncio
    async def test_reuses_and_creates_then_hands_on_the_cast(self, chat, studio):
        chat.side_effect = [json.dumps(_reply({"reuse": "dex"}, _new("wren"), _new("old-tom", voice="bm_george")))]
        settings = FakeSettings()
        out = await casting.cast_characters(
            casting.CastingRequest(topic="a fable"), site_config=_sc(), pool=None, settings=settings)
        assert out["screenplay_cast"] == ["dex", "wren", "old-tom"]
        assert out["screenplay_visual_style"] == "soft watercolor"
        assert out["screenplay_narrator"] == "none"
        assert out["cast_sheet"]["created"] == ["wren", "old-tom"]
        assert settings.rows["persona.wren.appearance"] == "a small girl in a red coat"
        assert settings.rows["persona.wren.style_policy"] == "stylized"
        assert settings.rows["persona.wren.portrait_url"].endswith("/wren.png")
        assert settings.rows["persona.old-tom.voice_provider"] == "chatterbox"
        assert settings.rows["persona.old-tom.voice_ref_audio_url"] == "/app/voices/cast/old-tom.wav"
        assert "persona.dex.appearance" not in settings.rows  # reused, untouched
        # Dex's face anchors the first new portrait; each new face anchors the next.
        assert [Path(a).name for a in studio.drawn[0]["anchors"]] == ["anchor_dex.png"]
        assert [Path(a).name for a in studio.drawn[1]["anchors"]] == ["anchor_dex.png", "wren.png"]
        assert chat.await_args.kwargs["phase"] == "story_cast_characters"

    @pytest.mark.asyncio
    async def test_an_asked_for_style_wins_and_old_faces_do_not_anchor(self, chat, studio):
        chat.side_effect = [json.dumps(_reply({"reuse": "dex"}, _new()))]
        out = await casting.cast_characters(
            casting.CastingRequest(topic="t", style_hint="ink and wash"),
            site_config=_sc(), pool=None, settings=FakeSettings())
        assert out["screenplay_visual_style"] == "ink and wash"
        assert studio.drawn[0]["anchors"] == [] and studio.drawn[0]["style"] == "ink and wash"

    @pytest.mark.asyncio
    async def test_a_bad_reply_gets_one_repair(self, chat, studio):
        chat.side_effect = [json.dumps(_reply(_new(voice="nope"))), json.dumps(_reply(_new()))]
        out = await casting.cast_characters(
            casting.CastingRequest(topic="t"), site_config=_sc(), pool=None, settings=FakeSettings())
        assert out["screenplay_cast"] == ["wren"] and chat.await_count == 2
        assert '"voice" must be one of' in chat.await_args_list[1].args[0]

    @pytest.mark.asyncio
    async def test_two_bad_replies_raise(self, chat, studio):
        chat.side_effect = [json.dumps(_reply(_new(voice="nope")))] * 2
        with pytest.raises(casting.CastingError):
            await casting.cast_characters(
                casting.CastingRequest(topic="t"), site_config=_sc(), pool=None, settings=FakeSettings())

    @pytest.mark.asyncio
    async def test_a_required_character_that_is_not_a_persona_fails_before_the_llm(self, chat, studio):
        with pytest.raises(casting.CastingError, match="off"):
            await casting.cast_characters(
                casting.CastingRequest(topic="t", required=("off",)), site_config=_sc(), pool=None,
                settings=FakeSettings())
        assert chat.await_count == 0

    @pytest.mark.asyncio
    async def test_a_new_narrator_is_a_voice_only_persona(self, chat, studio):
        narrator = {"use": True, "reuse": "", "name": "Storyteller", "voice": "bm_george",
                    "energy": "low", "sample_line": "Once upon a time."}
        chat.side_effect = [json.dumps(_reply(_new(), narrator=narrator))]
        settings = FakeSettings()
        out = await casting.cast_characters(
            casting.CastingRequest(topic="t"), site_config=_sc(), pool=None, settings=settings)
        assert out["screenplay_narrator"] == "storyteller"
        assert "storyteller" not in out["screenplay_cast"]
        assert settings.rows["persona.storyteller.voice_provider"] == "chatterbox"
        assert "persona.storyteller.portrait_url" not in settings.rows

    @pytest.mark.asyncio
    async def test_a_narrator_the_request_named_is_kept(self, chat, studio):
        chat.side_effect = [json.dumps(_reply(_new(), narrator={"use": True, "reuse": "dex"}))]
        out = await casting.cast_characters(
            casting.CastingRequest(topic="t", narrator="none"), site_config=_sc(), pool=None,
            settings=FakeSettings())
        assert out["screenplay_narrator"] == "none"

    @pytest.mark.asyncio
    async def test_a_portrait_that_cannot_be_drawn_leaves_a_voice(self, chat, studio, monkeypatch):
        async def no_draw(**kw):
            return None, "prompt"

        monkeypatch.setattr(casting, "draw_portrait", no_draw)
        chat.side_effect = [json.dumps(_reply(_new()))]
        settings = FakeSettings()
        out = await casting.cast_characters(
            casting.CastingRequest(topic="t"), site_config=_sc(), pool=None, settings=settings)
        assert out["cast_sheet"]["portraits"] == {}
        assert "persona.wren.portrait_url" not in settings.rows
        assert settings.rows["persona.wren.voice_provider"] == "chatterbox"


class TestDrawPortrait:
    @pytest.mark.asyncio
    async def test_a_rejected_draw_is_redrawn_with_the_references(self, monkeypatch, tmp_path):
        import poindexter.services.reference_frames as rf
        import poindexter.services.video_renderers.shot_vision_qa as qa

        calls = []

        async def fake_ref(**kw):
            calls.append(kw)
            Path(kw["output_path"]).write_bytes(b"png")
            return True, ""

        verdicts = iter([qa.TalkingFrameVerdict(ok=False, people=2, reason="two people"),
                         qa.TalkingFrameVerdict(ok=True, people=1)])
        monkeypatch.setattr(rf, "render_reference_frame", fake_ref)
        monkeypatch.setattr(qa, "check_cast_frame", AsyncMock(side_effect=lambda **kw: next(verdicts)))
        local, prompt = await casting.draw_portrait(
            slug="wren", name="Wren", appearance="a girl in a red coat", style="watercolor",
            anchors=["/a.png"], work=tmp_path, site_config=_sc(), pool=None,
        )
        assert local == str(tmp_path / "wren.png") and len(calls) == 2
        assert prompt.startswith("watercolor, character portrait of a girl in a red coat")
        assert calls[0]["reference_paths"] == ["/a.png"] and calls[0]["width"] == 1024

    @pytest.mark.asyncio
    async def test_without_anchors_the_image_service_draws(self, monkeypatch, tmp_path):
        import poindexter.services.image_service as img
        import poindexter.services.video_renderers.shot_vision_qa as qa

        async def gen(prompt, out):
            Path(out).write_bytes(b"png")
            return SimpleNamespace(ok=True)

        monkeypatch.setattr(img, "get_image_service", lambda sc: SimpleNamespace(generate_image_result=gen))
        monkeypatch.setattr(qa, "check_cast_frame", AsyncMock(return_value=qa.TalkingFrameVerdict(ok=None)))
        local, _ = await casting.draw_portrait(
            slug="wren", name="Wren", appearance="x", style="ink", anchors=[], work=tmp_path,
            site_config=_sc(), pool=None,
        )
        assert local == str(tmp_path / "wren.png")

    @pytest.mark.asyncio
    async def test_no_draw_at_all_is_none(self, monkeypatch, tmp_path):
        import poindexter.services.image_service as img

        async def gen(prompt, out):
            return SimpleNamespace(ok=False, reason="busy")

        monkeypatch.setattr(img, "get_image_service", lambda sc: SimpleNamespace(generate_image_result=gen))
        local, _ = await casting.draw_portrait(
            slug="wren", name="Wren", appearance="x", style="ink", anchors=[], work=tmp_path,
            site_config=_sc(story_portrait_attempts="2"), pool=None,
        )
        assert local is None


class TestBuildVoice:
    @pytest.mark.asyncio
    async def test_kokoro_engine_keeps_the_catalog_voice(self):
        out = await casting.build_voice("wren", "bf_emma", "high", "Hi.",
                                        site_config=_sc(story_cast_voice_engine="kokoro"))
        assert out == {"voice_provider": "kokoro", "voice_id": "bf_emma"}

    @pytest.mark.asyncio
    async def test_the_sample_line_becomes_a_chatterbox_reference(self, monkeypatch, tmp_path):
        import poindexter.services.podcast_service as pod

        spoken = []

        class FakePodcast:
            def __init__(self, *, site_config):
                pass

            async def synthesize_line(self, text, persona, path):
                spoken.append((text, persona.voice_provider, persona.voice_id))
                Path(path).write_bytes(b"mp3")
                return str(path)

        async def fake_wav(src, dst):
            Path(dst).write_bytes(b"wav")
            return True

        monkeypatch.setattr(pod, "PodcastService", FakePodcast)
        monkeypatch.setattr(casting, "_ffmpeg_to_wav", fake_wav)
        out = await casting.build_voice(
            "wren", "bf_emma", "high", "Who goes there?",
            site_config=_sc(story_cast_voice_dir=str(tmp_path)),
        )
        assert spoken == [("Who goes there?", "kokoro", "bf_emma")]
        assert out["voice_provider"] == "chatterbox"
        assert out["voice_ref_audio_url"] == "/app/voices/cast/wren.wav"
        assert out["voice_exaggeration"] == "0.9"
        assert (tmp_path / "wren.wav").exists()

    @pytest.mark.asyncio
    async def test_a_failed_clip_stays_on_kokoro(self, monkeypatch, tmp_path):
        import poindexter.services.podcast_service as pod

        class Broken:
            def __init__(self, *, site_config):
                pass

            async def synthesize_line(self, text, persona, path):
                raise RuntimeError("speaches down")

        monkeypatch.setattr(pod, "PodcastService", Broken)
        out = await casting.build_voice("wren", "bf_emma", "low", "Hi.",
                                        site_config=_sc(story_cast_voice_dir=str(tmp_path)))
        assert out == {"voice_provider": "kokoro", "voice_id": "bf_emma"}


class TestAtom:
    def test_outputs_are_declared_pipeline_state_channels(self):
        from poindexter.services.template_runner import PipelineState

        produces = set(atom.ATOM_META.produces)
        assert produces == {"screenplay_cast", "screenplay_visual_style", "screenplay_narrator", "cast_sheet"}
        assert produces <= set(PipelineState.__annotations__)

    @pytest.mark.asyncio
    async def test_run_reads_state_and_returns_the_cast(self, monkeypatch):
        seen = {}

        async def fake_cast(request, *, site_config, pool, task_id):
            seen.update(request=request, task_id=task_id)
            return {"screenplay_cast": ["wren"], "screenplay_visual_style": "ink",
                    "screenplay_narrator": "none", "cast_sheet": {}}

        monkeypatch.setattr(casting, "cast_characters", fake_cast)
        out = await atom.run({"site_config": _sc(), "topic": "a fable", "task_id": "t1",
                              "screenplay_visual_style": "ink"})
        assert out["screenplay_cast"] == ["wren"]
        assert seen["request"].style_hint == "ink" and seen["task_id"] == "t1"
