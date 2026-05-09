"""Snapshot test pinning the image_decision_agent YAML prompt.

This test is the public contract for the image-director prompt that was
migrated out of the inline f-string in
``services/image_decision_agent.py`` into ``prompts/image_generation.yaml``
during Lane A batch 3 of the OSS migration. Any future Langfuse edit
that drifts the YAML default (or any in-tree YAML edit) will trip this
snapshot and force a deliberate update.

The match is byte-for-byte intentionally — whitespace, double-brace
escaping, and trailing newlines are all part of the contract.
"""

from __future__ import annotations

import pytest

from services.prompt_manager import UnifiedPromptManager


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
}"""


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
