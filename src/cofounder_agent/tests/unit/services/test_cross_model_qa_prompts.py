"""Snapshot tests pinning the cross_model_qa stage YAML prompts.

These tests are the public contract for the QA aggregate-rewrite prompt
that was migrated out of the inline ``QA_AGGREGATE_REWRITE_PROMPT``
constant in ``services/stages/cross_model_qa.py`` into
``prompts/content_qa.yaml`` during Lane A batch 2 of the OSS migration.
Any future Langfuse edit that drifts the YAML default (or any in-tree
YAML edit) will trip this snapshot and force a deliberate update.

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
# ``services/stages/cross_model_qa.py`` immediately before the YAML
# migration. Keeping the snapshot inline (rather than reading from a
# frozen file) means a reviewer can read both halves of the contract in
# one place.
# ---------------------------------------------------------------------------


_AGGREGATE_REWRITE_EXPECTED = """You are revising your own draft to fix
EVERY issue a team of editors identified. Do NOT rewrite the entire
article. Do NOT add new sections. Only fix the specific problems
listed below, making the minimum changes needed to resolve each one.

Keep the same structure, same headings, same code examples where they
aren't affected by the issues, same length (within 10%).

TITLE: Test Title

ISSUES TO FIX (from programmatic validator + LLM critics + consistency checker):
[critical] fabricated_person: Made-up CEO quote
[warning] generic_heading: Replace "Conclusion" heading

How to interpret:
- "[critical]" means the issue will block publishing if not fixed. Top priority.
- "[warning]" means it will drag the score down but won't veto. Fix these too.
- "Contradictions:" lines mean sections disagree with each other — rewrite the
  weaker or later one to align with the stronger or earlier one.
- "Fabricated" or "Impossible" lines mean the draft made up a person, statistic,
  quote, or company claim. Remove the fabrication entirely; do NOT replace it
  with another made-up fact — either soften to a general statement or cut.
- "Generic section title" means replace the heading with a creative, benefit-
  focused alternative (never "Introduction", "Conclusion", "Summary", etc.).
- "Filler intro" means rewrite the first paragraph with a concrete hook, not
  "In this post..." or "In today's fast-paced world...".

ORIGINAL DRAFT:
Test draft body.

Return ONLY the revised article text. Do not include meta-commentary,
notes about what you changed, or markdown code fences around the output."""


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCrossModelQaPromptSnapshots:
    def test_aggregate_rewrite_snapshot(self, pm: UnifiedPromptManager):
        actual = pm.get_prompt(
            "qa.aggregate_rewrite",
            title="Test Title",
            issues_to_fix=(
                "[critical] fabricated_person: Made-up CEO quote\n"
                "[warning] generic_heading: Replace \"Conclusion\" heading"
            ),
            content="Test draft body.",
        )
        assert actual == _AGGREGATE_REWRITE_EXPECTED
