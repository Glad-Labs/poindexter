"""Model sheets: each cast character turned around once, drawn from in wide shots.

Pinned here: the sheet prompt; one panel is cut out of the sheet and trimmed
to the figure (the whole sheet made the image model draw Cinder twice in one
wide shot, live test 2026-10-14); stills draw from that view while talking
frames and a per-piece portrait keep the portrait; ``ensure_turnarounds``
draws only what is missing and never fails a render; and a new portrait
clears the sheet drawn from the old one.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from poindexter.schemas.video_shot_list import Shot
from poindexter.services import model_sheets as ms
from poindexter.services import persona_service as ps
from poindexter.services import reference_frames as rf
from poindexter.services.site_config import SiteConfig
from poindexter.services.video_renderers import shot_list_renderer as slr


def _sc(**extra):
    base = {
        "persona.dex.display_name": "Dex", "persona.dex.enabled": "true",
        "persona.dex.portrait_url": "https://cdn/dex.png",
        "persona.dex.turnaround_url": "https://cdn/dex-turnaround.png",
        "persona.dex.appearance": "a red jacket",
        "persona.nova.display_name": "Nova", "persona.nova.enabled": "true",
        "persona.nova.portrait_url": "https://cdn/nova.png",
        "persona.pip.display_name": "Pip", "persona.pip.enabled": "true",  # no portrait
    }
    base.update(extra)
    return SiteConfig(initial_config=base)


def _sheet(path: Path) -> None:
    """Three panels on a white page: red figure, green figure, blue figure."""
    img = Image.new("RGB", (300, 100), "white")
    for i, colour in enumerate(("red", "green", "blue")):
        img.paste(Image.new("RGB", (20, 60), colour), (100 * i + 40, 20))
    img.save(path)


class TestCrop:
    def test_one_panel_trimmed_to_the_figure(self, tmp_path):
        _sheet(tmp_path / "sheet.png")
        out = ms.crop_sheet_panel(str(tmp_path / "sheet.png"), str(tmp_path / "v.png"), panel=0, panels=3)
        with Image.open(out) as view:
            colours = {c for _, c in view.convert("RGB").getcolors(10_000)}
            assert (255, 0, 0) in colours and (0, 0, 255) not in colours and (0, 128, 0) not in colours
            assert view.width < 100 and view.height < 100  # trimmed, with a margin
            assert view.width > 20 and view.height > 60

    def test_the_panel_setting_picks_the_view_and_is_clamped(self, tmp_path):
        _sheet(tmp_path / "sheet.png")
        for panel, colour in ((2, (0, 0, 255)), (9, (0, 0, 255)), (-1, (255, 0, 0))):
            out = ms.crop_sheet_panel(str(tmp_path / "sheet.png"), str(tmp_path / "v.png"), panel=panel, panels=3)
            with Image.open(out) as view:
                assert colour in {c for _, c in view.convert("RGB").getcolors(10_000)}

    def test_an_unreadable_sheet_is_none(self, tmp_path):
        (tmp_path / "bad.png").write_bytes(b"not a png")
        assert ms.crop_sheet_panel(str(tmp_path / "bad.png"), str(tmp_path / "v.png"), panel=0, panels=3) is None

    def test_settings_read_from_the_registered_defaults(self):
        assert ms.sheet_panel_settings(_sc()) == (0, 3)
        assert ms.sheet_panel_settings(_sc(video_reference_sheet_panel="1", story_turnaround_panels="4")) == (1, 4)


def test_turnaround_prompt_names_the_character_and_their_look():
    prompt = ms.turnaround_prompt("Dex", "a red jacket", _sc())
    assert prompt.startswith("Picture 1 is Dex.") and "a red jacket" in prompt
    assert "{" not in prompt


@pytest.fixture
def stub_refs(monkeypatch, tmp_path):
    seen: dict = {"fetched": [], "calls": []}

    async def fetch(url, dest, factory):
        seen["fetched"].append(url)
        if "turnaround" in url:
            _sheet(Path(dest))
        else:
            Path(dest).write_bytes(b"PNG")
        return str(dest)

    async def draw(**kw):
        seen["calls"].append(kw)
        Path(kw["output_path"]).write_bytes(b"PNG")
        return True, ""

    monkeypatch.setattr(slr, "_fetch_presenter_portrait", fetch)
    monkeypatch.setattr(rf, "render_reference_frame", draw)
    monkeypatch.setattr(slr, "_clear_image_gen_for_hero", AsyncMock())
    return seen


def _shot(**over):
    base = dict(idx=0, duration_s=3.0, intent="x", source="image_kenburns", narration_offset_s=0.0,
                prompt="a wide shot of Dex and Nova on a roof")
    base.update(over)
    return Shot(**base)


class TestReferences:
    @pytest.mark.asyncio
    async def test_a_still_draws_from_the_cut_view(self, stub_refs, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex", "nova"))
        paths, names = await slr._reference_portraits(
            ["dex", "nova", "pip"], ctx, work_dir=tmp_path, site_config=_sc(),
            http_client_factory=None, sheets=True)
        assert [Path(p).name for p in paths] == ["reference_dex_view.png", "reference_nova.png"]
        assert names == ["Dex, shown head to toe", "Nova"]
        with Image.open(paths[0]) as view:
            assert view.width < 100  # one panel, not the sheet

    @pytest.mark.asyncio
    async def test_without_sheets_and_with_a_piece_portrait_the_portrait_is_kept(self, stub_refs, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex",))
        paths, names = await slr._reference_portraits(
            ["dex"], ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        assert [Path(p).name for p in paths] == ["reference_dex.png"] and names == ["Dex"]
        styled = slr.ReferenceContext(cast=("dex",), portrait_urls={"dex": "https://cdn/dex-ink.png"})
        await slr._reference_portraits(
            ["dex"], styled, work_dir=tmp_path, site_config=_sc(), http_client_factory=None, sheets=True)
        assert stub_refs["fetched"][-1] == "https://cdn/dex-ink.png"

    @pytest.mark.asyncio
    async def test_a_sheet_that_cannot_be_fetched_falls_back_to_the_portrait(self, monkeypatch, tmp_path):
        async def fetch(url, dest, factory):
            if "turnaround" in url:
                return None
            Path(dest).write_bytes(b"PNG")
            return str(dest)

        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fetch)
        ctx = slr.ReferenceContext(cast=("dex",))
        paths, names = await slr._reference_portraits(
            ["dex"], ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None, sheets=True)
        assert [Path(p).name for p in paths] == ["reference_dex.png"] and names == ["Dex"]

    @pytest.mark.asyncio
    async def test_stills_use_the_sheet_and_talking_frames_the_portrait(self, stub_refs, tmp_path):
        ctx = slr.ReferenceContext(cast=("dex", "nova"))
        args = dict(width=8, height=8, ctx=ctx, work_dir=tmp_path, site_config=_sc(), http_client_factory=None)
        await slr._render_reference_still(
            _shot(characters=["dex", "nova"]), prompt="a wide shot of Dex and Nova on a roof",
            output_path=str(tmp_path / "wide.png"), **args)
        assert "reference_dex_view.png" in [Path(p).name for p in stub_refs["calls"][-1]["reference_paths"]]
        await slr._render_reference_still(
            _shot(idx=1, characters=["dex"]), prompt="Dex talking", output_path=str(tmp_path / "talk.png"),
            speakers=["dex"], **args)
        assert [Path(p).name for p in stub_refs["calls"][-1]["reference_paths"]][0] == "reference_dex.png"


class TestEnsure:
    @pytest.mark.asyncio
    async def test_only_characters_with_a_portrait_and_no_sheet_are_drawn(self, monkeypatch):
        drawn: list[str] = []

        async def draw(persona, *, site_config, pool=None):
            drawn.append(persona.slug)
            return f"https://cdn/{persona.slug}-turnaround.png"

        monkeypatch.setattr(ms, "draw_turnaround", draw)
        sc = _sc()
        sc.reload = AsyncMock()
        made = await ms.ensure_turnarounds(["dex", "nova", "pip", "nova", "ghost"], site_config=sc, pool=object())
        assert drawn == ["nova"] and made == {"nova": "https://cdn/nova-turnaround.png"}
        sc.reload.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_off_or_failing_never_stops_the_render(self, monkeypatch):
        draw = AsyncMock(side_effect=RuntimeError("card busy"))
        monkeypatch.setattr(ms, "draw_turnaround", draw)
        assert await ms.ensure_turnarounds(["nova"], site_config=_sc(), pool=None) == {}
        assert await ms.ensure_turnarounds(["nova"], site_config=_sc(story_turnarounds_enabled="false"), pool=None) == {}
        assert draw.await_count == 1


class TestDraw:
    @pytest.mark.asyncio
    async def test_drawn_uploaded_and_stored_on_the_persona(self, monkeypatch, tmp_path):
        from poindexter.services import r2_upload_service as r2

        async def fetch(url, dest, factory):
            Path(dest).write_bytes(b"PNG")
            return str(dest)

        async def render(**kw):
            render.kw = kw
            Path(kw["output_path"]).write_bytes(b"PNG")
            return True, ""

        upload = AsyncMock(return_value="https://cdn/nova-turnaround.png")
        upsert = AsyncMock()
        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fetch)
        monkeypatch.setattr(rf, "render_reference_frame", render)
        monkeypatch.setattr(r2.R2UploadService, "upload_to_r2", upload)
        monkeypatch.setattr(ps, "upsert_persona", upsert)
        nova = ps.get_persona(_sc(), "nova")
        url = await ms.draw_turnaround(nova, site_config=_sc(), pool=object())
        assert url == "https://cdn/nova-turnaround.png"
        assert (render.kw["width"], render.kw["height"]) == (1536, 768)
        assert upload.await_args.args[1] == "personas/nova-turnaround.png"
        assert upsert.await_args.args[1:] == ("nova", {"turnaround_url": url})

    @pytest.mark.asyncio
    async def test_a_failed_draw_stores_nothing(self, monkeypatch):
        async def fetch(url, dest, factory):
            Path(dest).write_bytes(b"PNG")
            return str(dest)

        upsert = AsyncMock()
        monkeypatch.setattr(slr, "_fetch_presenter_portrait", fetch)
        monkeypatch.setattr(rf, "render_reference_frame", AsyncMock(return_value=(False, "oom")))
        monkeypatch.setattr(ps, "upsert_persona", upsert)
        assert await ms.draw_turnaround(ps.get_persona(_sc(), "nova"), site_config=_sc(), pool=object()) is None
        assert await ms.draw_turnaround(ps.get_persona(_sc(), "pip"), site_config=_sc(), pool=object()) is None
        upsert.assert_not_awaited()


class _Settings:
    def __init__(self, rows):
        self.rows = dict(rows)

    async def get(self, key, default=None, *, env_fallback=True):
        return self.rows.get(key, default)

    async def set(self, key, value, *, category=None, description=None):
        self.rows[key] = value


class TestPortraitChange:
    @pytest.mark.asyncio
    async def test_a_new_portrait_clears_the_old_sheet(self):
        settings = _Settings({"persona.dex.portrait_url": "https://cdn/dex.png",
                              "persona.dex.turnaround_url": "https://cdn/dex-turnaround.png"})
        await ps.upsert_persona(settings, "dex", {"portrait_url": "https://cdn/dex-2.png"})
        assert settings.rows["persona.dex.turnaround_url"] == ""

    @pytest.mark.asyncio
    async def test_the_same_portrait_or_a_new_sheet_keeps_it(self):
        settings = _Settings({"persona.dex.portrait_url": "https://cdn/dex.png",
                              "persona.dex.turnaround_url": "https://cdn/dex-turnaround.png"})
        await ps.upsert_persona(settings, "dex", {"portrait_url": "https://cdn/dex.png", "voice_id": "am_puck"})
        assert settings.rows["persona.dex.turnaround_url"] == "https://cdn/dex-turnaround.png"
        await ps.upsert_persona(settings, "dex", {"portrait_url": "https://cdn/dex-3.png",
                                                  "turnaround_url": "https://cdn/dex-3-turnaround.png"})
        assert settings.rows["persona.dex.turnaround_url"] == "https://cdn/dex-3-turnaround.png"
