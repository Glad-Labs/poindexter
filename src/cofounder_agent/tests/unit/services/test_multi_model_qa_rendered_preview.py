"""MultiModelQA._check_rendered_preview_outcome: a verdict, or why there is none.

The rendered-preview leg returned the same bare ``None`` for "switched off"
and for every failure (dead URL, 404 page, capture error, empty model answer),
so it sat dark for months with 0 verdicts in 53 runs and nobody told. The
outcome API separates ``disabled`` (legitimate) from ``failed`` (reported by
the caller) and names the cause. Mirrors ``_web_fact_check_outcome``
(poindexter#1062).

The judge is shown the page as viewport-sized TILES, not one full-page image: a
1280x13141 screenshot reached the judge at half scale (its encoder reads at most
~4.2 megapixels per image), which turned 16 px text into 8 px and produced
"placeholder hero, missing images" verdicts that were not true in 12 of 20 runs.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.modules.content.multi_model_qa import MultiModelQA
from poindexter.services.preview_screenshot import (
    FailedImage,
    PageFacts,
    PageTile,
    TiledScreenshot,
    describe_page_facts,
)
from poindexter.services.site_config import SiteConfig

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"  # not a real PNG: only the legacy URL path ever reads a header
PAGE_HTML = "<!DOCTYPE html><html><body><h1>Draft</h1></body></html>"


def _settings(**values):
    svc = MagicMock()

    async def _get(key):
        return values.get(key)

    svc.get = AsyncMock(side_effect=_get)
    return svc


_ON = {
    "qa_preview_screenshot_enabled": "true",
    "qa_preview_vision_model": "ollama/qwen3-vl:30b-a3b-instruct",
    "qa_preview_pass_threshold": "70",
}


def _qa(site_config=None, **settings) -> MultiModelQA:
    return MultiModelQA(
        pool=None,
        settings_service=_settings(**settings) if settings else None,
        site_config=site_config or SiteConfig(),
    )


def _tile(top: int, bottom: int, marker: bytes = b"") -> PageTile:
    return PageTile(png=FAKE_PNG + marker, top=top, bottom=bottom, width=1280, height=bottom - top)


def _shot(*spans, complete=True, page_height=None, scale=1.0, facts=None) -> TiledScreenshot:
    tiles = tuple(_tile(t, b, marker=bytes([n])) for n, (t, b) in enumerate(spans))
    return TiledScreenshot(
        tiles=tiles, page_width=1280, page_height=page_height or spans[-1][1],
        scale=scale, complete=complete, facts=facts,
    )


@pytest.fixture
def vision(monkeypatch):
    """Stub the screenshot + vision seams; returns the call recorder."""
    calls: dict = {"shot": _shot((0, 1024), (1024, 2048))}

    async def fake_tiles(html, **kwargs):
        calls["html"] = html
        calls["capture_kwargs"] = kwargs
        return calls["shot"]

    async def fake_url_capture(url, **kwargs):
        calls["url"] = url
        return FAKE_PNG

    monkeypatch.setattr(
        "poindexter.services.preview_screenshot.capture_html_tiles", fake_tiles,
    )
    monkeypatch.setattr(
        "poindexter.services.preview_screenshot.capture_preview_screenshot", fake_url_capture,
    )

    prompts = MagicMock()
    prompts.get_prompt = MagicMock(return_value="judge this page")
    calls["prompts"] = prompts
    monkeypatch.setattr(
        "poindexter.modules.content.multi_model_qa.get_prompt_manager", lambda: prompts,
    )

    async def fake_budget(self, model, base):
        return base

    monkeypatch.setattr(MultiModelQA, "_maybe_bump_vision_thinking_budget", fake_budget)

    calls["answer"] = json.dumps({"score": 85, "approved": True, "issues": ["table overflows"]})

    async def fake_vision(self, **kwargs):
        calls["vision_kwargs"] = kwargs
        return calls["answer"]

    monkeypatch.setattr(MultiModelQA, "_vision_complete", fake_vision)
    return calls


@pytest.mark.unit
@pytest.mark.asyncio
class TestRenderedPreviewOutcome:
    async def test_no_settings_service_is_disabled(self, vision):
        review, status, detail = await _qa()._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert (review, status) == (None, "disabled")
        assert "settings service" in detail
        assert "html" not in vision

    async def test_switched_off_is_disabled(self, vision):
        review, status, _ = await _qa(
            qa_preview_screenshot_enabled="false",
        )._check_rendered_preview_outcome("T", "t", preview_html=PAGE_HTML)
        assert (review, status) == (None, "disabled")
        assert "html" not in vision

    async def test_on_without_a_model_fails(self, vision):
        settings = {**_ON, "qa_preview_vision_model": ""}
        review, status, detail = await _qa(**settings)._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert (review, status) == (None, "failed")
        assert "qa_preview_vision_model is not set" in detail

    async def test_on_with_nothing_to_render_fails(self, vision):
        review, status, detail = await _qa(**_ON)._check_rendered_preview_outcome("T", "t")
        assert (review, status) == (None, "failed")
        assert "nothing to render" in detail

    async def test_config_read_failure_fails_not_disables(self, vision):
        svc = MagicMock()
        svc.get = AsyncMock(side_effect=RuntimeError("settings DB down"))
        qa = MultiModelQA(pool=None, settings_service=svc, site_config=SiteConfig())
        review, status, detail = await qa._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert (review, status) == (None, "failed")
        assert "settings DB down" in detail

    async def test_html_is_tiled_and_every_tile_is_judged(self, vision):
        review, status, detail = await _qa(**_ON)._check_rendered_preview_outcome(
            "Tuning FastAPI", "fastapi", preview_html=PAGE_HTML,
        )
        assert (status, detail) == ("reviewed", "")
        assert review.reviewer == "rendered_preview"
        assert review.provider == "vision_gate"
        assert review.score == 85.0
        assert review.approved is True
        assert "table overflows" in review.feedback
        assert vision["html"] == PAGE_HTML
        assert "url" not in vision
        kwargs = vision["vision_kwargs"]
        assert kwargs["phase"] == "qa_vision_preview"
        assert kwargs["mime"] == "image/png"
        # one image per tile, in page order, byte for byte
        assert [base64.b64decode(b) for b in kwargs["images_b64"]] == [t.png for t in vision["shot"].tiles]
        assert len(kwargs["images_b64"]) == 2

    async def test_the_prompt_tells_the_judge_what_each_tile_shows(self, vision):
        vision["shot"] = _shot((0, 1024), (1024, 2048), (2048, 2500))
        await _qa(**_ON)._check_rendered_preview_outcome("Title", "topic", preview_html=PAGE_HTML)
        kwargs = vision["prompts"].get_prompt.call_args.kwargs
        assert vision["prompts"].get_prompt.call_args.args == ("qa.vision_preview_screenshot",)
        assert kwargs["title"] == "Title" and kwargs["topic"] == "topic"
        assert kwargs["tile_count"] == 3
        assert kwargs["tile_guide"] == (
            "Tile 1: page rows 0-1024\nTile 2: page rows 1024-2048\nTile 3: page rows 2048-2500"
        )

    async def test_the_tile_budget_comes_from_settings(self, vision):
        await _qa(
            **_ON, qa_preview_viewport_width="1000", qa_preview_viewport_height="800",
            qa_preview_max_tiles="5", qa_preview_min_scale="0.7",
        )._check_rendered_preview_outcome("T", "t", preview_html=PAGE_HTML)
        assert vision["capture_kwargs"] == {
            "viewport_width": 1000, "viewport_height": 800, "max_tiles": 5, "min_scale": 0.7,
        }

    async def test_the_tile_budget_has_defaults(self, vision):
        await _qa(**_ON)._check_rendered_preview_outcome("T", "t", preview_html=PAGE_HTML)
        assert vision["capture_kwargs"] == {
            "viewport_width": 1280, "viewport_height": 1024, "max_tiles": 8, "min_scale": 0.6,
        }

    async def test_a_tile_cap_the_judges_context_cannot_hold_is_clamped(self, vision, caplog):
        """The cap is a preference; the pinned judge's 16,384-token context is a
        fact. 1280x1024 tiles cost ~1,282 tokens each, so with the answer and
        prompt reserved ten fit and fifty do not."""
        with caplog.at_level("WARNING"):
            await _qa(**_ON, qa_preview_max_tiles="50")._check_rendered_preview_outcome(
                "T", "t", preview_html=PAGE_HTML,
            )
        assert vision["capture_kwargs"]["max_tiles"] == 10
        assert any(
            "qa_preview_max_tiles=50" in r.message and "16384" in r.message
            for r in caplog.records
        )

    async def test_a_smaller_pinned_context_means_fewer_tiles(self, vision):
        small = SiteConfig(initial_config={"pinned_llm_endpoint_num_ctx": "8192"})
        await _qa(small, **_ON)._check_rendered_preview_outcome("T", "t", preview_html=PAGE_HTML)
        assert vision["capture_kwargs"]["max_tiles"] == 3  # (8192 - 3072) // 1282, below the cap of 8

    async def test_the_default_cap_is_never_clamped_on_the_default_context(self, vision, caplog):
        with caplog.at_level("WARNING"):
            await _qa(**_ON)._check_rendered_preview_outcome("T", "t", preview_html=PAGE_HTML)
        assert vision["capture_kwargs"]["max_tiles"] == 8
        assert not any("qa_preview_max_tiles" in r.message for r in caplog.records)

    async def test_the_guard_steps_aside_when_pinning_is_off(self, vision):
        """``pinned_llm_endpoint_num_ctx <= 0`` is the operator's explicit opt-out:
        the call's context is per-phase and unknown here, so the cap stands."""
        unpinned = SiteConfig(initial_config={"pinned_llm_endpoint_num_ctx": "0"})
        await _qa(unpinned, **_ON, qa_preview_max_tiles="50")._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert vision["capture_kwargs"]["max_tiles"] == 50

    async def test_the_guard_fails_open(self, vision, monkeypatch, caplog):
        """A guard that cannot read the context must not be why there is no verdict."""

        async def broken(pool, *, site_config=None):
            raise RuntimeError("context lookup exploded")

        monkeypatch.setattr(
            "poindexter.services.llm_providers.dispatcher.pinned_endpoint_num_ctx", broken,
        )
        with caplog.at_level("WARNING"):
            review, status, _ = await _qa(**_ON, qa_preview_max_tiles="6")._check_rendered_preview_outcome(
                "T", "t", preview_html=PAGE_HTML,
            )
        assert status == "reviewed" and review is not None
        assert vision["capture_kwargs"]["max_tiles"] == 6
        assert any("context lookup exploded" in r.message for r in caplog.records)

    async def test_a_whole_page_verdict_carries_no_sampling_note(self, vision):
        review, _, _ = await _qa(**_ON)._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert "sampled" not in review.feedback

    async def test_a_sampled_page_says_so_in_the_verdict(self, vision):
        vision["shot"] = _shot((0, 1024), (5000, 6024), (12117, 13141), complete=False)
        review, status, _ = await _qa(**_ON)._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert status == "reviewed"
        assert "sampled 3 tiles of a 13141px page" in review.feedback
        assert "table overflows" in review.feedback

    async def test_score_below_threshold_is_not_approved(self, vision):
        vision["answer"] = json.dumps({"score": 40, "issues": ["no CSS"]})
        review, status, _ = await _qa(**_ON)._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert status == "reviewed"
        assert review.approved is False

    async def test_capture_error_fails_with_the_cause(self, vision, monkeypatch):
        from poindexter.services.preview_screenshot import PreviewScreenshotError

        async def broken(html, **kwargs):
            raise PreviewScreenshotError("chromium: target crashed")

        monkeypatch.setattr(
            "poindexter.services.preview_screenshot.capture_html_tiles", broken,
        )
        review, status, detail = await _qa(**_ON)._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert (review, status) == (None, "failed")
        assert "target crashed" in detail
        assert "vision_kwargs" not in vision

    async def test_empty_model_answer_fails(self, vision):
        vision["answer"] = ""
        review, status, detail = await _qa(**_ON)._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert (review, status) == (None, "failed")
        assert "returned no text" in detail

    async def test_unparseable_model_answer_fails(self, vision):
        vision["answer"] = "I think it looks fine overall."
        review, status, detail = await _qa(**_ON)._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )
        assert (review, status) == (None, "failed")
        assert "unparseable" in detail

    async def test_legacy_url_wrapper_still_returns_review_or_none(self, vision, monkeypatch):
        qa = _qa(**_ON)
        review = await qa._check_rendered_preview("T", "t", "http://worker:8002/preview/x")
        assert review is not None and review.score == 85.0
        assert vision["url"] == "http://worker:8002/preview/x"

        async def no_png(url, **kwargs):
            return None

        monkeypatch.setattr(
            "poindexter.services.preview_screenshot.capture_preview_screenshot", no_png,
        )
        assert await qa._check_rendered_preview("T", "t", "http://dead/preview/x") is None
        _, status, detail = await qa._check_rendered_preview_outcome(
            "T", "t", preview_url="http://dead/preview/x",
        )
        assert status == "failed"
        assert "http://dead/preview/x" in detail

    async def test_a_served_page_is_judged_as_one_tile(self, vision):
        """The legacy URL path screenshots a served page whole: one image, one tile."""
        _, status, _ = await _qa(**_ON)._check_rendered_preview_outcome(
            "T", "t", preview_url="http://worker:8002/preview/x",
        )
        assert status == "reviewed"
        assert len(vision["vision_kwargs"]["images_b64"]) == 1
        assert vision["prompts"].get_prompt.call_args.kwargs["tile_count"] == 1
        assert "capture_kwargs" not in vision  # capture_html_tiles was not involved


_CLEAN = PageFacts(images=4, failed_images=(), overflow_px=0)
_DEAD_HERO = PageFacts(
    images=4, failed_images=(FailedImage(alt="The Mission Control dashboard", row=1954),), overflow_px=0,
)


@pytest.mark.unit
@pytest.mark.asyncio
class TestMeasuredFacts:
    """What the browser measured is not left to the judge. Asked pointedly, tile by tile,
    whether an image was broken, it said "no" on 32 of 32 runs over 8 real drafts with a
    dead image; told to hunt for problems, it flagged every clean page. A failed image
    or sideways overflow is a serious visual defect by the rubric itself, so it is an
    objection whatever the judge answered."""

    async def _run(self, vision, facts, answer=None, **settings):
        vision["shot"] = _shot((0, 1024), (1024, 2048), facts=facts)
        if answer is not None:
            vision["answer"] = json.dumps(answer)
        return await _qa(**{**_ON, **settings})._check_rendered_preview_outcome(
            "T", "t", preview_html=PAGE_HTML,
        )

    async def test_the_prompt_carries_what_the_browser_measured(self, vision):
        await self._run(vision, _DEAD_HERO)
        text = vision["prompts"].get_prompt.call_args.kwargs["page_facts"]
        assert text == describe_page_facts(_DEAD_HERO)
        assert '1 failed to load (alt text: "The Mission Control dashboard")' in text

    async def test_a_screenshot_nobody_measured_says_so(self, vision):
        await self._run(vision, None)
        assert vision["prompts"].get_prompt.call_args.kwargs["page_facts"] == (
            "- Nothing was measured for this screenshot."
        )

    async def test_clean_facts_change_nothing(self, vision):
        review, status, _ = await self._run(
            vision, _CLEAN, {"score": 85, "approved": True, "issues": ["table overflows"]},
        )
        assert status == "reviewed"
        assert (review.score, review.approved) == (85.0, True)
        assert vision["prompts"].get_prompt.call_args.kwargs["page_facts"].startswith(
            "- Images: 4 in the page, all loaded."
        )

    async def test_a_failed_image_is_an_objection_whatever_the_judge_says(self, vision):
        review, status, _ = await self._run(
            vision, _DEAD_HERO, {"score": 95, "approved": True, "issues": []},
        )
        assert status == "reviewed"
        assert review.approved is False
        assert review.score == 69.0  # one under the pass threshold (70): an objection, in the score too
        assert 'Image failed to load: "The Mission Control dashboard"' in review.feedback

    async def test_the_measured_issue_leads_the_verdict_text(self, vision):
        review, _, _ = await self._run(
            vision, _DEAD_HERO, {"score": 90, "approved": True, "issues": ["Tile 2: dense text"]},
        )
        assert review.feedback.index("Image failed to load") < review.feedback.index("Tile 2: dense text")

    async def test_sideways_overflow_is_an_objection_too(self, vision):
        review, status, _ = await self._run(
            vision, PageFacts(images=2, failed_images=(), overflow_px=656),
            {"score": 95, "approved": True, "issues": []},
        )
        assert status == "reviewed"
        assert (review.approved, review.score) == (False, 69.0)
        assert "656 px past the right edge" in review.feedback

    async def test_a_lower_judge_score_is_kept(self, vision):
        review, _, _ = await self._run(
            vision, _DEAD_HERO, {"score": 40, "approved": False, "issues": ["no styling"]},
        )
        assert review.score == 40.0

    async def test_the_cap_follows_the_pass_threshold(self, vision):
        review, _, _ = await self._run(
            vision, _DEAD_HERO, {"score": 95, "approved": True, "issues": []},
            qa_preview_pass_threshold="85",
        )
        assert review.score == 84.0

    async def test_the_served_page_path_measures_nothing_and_caps_nothing(self, vision):
        """The legacy URL path screenshots a served page whole and has no measurement."""
        review, status, _ = await _qa(**_ON)._check_rendered_preview_outcome(
            "T", "t", preview_url="http://worker:8002/preview/x",
        )
        assert status == "reviewed"
        assert (review.score, review.approved) == (85.0, True)
        assert vision["prompts"].get_prompt.call_args.kwargs["page_facts"] == (
            "- Nothing was measured for this screenshot."
        )
