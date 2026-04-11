"""Unit tests for services/image_decision_agent.py.

Covers:
- ImagePlan and ImagePlanResult dataclasses
- plan_images: no-sections short-circuit
- plan_images: happy path with mocked Ollama (/api/generate path)
- plan_images: thinking-model path (/api/chat) and JSON extraction from
  reasoning text
- plan_images: ollama unreachable error path
- plan_images: model-not-found error path
- plan_images: 404 retry-then-success
- plan_images: malformed JSON falls back to empty result with raw_response
- plan_images: max_images cap is honored
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.image_decision_agent import (
    ImagePlan,
    ImagePlanResult,
    plan_images,
)

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
# Helpers
# ---------------------------------------------------------------------------


def _mock_client_factory(get_resp=None, post_responses=None):
    """Build a fake httpx.AsyncClient context manager.

    get_resp: response object for the /api/tags health check
    post_responses: list of response objects to return in order from .post()
    """
    client = AsyncMock()
    client.get = AsyncMock(return_value=get_resp)
    if post_responses:
        client.post = AsyncMock(side_effect=post_responses)
    else:
        client.post = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _tags_resp(model_names):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"models": [{"name": n} for n in model_names]}
    return r


def _generate_resp(text, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"response": text}
    r.raise_for_status = MagicMock()
    return r


def _chat_resp(text, status=200, thinking=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"message": {"content": text}, "thinking": thinking}
    r.raise_for_status = MagicMock()
    return r


SAMPLE_CONTENT = """# Intro

Some intro text.

## How It Works

Body content for how it works.

## Performance

Body content for performance.

### Subsection Three

Sub body.
"""


# ---------------------------------------------------------------------------
# plan_images
# ---------------------------------------------------------------------------


class TestPlanImagesShortCircuits:
    @pytest.mark.asyncio
    async def test_no_sections_returns_empty(self):
        # Content with no h2/h3 headings should skip planning entirely
        result = await plan_images("Just a paragraph with no headings.", "topic")
        assert isinstance(result, ImagePlanResult)
        assert result.images == []
        assert result.featured_image is None


class TestPlanImagesHappyPath:
    @pytest.mark.asyncio
    async def test_generate_path_with_valid_json(self):
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
        client = _mock_client_factory(
            get_resp=_tags_resp(["llama3:latest"]),
            post_responses=[_generate_resp(json.dumps(plan_json))],
        )

        with patch("httpx.AsyncClient", return_value=client), \
             patch("services.image_decision_agent.site_config") as mock_site:
            mock_site.get.side_effect = lambda k, d=None: {
                "ollama_base_url": "http://fake-ollama:11434",
                "model_role_image_decision": "ollama/llama3",
                "database_url": "",
            }.get(k, d)

            result = await plan_images(SAMPLE_CONTENT, "Test Topic", category="technology")

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
        client = _mock_client_factory(
            get_resp=_tags_resp(["llama3:latest"]),
            post_responses=[_generate_resp(json.dumps(plan_json))],
        )

        with patch("httpx.AsyncClient", return_value=client), \
             patch("services.image_decision_agent.site_config") as mock_site:
            mock_site.get.side_effect = lambda k, d=None: {
                "ollama_base_url": "http://fake:11434",
                "model_role_image_decision": "ollama/llama3",
                "database_url": "",
            }.get(k, d)

            result = await plan_images(SAMPLE_CONTENT, "Test", max_images=3)

        assert len(result.images) == 3


class TestPlanImagesThinkingModel:
    @pytest.mark.asyncio
    async def test_chat_path_extracts_json_from_reasoning(self):
        # Thinking model returns reasoning text wrapping the JSON object
        thinking_output = (
            "Let me think about this article. The sections are clear...\n"
            "I'll go with these choices:\n"
            '{"featured": {"source": "sdxl", "style": "dramatic", "prompt": "moody data center", "reasoning": "ok"}, '
            '"inline": [{"section": "How It Works", "source": "sdxl", "style": "blueprint", "prompt": "neural net", "reasoning": "ok"}]}'
        )
        client = _mock_client_factory(
            get_resp=_tags_resp(["qwen3:8b"]),
            post_responses=[_chat_resp(thinking_output)],
        )

        with patch("httpx.AsyncClient", return_value=client), \
             patch("services.image_decision_agent.site_config") as mock_site:
            mock_site.get.side_effect = lambda k, d=None: {
                "ollama_base_url": "http://fake:11434",
                "model_role_image_decision": "qwen3:8b",
                "database_url": "",
            }.get(k, d)

            result = await plan_images(SAMPLE_CONTENT, "Topic")

        assert result.featured_image is not None
        assert result.featured_image.style == "dramatic"
        assert len(result.images) == 1

    @pytest.mark.asyncio
    async def test_chat_path_strips_think_tags(self):
        wrapped = (
            "<think>I'm reasoning about this...</think>\n"
            '{"featured": {"source": "sdxl", "style": "editorial", "prompt": "p", "reasoning": "r"}, "inline": []}'
        )
        client = _mock_client_factory(
            get_resp=_tags_resp(["qwen3:8b"]),
            post_responses=[_chat_resp(wrapped)],
        )

        with patch("httpx.AsyncClient", return_value=client), \
             patch("services.image_decision_agent.site_config") as mock_site:
            mock_site.get.side_effect = lambda k, d=None: {
                "ollama_base_url": "http://fake:11434",
                "model_role_image_decision": "qwen3:8b",
                "database_url": "",
            }.get(k, d)

            result = await plan_images(SAMPLE_CONTENT, "Topic")

        assert result.featured_image is not None
        assert result.featured_image.style == "editorial"
        assert result.images == []


class TestPlanImagesErrorPaths:
    @pytest.mark.asyncio
    async def test_ollama_unreachable_returns_empty(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=Exception("connection refused"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=client), \
             patch("services.image_decision_agent.site_config") as mock_site:
            mock_site.get.side_effect = lambda k, d=None: {
                "ollama_base_url": "http://fake:11434",
                "model_role_image_decision": "ollama/llama3",
                "database_url": "",
            }.get(k, d)

            result = await plan_images(SAMPLE_CONTENT, "Topic")

        # Graceful fallback — empty result, no exception raised
        assert isinstance(result, ImagePlanResult)
        assert result.images == []
        assert result.featured_image is None

    @pytest.mark.asyncio
    async def test_model_not_in_available_list_returns_empty(self):
        client = _mock_client_factory(
            get_resp=_tags_resp(["completely-different-model:latest"]),
        )

        with patch("httpx.AsyncClient", return_value=client), \
             patch("services.image_decision_agent.site_config") as mock_site:
            mock_site.get.side_effect = lambda k, d=None: {
                "ollama_base_url": "http://fake:11434",
                "model_role_image_decision": "ollama/llama3",
                "database_url": "",
            }.get(k, d)

            result = await plan_images(SAMPLE_CONTENT, "Topic")

        assert isinstance(result, ImagePlanResult)
        assert result.images == []

    @pytest.mark.asyncio
    async def test_404_then_success_retry(self):
        # First call returns 404 (model not loaded), second succeeds
        plan_json = {
            "featured": {"source": "sdxl", "style": "x", "prompt": "p", "reasoning": "r"},
            "inline": [],
        }
        success_resp = _generate_resp(json.dumps(plan_json))
        not_loaded = MagicMock()
        not_loaded.status_code = 404
        not_loaded.raise_for_status = MagicMock(side_effect=Exception("should not be called"))

        client = _mock_client_factory(
            get_resp=_tags_resp(["llama3:latest"]),
            post_responses=[not_loaded, success_resp],
        )

        # Patch asyncio.sleep so we don't actually wait 3s
        with patch("httpx.AsyncClient", return_value=client), \
             patch("services.image_decision_agent.site_config") as mock_site, \
             patch("asyncio.sleep", new=AsyncMock()):
            mock_site.get.side_effect = lambda k, d=None: {
                "ollama_base_url": "http://fake:11434",
                "model_role_image_decision": "ollama/llama3",
                "database_url": "",
            }.get(k, d)

            result = await plan_images(SAMPLE_CONTENT, "Topic")

        assert result.featured_image is not None
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_malformed_json_returns_empty_with_raw(self):
        client = _mock_client_factory(
            get_resp=_tags_resp(["llama3:latest"]),
            post_responses=[_generate_resp("not json at all, just words")],
        )

        with patch("httpx.AsyncClient", return_value=client), \
             patch("services.image_decision_agent.site_config") as mock_site:
            mock_site.get.side_effect = lambda k, d=None: {
                "ollama_base_url": "http://fake:11434",
                "model_role_image_decision": "ollama/llama3",
                "database_url": "",
            }.get(k, d)

            result = await plan_images(SAMPLE_CONTENT, "Topic")

        assert result.featured_image is None
        assert result.images == []
        assert "not json" in result.raw_response
