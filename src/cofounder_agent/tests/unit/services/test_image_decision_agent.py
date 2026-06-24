"""Unit tests for services/image_decision_agent.py.

Covers:
- ImagePlan and ImagePlanResult dataclasses
- plan_images: no-sections short-circuit
- plan_images: happy path via dispatch_complete
- plan_images: thinking-model JSON extraction from reasoning text
- plan_images: no-pool guard returns empty result
- plan_images: dispatch_complete error returns empty result
- plan_images: malformed JSON falls back to empty result with raw_response
- plan_images: max_images cap is honored
- plan_images: cost-tier resolution path (Lane B sweep)
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.image_decision_agent import (
    ImagePlan,
    ImagePlanResult,
    plan_images,
)


def _patched_site_config(model_role: str = "ollama/llama3") -> MagicMock:
    """Build a site_config mock that mimics the post-Lane-B DI shape.

    ``_pool`` is set to a _FakePool by default so dispatch_complete can be
    exercised. Pass ``_pool=None`` explicitly to test the no-pool guard.
    Tests that want to exercise the tier path supply their own pool via
    ``mock_site._pool = <fake_pool>``.
    """
    mock_site = MagicMock()
    mock_site._pool = _FakePool("ollama/" + model_role.removeprefix("ollama/"))
    mock_site.get.side_effect = lambda k, d=None: {
        "model_role_image_decision": model_role,
        "database_url": "",
    }.get(k, d)
    return mock_site


class _FakeConn:
    def __init__(self, value: str | None):
        self._value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query: str, *args: Any) -> str | None:
        return self._value


class _FakePool:
    """Minimal asyncpg-like pool returning a configurable fetchval value."""

    def __init__(self, value: str | None):
        self._value = value

    def acquire(self):
        return _FakeConn(self._value)


# ---------------------------------------------------------------------------
# Helper: build a mock dispatch_complete completion object
# ---------------------------------------------------------------------------


def _completion(text: str) -> MagicMock:
    """Return a minimal completion object with a .text attribute."""
    c = MagicMock()
    c.text = text
    return c


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


class TestImagePlanDataclass:
    def test_required_fields(self):
        plan = ImagePlan(
            section_heading="Intro",
            source="sdxl",
            style="blueprint",
            prompt="abstract neural network",
            position="after_heading",
            reasoning="visualizes the topic",
        )
        assert plan.section_heading == "Intro"
        assert plan.source == "sdxl"
        assert plan.style == "blueprint"


class TestImagePlanResultDataclass:
    def test_defaults(self):
        result = ImagePlanResult()
        assert result.images == []
        assert result.featured_image is None
        assert result.raw_response == ""

    def test_carries_raw_response(self):
        result = ImagePlanResult(raw_response="some llm output")
        assert result.raw_response == "some llm output"


# ---------------------------------------------------------------------------
# Short-circuit paths (no sections, no pool)
# ---------------------------------------------------------------------------


SAMPLE_CONTENT = """# Intro

Some intro text.

## How It Works

Body content for how it works.

## Performance

Body content for performance.

### Subsection Three

Sub body.
"""


class TestPlanImagesShortCircuits:
    @pytest.mark.asyncio
    async def test_no_sections_returns_empty(self):
        # Content with no h2/h3 headings AND no bold pseudo-headings
        # short-circuits — nothing to anchor images to.
        result = await plan_images(
            "Just a paragraph with no headings.", "topic",
            site_config=_patched_site_config(),
        )
        assert isinstance(result, ImagePlanResult)
        assert result.images == []
        assert result.featured_image is None

    @pytest.mark.asyncio
    async def test_no_pool_returns_empty(self):
        """When site_config._pool is None dispatch_complete cannot be called;
        plan_images must return an empty result rather than raise."""
        mock_site = _patched_site_config()
        mock_site._pool = None  # explicitly disable

        result = await plan_images(SAMPLE_CONTENT, "topic", site_config=mock_site)
        assert isinstance(result, ImagePlanResult)
        assert result.images == []
        assert result.featured_image is None

    @pytest.mark.asyncio
    async def test_bold_pseudo_headings_do_not_short_circuit(self):
        """Regression for the 2026-05-27 finding: 12 consecutive published
        canonical_blog posts had 0 inline images because writers emit
        ``**Section Title**`` standalone-line headings instead of real
        ``## Section Title`` markdown. The agent's section-detection
        falls back to bold-text pseudo-headings before short-circuiting.

        Verify by checking the short-circuit DOESN'T fire (dispatch_complete
        is called). The dispatch is mocked to succeed so the test focuses on
        the section-extraction layer."""
        content = (
            "Intro paragraph here.\n\n"
            "**First Real Section**\n\n"
            "Body of the first section.\n\n"
            "**Another Section**\n\n"
            "Body of the second section.\n"
        )
        plan_json = {
            "featured": {"source": "sdxl", "style": "x", "prompt": "p", "reasoning": "r"},
            "inline": [],
        }

        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion(json.dumps(plan_json))),
        ) as mock_dispatch:
            result = await plan_images(
                content, "the topic", site_config=_patched_site_config(),
            )

        assert mock_dispatch.await_count == 1, (
            "Agent short-circuited on bold-text pseudo-headings — "
            "the 2026-05-27 fix regressed."
        )
        assert isinstance(result, ImagePlanResult)

    @pytest.mark.asyncio
    async def test_inline_bold_not_treated_as_heading(self):
        """A ``**phrase**`` mid-paragraph is NOT a heading — it's a
        bold word in prose. Only entire-line bold counts."""
        content = (
            "This sentence contains **bold inline text** in the middle.\n"
            "Another paragraph with **more bold text** scattered through it.\n"
        )

        result = await plan_images(
            content, "topic", site_config=_patched_site_config(),
        )
        assert isinstance(result, ImagePlanResult)
        assert result.images == []
        assert result.featured_image is None


# ---------------------------------------------------------------------------
# Happy path via dispatch_complete
# ---------------------------------------------------------------------------


class TestPlanImagesHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path_with_valid_json(self):
        plan_json = {
            "featured": {
                "source": "sdxl",
                "style": "editorial",
                "prompt": "abstract scene",
                "reasoning": "hero",
            },
            "inline": [
                {
                    "section": "How It Works",
                    "source": "pexels",
                    "style": "photo",
                    "prompt": "people working on a laptop",
                    "reasoning": "concrete",
                },
                {
                    "section": "Performance",
                    "source": "sdxl",
                    "style": "blueprint",
                    "prompt": "graph going up",
                    "reasoning": "abstract metric",
                },
            ],
        }

        mock_site = _patched_site_config()
        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion(json.dumps(plan_json))),
        ):
            result = await plan_images(SAMPLE_CONTENT, "Test Topic", category="technology", site_config=mock_site)

        assert result.featured_image is not None
        assert result.featured_image.source == "sdxl"
        assert result.featured_image.style == "editorial"
        assert result.featured_image.position == "hero"
        assert len(result.images) == 2
        assert result.images[0].section_heading == "How It Works"
        assert result.images[0].source == "pexels"
        assert result.images[1].section_heading == "Performance"
        assert result.images[1].style == "blueprint"

    @pytest.mark.asyncio
    async def test_max_images_cap(self):
        plan_json = {
            "featured": {"source": "sdxl", "style": "x", "prompt": "p", "reasoning": "r"},
            "inline": [
                {"section": f"Sec {i}", "source": "sdxl", "style": "x", "prompt": "p", "reasoning": "r"}
                for i in range(8)
            ],
        }

        mock_site = _patched_site_config()
        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion(json.dumps(plan_json))),
        ):
            result = await plan_images(SAMPLE_CONTENT, "Test", max_images=3, site_config=mock_site)

        assert len(result.images) == 3

    @pytest.mark.asyncio
    async def test_routes_through_budget_tier(self):
        """dispatch_complete must be called with tier='budget'."""
        plan_json = {
            "featured": {"source": "sdxl", "style": "x", "prompt": "p", "reasoning": "r"},
            "inline": [],
        }
        mock_site = _patched_site_config()
        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion(json.dumps(plan_json))),
        ) as mock_dispatch:
            await plan_images(SAMPLE_CONTENT, "Test", site_config=mock_site)

        assert mock_dispatch.await_count == 1
        call_kwargs = mock_dispatch.await_args
        assert call_kwargs.kwargs.get("tier") == "budget"
        assert call_kwargs.kwargs.get("phase") == "image_decision_agent"


# ---------------------------------------------------------------------------
# Thinking-model / JSON-in-reasoning
# ---------------------------------------------------------------------------


class TestPlanImagesThinkingModel:
    @pytest.mark.asyncio
    async def test_extracts_json_from_reasoning_text(self):
        """Thinking models return reasoning text wrapping the JSON object;
        the parser must extract it."""
        thinking_output = (
            "Let me think about this article. The sections are clear...\n"
            "I'll go with these choices:\n"
            '{"featured": {"source": "sdxl", "style": "dramatic", "prompt": "moody data center", "reasoning": "ok"}, '
            '"inline": [{"section": "How It Works", "source": "sdxl", "style": "blueprint", "prompt": "neural net", "reasoning": "ok"}]}'
        )

        mock_site = _patched_site_config(model_role="qwen3:8b")
        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion(thinking_output)),
        ):
            result = await plan_images(SAMPLE_CONTENT, "Topic", site_config=mock_site)

        assert result.featured_image is not None
        assert result.featured_image.style == "dramatic"
        assert len(result.images) == 1

    @pytest.mark.asyncio
    async def test_strips_think_tags(self):
        """<think>...</think> tags wrapping the JSON must be stripped."""
        wrapped = (
            "<think>I'm reasoning about this...</think>\n"
            '{"featured": {"source": "sdxl", "style": "editorial", "prompt": "p", "reasoning": "r"}, "inline": []}'
        )

        mock_site = _patched_site_config(model_role="qwen3:8b")
        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion(wrapped)),
        ):
            result = await plan_images(SAMPLE_CONTENT, "Topic", site_config=mock_site)

        assert result.featured_image is not None
        assert result.featured_image.style == "editorial"
        assert result.images == []


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestPlanImagesErrorPaths:
    @pytest.mark.asyncio
    async def test_dispatch_complete_error_returns_empty(self):
        """Any exception from dispatch_complete is caught; empty result returned."""
        mock_site = _patched_site_config()
        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(side_effect=RuntimeError("provider offline")),
        ):
            result = await plan_images(SAMPLE_CONTENT, "Topic", site_config=mock_site)

        assert isinstance(result, ImagePlanResult)
        assert result.images == []
        assert result.featured_image is None

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty_with_raw(self):
        mock_site = _patched_site_config()
        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion("not json at all, just words")),
        ):
            result = await plan_images(SAMPLE_CONTENT, "Topic", site_config=mock_site)

        assert result.featured_image is None
        assert result.images == []
        assert "not json" in result.raw_response

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty(self):
        mock_site = _patched_site_config()
        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion("")),
        ):
            result = await plan_images(SAMPLE_CONTENT, "Topic", site_config=mock_site)

        assert isinstance(result, ImagePlanResult)
        assert result.images == []


# ---------------------------------------------------------------------------
# Model-pin resolution (model_role_image_decision)
# ---------------------------------------------------------------------------


class TestPlanImagesModelPinResolution:
    """``plan_images`` resolves the image-decision model from the per-step
    ``model_role_image_decision`` pin, read directly (the cost_tier.budget
    fallback was removed). An empty pin fails loud (notify) and returns an
    empty plan."""

    @pytest.mark.asyncio
    async def test_model_role_pin_passed_to_provider_call(self):
        """model_role_image_decision is read directly and passed to dispatch_complete."""
        plan_json = {
            "featured": {"source": "sdxl", "style": "x", "prompt": "p", "reasoning": "r"},
            "inline": [],
        }

        mock_site = _patched_site_config(model_role="ollama/image-decider")
        mock_site._pool = _FakePool("ollama/unused")

        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion(json.dumps(plan_json))),
        ) as mock_dispatch:
            result = await plan_images(SAMPLE_CONTENT, "Topic", site_config=mock_site)

        # dispatch_complete received the pin (ollama/ prefix stripped).
        call_model = mock_dispatch.await_args.kwargs.get("model")
        assert call_model == "image-decider"
        assert result.featured_image is not None

    @pytest.mark.asyncio
    async def test_pin_with_prefix_is_stripped(self):
        """The ollama/ prefix on the pin is stripped before dispatch."""
        plan_json = {
            "featured": {"source": "sdxl", "style": "x", "prompt": "p", "reasoning": "r"},
            "inline": [],
        }

        mock_site = _patched_site_config(model_role="ollama/per-site-model")
        mock_site._pool = _FakePool(None)

        with patch(
            "services.image_decision_agent.dispatch_complete",
            new=AsyncMock(return_value=_completion(json.dumps(plan_json))),
        ) as mock_dispatch:
            result = await plan_images(SAMPLE_CONTENT, "Topic", site_config=mock_site)

        call_model = mock_dispatch.await_args.kwargs.get("model")
        assert call_model == "per-site-model"
        assert result.featured_image is not None

    @pytest.mark.asyncio
    async def test_pages_operator_when_pin_unset(self):
        """No model_role_image_decision — fail loud (notify) + empty plan."""
        mock_site = _patched_site_config(model_role="")
        mock_site._pool = _FakePool(None)

        notify = AsyncMock()
        with patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify,
        ):
            result = await plan_images(SAMPLE_CONTENT, "Topic", site_config=mock_site)

        assert isinstance(result, ImagePlanResult)
        assert result.images == []
        assert result.featured_image is None
        notify.assert_awaited_once()
        msg = notify.await_args.args[0]
        assert "image_decision_agent" in msg
        assert "model_role_image_decision" in msg
