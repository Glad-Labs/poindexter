"""MultiModelQA._check_rendered_preview_outcome: a verdict, or why there is none.

The rendered-preview leg returned the same bare ``None`` for "switched off"
and for every failure (dead URL, 404 page, capture error, empty model answer),
so it sat dark for months with 0 verdicts in 53 runs and nobody told. The
outcome API separates ``disabled`` (legitimate) from ``failed`` (reported by
the caller) and names the cause. Mirrors ``_web_fact_check_outcome``
(poindexter#1062).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from poindexter.modules.content.multi_model_qa import MultiModelQA
from poindexter.services.site_config import SiteConfig

FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"
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


def _qa(**settings) -> MultiModelQA:
    return MultiModelQA(
        pool=None,
        settings_service=_settings(**settings) if settings else None,
        site_config=SiteConfig(),
    )


@pytest.fixture
def vision(monkeypatch):
    """Stub the screenshot + vision seams; returns the call recorder."""
    calls: dict = {}

    async def fake_html_capture(html, **kwargs):
        calls["html"] = html
        calls["capture_kwargs"] = kwargs
        return FAKE_PNG

    async def fake_url_capture(url, **kwargs):
        calls["url"] = url
        return FAKE_PNG

    monkeypatch.setattr(
        "poindexter.services.preview_screenshot.capture_html_screenshot", fake_html_capture,
    )
    monkeypatch.setattr(
        "poindexter.services.preview_screenshot.capture_preview_screenshot", fake_url_capture,
    )

    prompts = MagicMock()
    prompts.get_prompt = MagicMock(return_value="judge this page")
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

    async def test_html_is_screenshotted_and_judged(self, vision):
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
        assert vision["capture_kwargs"]["full_page"] is True
        assert "url" not in vision
        assert vision["vision_kwargs"]["phase"] == "qa_vision_preview"
        assert vision["vision_kwargs"]["mime"] == "image/png"

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
            "poindexter.services.preview_screenshot.capture_html_screenshot", broken,
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
