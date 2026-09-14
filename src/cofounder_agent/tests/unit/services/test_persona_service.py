"""persona_service — settings-backed presenter records: validation, listing
by prefix, niche resolution, voice inheritance, and the portrait render
against a faked image service + uploader."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.services import persona_service as ps
from poindexter.services.site_config import SiteConfig


def _sc(**kv):
    return SiteConfig(initial_config={k: str(v) for k, v in kv.items()})


def _presenter(**over):
    base = {
        "persona.presenter.display_name": "Presenter",
        "persona.presenter.voice_provider": "kokoro",
        "persona.presenter.voice_id": "",
        "persona.presenter.style_policy": "photoreal",
        "persona.presenter.enabled": "true",
        "media_default_persona": "presenter",
        "podcast_tts_voice": "bf_emma",
    }
    base.update(over)
    return _sc(**base)


class TestValidation:
    def test_slug_rules(self):
        assert ps.validate_slug(" Host-2 ") == "host-2"
        for bad in ("", "-x", "a b", "x" * 65, "Ünï"):
            with pytest.raises(ps.PersonaError):
                ps.validate_slug(bad)

    def test_field_normalisation_and_rejections(self):
        assert ps.validate_field("enabled", "Yes") == "true"
        assert ps.validate_field("enabled", "0") == "false"
        assert ps.validate_field("portrait_seed", " 21 ") == "21"
        assert ps.validate_field("voice_id", "bf_emma") == "bf_emma"
        with pytest.raises(ps.PersonaError):
            ps.validate_field("voice_provider", "elevenlabs")
        with pytest.raises(ps.PersonaError):
            ps.validate_field("style_policy", "anime")
        with pytest.raises(ps.PersonaError):
            ps.validate_field("portrait_seed", "abc")
        with pytest.raises(ps.PersonaError):
            ps.validate_field("nope", "x")


class TestReadAndResolve:
    def test_get_persona_reads_the_key_family(self):
        sc = _presenter(**{"persona.presenter.portrait_seed": "21"})
        p = ps.get_persona(sc, "presenter")
        assert p is not None
        assert (p.display_name, p.voice_provider, p.voice_id, p.style_policy) == ("Presenter", "kokoro", "", "photoreal")
        assert p.enabled is True and p.portrait_seed == 21 and p.has_portrait is False

    def test_unknown_slug_is_none(self):
        assert ps.get_persona(_presenter(), "ghost") is None
        assert ps.get_persona(None, "presenter") is None

    def test_list_personas_scans_the_prefix(self):
        sc = _presenter(**{"persona.host.display_name": "Host", "persona.host.voice_id": "bm_george"})
        assert [p.slug for p in ps.list_personas(sc)] == ["host", "presenter"]

    def test_niche_selection_beats_the_default(self):
        sc = _presenter(**{
            "persona.host.display_name": "Host", "persona.host.voice_id": "bm_george",
            "niche.dev-diary.media.persona": "host",
        })
        assert ps.resolve_persona_for_niche(sc, "dev-diary").slug == "host"
        assert ps.resolve_persona_for_niche(sc, "glad-labs").slug == "presenter"
        assert ps.resolve_persona_for_niche(sc, None).slug == "presenter"

    def test_disabled_persona_resolves_to_none_not_another(self):
        sc = _presenter(**{"persona.presenter.enabled": "false"})
        assert ps.resolve_persona_for_niche(sc, "glad-labs") is None

    def test_dangling_reference_resolves_to_none(self):
        sc = _presenter(media_default_persona="missing")
        assert ps.resolve_persona_for_niche(sc, None) is None

    def test_voice_inherits_podcast_tts_voice_when_unset(self):
        sc = _presenter()
        assert ps.persona_voice(ps.get_persona(sc, "presenter"), sc) == "bf_emma"
        pinned = _presenter(**{"persona.presenter.voice_id": "bf_isabella"})
        assert ps.persona_voice(ps.get_persona(pinned, "presenter"), pinned) == "bf_isabella"
        assert ps.persona_voice(None, sc) == "bf_emma"

    def test_default_portrait_prompt_follows_style(self):
        photo = ps.Persona(slug="a", description="a calm host in his forties")
        styl = ps.Persona(slug="b", description="a calm host", style_policy="stylized")
        assert "Editorial photograph of a calm host in his forties" in ps.default_portrait_prompt(photo)
        assert ps.default_portrait_prompt(styl).startswith("Flat vector illustration of a calm host")
        assert "no text" in ps.default_portrait_prompt(photo)


class TestWrite:
    @pytest.mark.asyncio
    async def test_upsert_validates_and_writes_each_key(self):
        svc = MagicMock()
        svc.set = AsyncMock()
        stored = await ps.upsert_persona(svc, "Host", {"display_name": "Host", "enabled": "yes", "voice_id": "bm_george"})
        assert stored == {"display_name": "Host", "enabled": "true", "voice_id": "bm_george"}
        keys = [c.args[0] for c in svc.set.await_args_list]
        assert keys == ["persona.host.display_name", "persona.host.enabled", "persona.host.voice_id"]
        assert all(c.kwargs["category"] == "media" for c in svc.set.await_args_list)

    @pytest.mark.asyncio
    async def test_upsert_rejects_bad_values_before_writing(self):
        svc = MagicMock()
        svc.set = AsyncMock()
        with pytest.raises(ps.PersonaError):
            await ps.upsert_persona(svc, "host", {"style_policy": "anime"})
        svc.set.assert_not_awaited()


class TestPortrait:
    @pytest.mark.asyncio
    async def test_render_portrait_renders_uploads_and_returns_url(self, tmp_path):
        persona = ps.Persona(slug="presenter", description="a presenter")

        async def fake_generate(prompt, output_path, **_):
            with open(output_path, "wb") as fh:
                fh.write(b"PNG")
            return MagicMock(ok=True, reason=None, detail=None)

        image_svc = MagicMock()

        image_svc.generate_image_result = fake_generate
        uploader = MagicMock()
        uploader.upload_to_r2 = AsyncMock(return_value="https://cdn/personas/presenter-7.png")
        with patch("poindexter.services.image_service.get_image_service", return_value=image_svc), \
             patch("poindexter.services.r2_upload_service.R2UploadService", return_value=uploader):
            url, prompt, local = await ps.render_portrait(persona, site_config=_presenter(), seed=7, output_dir=str(tmp_path))
        assert url == "https://cdn/personas/presenter-7.png"
        assert prompt.startswith("Editorial photograph of a presenter")
        assert local.endswith("presenter-7.png")
        key = uploader.upload_to_r2.await_args.args[1]
        assert key == "personas/presenter-7.png"

    @pytest.mark.asyncio
    async def test_render_portrait_fails_loud_when_the_image_service_declines(self, tmp_path):
        persona = ps.Persona(slug="presenter")
        image_svc = MagicMock()
        image_svc.generate_image_result = AsyncMock(return_value=MagicMock(ok=False, reason="gpu busy", detail=None))
        with patch("poindexter.services.image_service.get_image_service", return_value=image_svc):
            with pytest.raises(ps.PersonaError, match="gpu busy"):
                await ps.render_portrait(persona, site_config=_presenter(), output_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_upload_portrait_requires_the_file_and_a_url(self, tmp_path):
        persona = ps.Persona(slug="presenter")
        with pytest.raises(ps.PersonaError, match="not found"):
            await ps.upload_portrait(persona, str(tmp_path / "nope.png"), site_config=_presenter())
        f = tmp_path / "face.png"
        f.write_bytes(b"PNG")
        uploader = MagicMock()
        uploader.upload_to_r2 = AsyncMock(return_value=None)
        with patch("poindexter.services.r2_upload_service.R2UploadService", return_value=uploader):
            with pytest.raises(ps.PersonaError, match="upload failed"):
                await ps.upload_portrait(persona, str(f), site_config=_presenter())
