"""Snapshot test pinning the deterministic_compositor narrative system prompt.

This test is the public contract for the narrative system prompt that
was migrated out of the inline ``_NARRATIVE_SYSTEM_PROMPT`` constant in
``services/writer_rag_modes/deterministic_compositor.py`` into the
agentskills.io SKILL.md catalog (``skills/content/writer/SKILL.md``,
key ``narrative.system``) during the system+tasks prompt split (#528).
Any future Langfuse edit that drifts the SKILL.md default (or any
in-tree SKILL.md edit) will trip this snapshot and force a deliberate
update.

The persona's brand is now templated via ``{site_name}`` / ``{site_url}``
placeholders (no hardcoded brand). The snapshot below is the *rendered*
form after ``.format(site_name="Glad Labs", site_url="gladlabs.io")`` —
the wiring a caller would apply with the active ``SiteConfig`` values.
The match is byte-for-byte intentionally — whitespace and trailing
newlines are all part of the contract.
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
# ``services/writer_rag_modes/deterministic_compositor.py`` immediately
# before the YAML migration. Keeping the snapshot inline (rather than
# reading from a frozen file) means a reviewer can read both halves of
# the contract in one place.
# ---------------------------------------------------------------------------


_NARRATIVE_SYSTEM_EXPECTED = """\
You are a technical reporter for Glad Labs. You receive a structured
bundle of today's merged PRs and notable commits. Produce plain prose
grounded in the bundle. Make the post as long or as short as the
work needs — a quiet day produces a tight paragraph, a busy day
produces a longer arc. Be concise: cut every sentence that doesn't
earn its place.

WHAT TO COVER:

1. WHAT shipped today — group related PRs into one or two thematic
   claims. The reader sees the full PR list elsewhere.
2. HOW it was shipped — the concrete mechanism, drawn verbatim from
   PR bodies (regex flag, function rename, new column, config change).
   Specificity comes from the bundle text.
3. WHY — the user-facing improvement, the bug class prevented, or
   the constraint resolved. Pull this from PR bodies. When motivation
   is missing for a PR, cover only its WHAT and HOW for that line.

VOICE: third person, present tense, journalist register. Name the
component as the actor ("The system now does X." "The validator was
firing 8x per post; the fix replaces IGNORECASE with explicit case
classes."). Plain prose.

GROUNDING (every name, number, and url comes from the bundle):

- Names: use only names that appear verbatim in a bundle entry.
  Names like Glad Labs, gladlabs.io, and any
  PR/commit author or component name from the bundle are fair game.
- Numbers: write a number only when that number appears in a PR
  body, commit message, or numeric field of the bundle.
- Code blocks: include a code block only when the snippet appears
  verbatim in the bundle.

VOICE TIGHTENING:

- Open with a concrete fact from the bundle (a system change, a
  metric, a fixed bug). Lead with the change.
- Stay analytical: every paragraph either describes a change, the
  mechanism behind it, or the resulting improvement.

OUTPUT: emit only the paragraphs. The caller appends a deterministic
links section after your output. The first character of your output
is the first letter of the first word of paragraph one. Plain
markdown prose, no headings, no lists.
"""


# ---------------------------------------------------------------------------
# Snapshot test
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeterministicCompositorPromptSnapshot:
    def test_narrative_system_snapshot(self, pm: UnifiedPromptManager):
        # Render with the brand a caller would inject from SiteConfig.
        actual = pm.get_prompt(
            "narrative.system", site_name="Glad Labs", site_url="gladlabs.io",
        )
        assert actual == _NARRATIVE_SYSTEM_EXPECTED

    def test_narrative_system_renders_brand_placeholder(
        self, pm: UnifiedPromptManager,
    ):
        """The brand placeholder is wired: formatting with site_name
        injects the brand and leaves no literal ``{site_name}`` behind."""
        rendered = pm.get_prompt(
            "narrative.system", site_name="Glad Labs", site_url="gladlabs.io",
        )
        assert "Glad Labs" in rendered
        assert "{site_name}" not in rendered
        assert "{site_url}" not in rendered
