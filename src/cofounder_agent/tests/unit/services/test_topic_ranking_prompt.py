"""Snapshot test pinning the topic.ranking YAML prompt.

This test is the public contract for the topic-ranking prompt that was
migrated out of the inline f-string in
``services/topic_ranking.llm_final_score`` into
``prompts/research.yaml`` during Lane A batch 4 of the OSS migration.
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
# ``services/topic_ranking.py`` immediately before the YAML migration.
# Keeping the snapshot inline (rather than reading from a frozen file)
# means a reviewer can read both halves of the contract in one place.
# ---------------------------------------------------------------------------


_TOPIC_RANKING_EXPECTED = """You are scoring topic candidates for a content pipeline against the operator's weighted goals.

Goals (weight in pct):
- TRAFFIC (weight 50%): Topic likely to attract organic search traffic; trending keyword, broad appeal, evergreen demand.
- EDUCATION (weight 50%): Topic that teaches the reader something concrete and useful they didn't know before.

Candidates:
[c1] Test Title — Test summary
[c2] Other Title — Other summary

Return STRICT JSON keyed by candidate id, of the form:
{"<id>": {"score": <0-100>, "breakdown": {"<GOAL_TYPE>": <weighted contribution 0-1>, ...}}, ...}

The breakdown values per candidate should approximately sum to (score / 100).
Return ONLY the JSON, no commentary.
"""


# ---------------------------------------------------------------------------
# Snapshot test
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTopicRankingPromptSnapshot:
    def test_topic_ranking_snapshot(self, pm: UnifiedPromptManager):
        weights_descr = (
            "- TRAFFIC (weight 50%): Topic likely to attract organic search "
            "traffic; trending keyword, broad appeal, evergreen demand.\n"
            "- EDUCATION (weight 50%): Topic that teaches the reader something "
            "concrete and useful they didn't know before."
        )
        cand_block = "[c1] Test Title — Test summary\n[c2] Other Title — Other summary"
        actual = pm.get_prompt(
            "topic.ranking",
            weights_descr=weights_descr,
            cand_block=cand_block,
        )
        assert actual == _TOPIC_RANKING_EXPECTED
