"""Unit tests for the qa.vision atom (Glad-Labs/poindexter#563).

Pins the contract that the vision/preview gate runs on the live graph_def
path: the image-relevance check restores the cold ``vision_gate`` score, and
``preview_url`` is threaded through to ``_check_rendered_preview`` so the
rendered-preview screenshot gate actually runs (it was permanently skipped on
the live path because no ``preview_url`` reached it).
"""

from __future__ import annotations

import pytest

from services.atoms import qa_vision
from services.multi_model_qa import MultiModelQA, ReviewerResult


class _Cfg:
    def __init__(self, base="http://localhost:8002"):
        self._base = base

    def get(self, key, default=None):
        if key == "preview_base_url":
            return self._base
        return default


def _state(**over):
    base = {
        "content": "a sufficiently long blog body to review",
        "topic": "FastAPI performance",
        "seo_title": "Tuning FastAPI",
        "site_config": _Cfg(),
    }
    base.update(over)
    return base


_GATE_STATES = {"vision_gate": (True, False)}  # enabled, advisory


@pytest.fixture(autouse=True)
def _no_db_gate_states(monkeypatch):
    async def fake_gate_states(self):
        return _GATE_STATES
    monkeypatch.setattr(MultiModelQA, "_load_gate_states", fake_gate_states)


@pytest.mark.unit
class TestQaVisionAtom:
    def test_meta(self):
        m = qa_vision.ATOM_META
        assert m.name == "qa.vision"
        assert "qa_rail_reviews" in m.produces
        assert "content" in m.requires
        # preview_url is a SOFT input — declared but not required, so the
        # build-time validator never forces a producer for it.
        names = {f.name: f.required for f in m.inputs}
        assert names.get("preview_url") is False
        assert m.parallelizable is True

    async def test_empty_content_noops(self):
        assert await qa_vision.run({"content": "  ", "site_config": _Cfg()}) == {}

    async def test_image_relevance_review_emitted(self, monkeypatch):
        """The image-relevance check restores the cold vision_gate score."""
        async def img(self, title, topic, content):
            return ReviewerResult("image_relevance", True, 88.0, "ok", "vision_gate")

        async def no_preview(self, title, topic, preview_url):  # pragma: no cover
            raise AssertionError("preview check ran without a preview_url")

        monkeypatch.setattr(MultiModelQA, "_check_image_relevance", img)
        monkeypatch.setattr(MultiModelQA, "_check_rendered_preview", no_preview)

        out = await qa_vision.run(_state())  # no preview_url/token in state
        reviewers = [r["reviewer"] for r in out["qa_rail_reviews"]]
        assert "image_relevance" in reviewers
        assert "rendered_preview" not in reviewers

    async def test_preview_url_threaded_to_rendered_preview(self, monkeypatch):
        """THE #563 contract: an explicit preview_url reaches _check_rendered_preview."""
        seen = {}

        async def img(self, title, topic, content):
            return None  # isolate the preview leg

        async def preview(self, title, topic, preview_url):
            seen["preview_url"] = preview_url
            return ReviewerResult("rendered_preview", True, 91.0, "looks good", "vision_gate")

        monkeypatch.setattr(MultiModelQA, "_check_image_relevance", img)
        monkeypatch.setattr(MultiModelQA, "_check_rendered_preview", preview)

        url = "http://localhost:8002/preview/deadbeef"
        out = await qa_vision.run(_state(preview_url=url))
        assert seen.get("preview_url") == url
        reviewers = [r["reviewer"] for r in out["qa_rail_reviews"]]
        assert "rendered_preview" in reviewers

    async def test_preview_url_built_from_token(self, monkeypatch):
        """When only a preview_token is present, qa.vision builds the URL from
        preview_base_url + token (the verify_task → qa.vision seam)."""
        seen = {}

        async def img(self, title, topic, content):
            return None

        async def preview(self, title, topic, preview_url):
            seen["preview_url"] = preview_url
            return ReviewerResult("rendered_preview", True, 80.0, "ok", "vision_gate")

        monkeypatch.setattr(MultiModelQA, "_check_image_relevance", img)
        monkeypatch.setattr(MultiModelQA, "_check_rendered_preview", preview)

        out = await qa_vision.run(_state(preview_token="cafef00d"))
        assert seen.get("preview_url") == "http://localhost:8002/preview/cafef00d"
        assert any(r["reviewer"] == "rendered_preview" for r in out["qa_rail_reviews"])

    async def test_advisory_flag_from_gate_state(self, monkeypatch):
        """vision_gate is advisory in prod baseline → review.advisory=True."""
        async def img(self, title, topic, content):
            return ReviewerResult("image_relevance", True, 88.0, "ok", "vision_gate")
        monkeypatch.setattr(MultiModelQA, "_check_image_relevance", img)

        out = await qa_vision.run(_state())  # no preview_url → preview leg skipped
        review = next(r for r in out["qa_rail_reviews"] if r["reviewer"] == "image_relevance")
        assert review["advisory"] is True

    async def test_fail_loud_when_enabled_but_no_preview_url(self, monkeypatch):
        """feedback_no_silent_defaults: preview screenshot enabled but no URL
        → the atom pages the operator instead of silently skipping."""
        async def img(self, title, topic, content):
            return None  # no image review either → reviews empty

        monkeypatch.setattr(MultiModelQA, "_check_image_relevance", img)

        async def settings_get(key):
            return "true" if key == "qa_preview_screenshot_enabled" else None

        class _Settings:
            get = staticmethod(settings_get)

        notified = {}

        async def fake_notify(message, *, critical=False, site_config=None):
            notified["message"] = message

        monkeypatch.setattr(
            "services.integrations.operator_notify.notify_operator", fake_notify,
        )

        # No preview_url / preview_token → the fail-loud branch must fire.
        out = await qa_vision.run(_state(settings_service=_Settings(), task_id="abc123"))
        assert out == {}  # nothing to score, but...
        assert "no preview_url" in notified.get("message", "")

    async def test_no_fail_loud_when_preview_disabled(self, monkeypatch):
        """When the screenshot flag is off, an absent preview_url is NOT an
        alert — the gate is simply opt-out."""
        async def img(self, title, topic, content):
            return None
        monkeypatch.setattr(MultiModelQA, "_check_image_relevance", img)

        class _Settings:
            @staticmethod
            async def get(key):
                return "false"

        notified = {}

        async def fake_notify(message, *, critical=False, site_config=None):
            notified["message"] = message
        monkeypatch.setattr(
            "services.integrations.operator_notify.notify_operator", fake_notify,
        )

        out = await qa_vision.run(_state(settings_service=_Settings()))
        assert out == {}
        assert "message" not in notified
