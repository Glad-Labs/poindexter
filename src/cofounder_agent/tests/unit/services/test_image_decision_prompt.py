"""Snapshot test pinning the image_decision_agent prompt.

This test is the public contract for the image-director prompt that was
migrated out of the inline f-string in
``services/image_decision_agent.py`` into the image-generation prompt
pack, then again from ``prompts/image_generation.yaml`` into the
agentskills.io catalog at ``skills/content/image-generation/SKILL.md``.
Any future Langfuse edit that drifts the default (or any in-tree edit)
will trip this snapshot and force a deliberate update.

The match is byte-for-byte intentionally — whitespace, double-brace
escaping, and trailing newlines are all part of the contract. The
SKILL.md loader normalizes every template to YAML ``|`` clip semantics
(exactly one trailing newline), so the snapshot ends with a newline.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import services.image_decision_agent as ida
from services.image_decision_agent import ImagePlanResult, plan_images
from services.prompt_manager import UnifiedPromptManager
from services.site_config import SiteConfig


@pytest.fixture
def pm() -> UnifiedPromptManager:
    """Fresh UnifiedPromptManager — YAML-only, no Langfuse, no DB."""
    return UnifiedPromptManager()


# ---------------------------------------------------------------------------
# Snapshot body
#
# This string is the production prompt text as it lived in
# ``services/image_decision_agent.py`` immediately before the YAML
# migration — produced by interpolating the fixture kwargs below into
# the original inline f-string. Keeping the snapshot inline (rather than
# reading from a frozen file) means a reviewer can read both halves of
# the contract in one place.
# ---------------------------------------------------------------------------


_IMAGE_DECISION_EXPECTED = """You are an image director for a tech blog. Analyze this article and decide what images would make it more engaging.

ARTICLE TOPIC: Test Topic
CATEGORY: Test Category

SECTIONS:
  1. Section One
  2. Section Two

AVAILABLE IMAGE SOURCES:
- "sdxl": AI-generated images. Best for: abstract concepts, mood imagery, artistic visualizations, diagrams, futuristic scenes. Styles: blueprint, dramatic, minimal, isometric, macro, editorial.
- "pexels": Stock photography. Best for: real-world objects, hardware close-ups, workspaces, screens with code, servers, people working (if appropriate).

RULES:
1. Pick 3 sections that would benefit most from a visual (skip sections that are mostly code)
2. For each, decide: sdxl or pexels? What style? What specific image?
3. Also decide on 1 featured image (the hero/header image for the article)
4. Be specific in your prompts — describe the exact scene, not vague concepts
5. NEVER include text, words, letters, or faces in SDXL images

Output ONLY valid JSON (no markdown, no explanation):
{
  "featured": {
    "source": "sdxl" or "pexels",
    "style": "style_name",
    "prompt": "detailed image prompt or search query",
    "reasoning": "why this image works for the hero"
  },
  "inline": [
    {
      "section": "exact section title",
      "source": "sdxl" or "pexels",
      "style": "style_name",
      "prompt": "detailed image prompt or search query",
      "reasoning": "why this visual helps this section"
    }
  ]
}
"""


# ---------------------------------------------------------------------------
# Snapshot test
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImageDecisionPromptSnapshot:
    def test_image_decision_snapshot(self, pm: UnifiedPromptManager):
        actual = pm.get_prompt(
            "image.decision",
            topic="Test Topic",
            category="Test Category",
            section_list="  1. Section One\n  2. Section Two",
            max_images=3,
        )
        assert actual == _IMAGE_DECISION_EXPECTED


# ---------------------------------------------------------------------------
# plan_images() — edge cases + error paths
#
# The snapshot test above only pins the prompt text. These exercise the
# agent's decision/parsing logic: model resolution, section extraction
# (real H2/H3 vs bold-text pseudo-headings vs none), the three-layer JSON
# recovery, default-filling, and the max_images cap. Every call uses a
# fake module-global ``http_client`` so no real Ollama / network / DB is
# touched. ``database_url=""`` is seeded so the (non-fatal) decision-log
# branch short-circuits instead of falling back to the conftest dummy DSN.
# ---------------------------------------------------------------------------


_NON_THINKING_MODEL = "gemma3:27b"  # matches no thinking-model substring


def _sc(**overrides) -> SiteConfig:
    """A SiteConfig wired for plan_images with no live pool/DB.

    ``model_role_image_decision`` supplies the fallback model (pool is
    None so the cost-tier resolver is skipped); ``database_url=""`` keeps
    the decision-logging branch off.
    """
    cfg = {
        "model_role_image_decision": _NON_THINKING_MODEL,
        "database_url": "",
        "ollama_base_url": "http://ollama.test:11434",
    }
    cfg.update(overrides)
    return SiteConfig(initial_config=cfg, pool=None)


class _FakeResp:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        # Tests only ever drive 200s; mirror httpx's "no-op on success".
        return None


class _FakeClient:
    """Stand-in for the lifespan-bound ``httpx.AsyncClient``.

    ``GET /api/tags`` reports ``model`` as installed; ``POST`` returns the
    canned generate body. Records calls so tests can assert the network
    was (not) reached.
    """

    def __init__(self, *, generate_text: str, model: str = _NON_THINKING_MODEL):
        self._generate_text = generate_text
        self._model = model
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []

    async def get(self, url, timeout=None):
        self.get_calls.append(url)
        return _FakeResp(payload={"models": [{"name": self._model}]})

    async def post(self, url, json=None, timeout=None):
        self.post_calls.append((url, json or {}))
        return _FakeResp(payload={"response": self._generate_text})


class _ExplodingClient:
    """A client that fails the test if any network method is called.

    Used to prove plan_images returns BEFORE reaching the HTTP block.
    """

    async def get(self, *a, **k):  # pragma: no cover - asserts non-invocation
        raise AssertionError("plan_images made a network call it should not have")

    async def post(self, *a, **k):  # pragma: no cover
        raise AssertionError("plan_images made a network call it should not have")


@pytest.mark.unit
class TestPlanImagesEdgeCases:
    async def test_no_model_notifies_operator_and_returns_empty(self, monkeypatch):
        """No cost-tier pool AND empty fallback key → page operator, empty plan."""
        notify = AsyncMock()
        monkeypatch.setattr(
            "services.integrations.operator_notify.notify_operator", notify
        )
        # Explicitly empty the fallback; pool=None means no tier resolution.
        cfg = _sc(model_role_image_decision="")

        result = await plan_images(
            "## Section\n\nBody.", topic="T", category="tech", site_config=cfg
        )

        assert isinstance(result, ImagePlanResult)
        assert result.images == []
        assert result.featured_image is None
        notify.assert_awaited_once()

    async def test_no_sections_returns_empty_without_network(self, monkeypatch):
        """Content with no headings short-circuits before any Ollama call."""
        monkeypatch.setattr(ida, "http_client", _ExplodingClient())

        result = await plan_images(
            "Just a flat paragraph with no headings whatsoever.",
            topic="T",
            site_config=_sc(),
        )

        assert result.images == []
        assert result.featured_image is None

    async def test_bold_pseudo_headings_drive_planning(self, monkeypatch):
        """Bold-text dividers (no real H2) are the #527 fallback section source."""
        body = (
            "**First Idea**\n\nsome prose\n\n**Second Idea**\n\nmore prose\n"
        )
        plan_json = json.dumps(
            {
                "featured": {"source": "sdxl", "style": "blueprint",
                             "prompt": "hero", "reasoning": "r"},
                "inline": [
                    {"section": "First Idea", "source": "pexels",
                     "style": "macro", "prompt": "p1", "reasoning": "r1"},
                ],
            }
        )
        client = _FakeClient(generate_text=plan_json)
        monkeypatch.setattr(ida, "http_client", client)

        result = await plan_images(body, topic="T", site_config=_sc())

        assert client.post_calls, "expected an Ollama generate call"
        assert result.featured_image is not None
        assert result.featured_image.source == "sdxl"
        assert len(result.images) == 1
        assert result.images[0].section_heading == "First Idea"
        assert result.images[0].source == "pexels"

    async def test_code_fenced_json_is_stripped_and_parsed(self, monkeypatch):
        """A ```json fenced body parses cleanly (fence-strip path)."""
        inner = {
            "featured": {"source": "pexels", "style": "editorial",
                         "prompt": "x", "reasoning": "y"},
            "inline": [],
        }
        fenced = "```json\n" + json.dumps(inner) + "\n```"
        monkeypatch.setattr(ida, "http_client", _FakeClient(generate_text=fenced))

        result = await plan_images("## A\n\nbody", topic="T", site_config=_sc())

        assert result.featured_image is not None
        assert result.featured_image.source == "pexels"

    async def test_embedded_json_extracted_from_reasoning(self, monkeypatch):
        """JSON buried in reasoning prose is recovered by the regex fallback."""
        inner = {
            "featured": {"source": "sdxl", "style": "minimal",
                         "prompt": "p", "reasoning": "r"},
            "inline": [],
        }
        noisy = f"Let me think it through. {json.dumps(inner)} That is my plan."
        monkeypatch.setattr(ida, "http_client", _FakeClient(generate_text=noisy))

        result = await plan_images("## A\n\nbody", topic="T", site_config=_sc())

        assert result.featured_image is not None
        assert result.featured_image.style == "minimal"

    async def test_unparseable_response_returns_raw_and_empty_plan(self, monkeypatch):
        """Non-JSON output yields an empty plan but preserves raw_response."""
        garbage = "I cannot produce a plan for this article, sorry."
        monkeypatch.setattr(ida, "http_client", _FakeClient(generate_text=garbage))

        result = await plan_images("## A\n\nbody", topic="T", site_config=_sc())

        assert result.images == []
        assert result.featured_image is None
        assert result.raw_response == garbage

    async def test_partial_entries_get_sensible_defaults(self, monkeypatch):
        """Missing fields fall back to defaults; featured prompt → topic."""
        partial = json.dumps(
            {
                "featured": {"source": "sdxl"},  # no style/prompt/reasoning
                "inline": [{"section": "S1"}],    # no source/style/prompt
            }
        )
        monkeypatch.setattr(ida, "http_client", _FakeClient(generate_text=partial))

        result = await plan_images(
            "## S1\n\nbody", topic="My Topic", site_config=_sc()
        )

        assert result.featured_image is not None
        assert result.featured_image.style == "editorial"
        assert result.featured_image.prompt == "My Topic"  # defaults to topic
        assert len(result.images) == 1
        assert result.images[0].source == "sdxl"
        assert result.images[0].style == "editorial"
        assert result.images[0].prompt == ""

    async def test_inline_images_capped_at_max_images(self, monkeypatch):
        """More inline entries than max_images → list is truncated."""
        inline = [
            {"section": f"S{i}", "source": "sdxl", "style": "editorial",
             "prompt": f"p{i}", "reasoning": ""}
            for i in range(5)
        ]
        body = json.dumps({"featured": {}, "inline": inline})
        monkeypatch.setattr(ida, "http_client", _FakeClient(generate_text=body))

        result = await plan_images(
            "## A\n\nbody", topic="T", max_images=2, site_config=_sc()
        )

        assert len(result.images) == 2
        # Empty featured dict is falsy → no featured image parsed.
        assert result.featured_image is None
