"""Unit tests for the qa.vision atom (Glad-Labs/poindexter#563).

Pins the contract that the vision/preview gate runs on the live graph_def
path: the image-relevance check restores the cold ``vision_gate`` score, and
``preview_url`` is threaded through to ``_check_rendered_preview`` so the
rendered-preview screenshot gate actually runs (it was permanently skipped on
the live path because no ``preview_url`` reached it).
"""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_vision
from modules.content.multi_model_qa import MultiModelQA, ReviewerResult


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
        → the atom pages the operator. It now ALSO emits a deliberate advisory
        PASS (never a silent {}) so a required vision_gate isn't failed closed
        on this vacuous run (#563)."""
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

        # No images + preview enabled + no preview_url/token.
        out = await qa_vision.run(_state(settings_service=_Settings(), task_id="abc123"))
        reviews = out["qa_rail_reviews"]
        assert len(reviews) == 1
        assert reviews[0]["reviewer"] == "image_relevance"  # aliases to vision_gate
        assert reviews[0]["approved"] is True
        assert reviews[0]["advisory"] is True  # deliberate pass, never gates
        assert "no preview_url" in notified.get("message", "")  # operator still paged

    async def test_no_fail_loud_when_preview_disabled(self, monkeypatch):
        """When the screenshot flag is off and there are no inline images, an
        absent preview_url is NOT an alert — but the atom STILL emits a
        deliberate advisory PASS (case C: nothing to assess) so a required
        vision_gate passes by vacuity rather than failing closed (#563)."""
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
        reviews = out["qa_rail_reviews"]
        assert len(reviews) == 1
        assert reviews[0]["advisory"] is True
        assert reviews[0]["approved"] is True
        assert "no inline images" in reviews[0]["feedback"].lower()
        assert "message" not in notified  # genuinely nothing wrong → no page

    async def test_no_images_satisfies_required_vision_gate(self, monkeypatch):
        """THE #563 acceptance core: a post with no inline images produces a
        review that satisfies a REQUIRED vision_gate (missing_required_gates
        returns it as present), so qa.aggregate does NOT fail closed."""
        from modules.content.atoms._qa_rail_common import missing_required_gates

        async def img(self, title, topic, content):
            return None
        monkeypatch.setattr(MultiModelQA, "_check_image_relevance", img)

        out = await qa_vision.run(_state())  # no images, no preview
        reviews = out["qa_rail_reviews"]
        # vision_gate is required + enabled here; the deliberate pass must
        # register as present so the vacuous-pass guard doesn't reject.
        assert missing_required_gates(reviews, {"vision_gate": (True, True)}) == []

    async def test_images_present_but_unassessable_passes_open_and_pages(self, monkeypatch):
        """Case D (operator policy = fail-open + page): inline images ARE
        present but the image leg couldn't assess them (vision model down /
        unparseable). The atom passes open (advisory) AND pages the operator,
        rather than failing the post closed."""
        async def img(self, title, topic, content):
            return None  # model unreachable
        monkeypatch.setattr(MultiModelQA, "_check_image_relevance", img)

        notified = {}

        async def fake_notify(message, *, critical=False, site_config=None):
            notified["message"] = message
        monkeypatch.setattr(
            "services.integrations.operator_notify.notify_operator", fake_notify,
        )

        body = 'Body.\n<img src="https://r2.dev/x.webp" alt="x" width="1024" />\nmore'
        out = await qa_vision.run(_state(content=body, task_id="def456"))
        reviews = out["qa_rail_reviews"]
        assert len(reviews) == 1
        assert reviews[0]["reviewer"] == "image_relevance"
        assert reviews[0]["approved"] is True      # fail-open
        assert reviews[0]["advisory"] is True       # but doesn't gate the score
        assert "could not assess" in reviews[0]["feedback"].lower()
        assert notified.get("message")              # operator paged (fail-open + page)
