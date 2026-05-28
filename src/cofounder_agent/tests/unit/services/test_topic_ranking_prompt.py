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


# ---------------------------------------------------------------------------
# Error paths
#
# These pin the behavior of the *error* surface of get_prompt — when the
# caller forgets a variable, asks for a nonexistent key, etc. The
# snapshot above pins the happy path; these pin the unhappy path so
# the prompt_manager's error messages (which operators read in logs)
# stay stable.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTopicRankingErrorPaths:
    def test_missing_weights_descr_raises_keyerror_naming_var(
        self, pm: UnifiedPromptManager
    ):
        """A pipeline caller that forgets ``weights_descr`` should get a
        KeyError whose message names the missing variable — not the
        opaque single-letter KeyError str.format() raises by default."""
        with pytest.raises(KeyError) as excinfo:
            pm.get_prompt("topic.ranking", cand_block="[c1] X — Y")
        assert "weights_descr" in str(excinfo.value)
        assert "topic.ranking" in str(excinfo.value)

    def test_missing_cand_block_raises_keyerror_naming_var(
        self, pm: UnifiedPromptManager
    ):
        with pytest.raises(KeyError) as excinfo:
            pm.get_prompt("topic.ranking", weights_descr="- TRAFFIC (50%)")
        assert "cand_block" in str(excinfo.value)

    def test_unknown_prompt_key_raises_with_available_list(
        self, pm: UnifiedPromptManager
    ):
        """Typo'd keys must surface the available-keys list so the
        caller can self-correct without grepping the YAML tree."""
        with pytest.raises(KeyError) as excinfo:
            pm.get_prompt("topic.rankin", weights_descr="x", cand_block="y")
        msg = str(excinfo.value)
        assert "topic.rankin" in msg
        assert "Available" in msg
        # The real key must appear in the available list:
        assert "topic.ranking" in msg


# ---------------------------------------------------------------------------
# Structural invariants
#
# The snapshot above pins the *exact* text for one fixed input. These
# tests pin the *shape* of the rendered prompt for arbitrary inputs —
# what must always be present regardless of what the operator passes
# in. If a future Langfuse edit drops "Return STRICT JSON", these will
# catch it even if the snapshot test's inputs happen to land elsewhere.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTopicRankingStructuralInvariants:
    def test_empty_variables_still_render_strict_json_directive(
        self, pm: UnifiedPromptManager
    ):
        """Empty inputs are degenerate but must not crash — the LLM
        will see an empty Candidates block and return ``{}``, which is
        fine. The STRICT JSON directive must still survive so the
        downstream parser doesn't get prose."""
        actual = pm.get_prompt("topic.ranking", weights_descr="", cand_block="")
        assert "Return STRICT JSON" in actual
        assert "Return ONLY the JSON, no commentary." in actual

    def test_json_braces_in_template_render_as_literal_braces(
        self, pm: UnifiedPromptManager
    ):
        """The YAML uses ``{{`` and ``}}`` to escape literal braces
        through str.format(). If that escaping breaks (e.g. someone
        switches the template engine), the LLM gets malformed JSON
        examples and starts hallucinating schemas. Pin it."""
        actual = pm.get_prompt(
            "topic.ranking", weights_descr="w", cand_block="c"
        )
        # The example schema in the template should render literally —
        # NOT be interpreted as another format placeholder:
        assert '{"<id>": {"score": <0-100>' in actual
        assert '"breakdown":' in actual

    def test_unicode_em_dash_preserved_through_format(
        self, pm: UnifiedPromptManager
    ):
        """Topic summaries from research providers (Pexels, IGDB,
        topic-rss feeds) routinely contain em-dashes, smart quotes,
        and non-ASCII characters. str.format() must pass them through
        verbatim — no encoding round-trip, no normalization."""
        cand_block = "[c1] Café résumé — naïve fiancé"
        actual = pm.get_prompt(
            "topic.ranking", weights_descr="x", cand_block=cand_block
        )
        assert "Café résumé — naïve fiancé" in actual

    def test_extra_kwargs_are_ignored_not_an_error(
        self, pm: UnifiedPromptManager
    ):
        """The pipeline often passes a context dict with **kwargs that
        includes more keys than any single prompt needs. Extra kwargs
        must be ignored (str.format() default behavior) so callers can
        share one context across multiple get_prompt calls without
        filtering."""
        actual = pm.get_prompt(
            "topic.ranking",
            weights_descr="w",
            cand_block="c",
            niche_slug="ai-ml",  # not referenced by this prompt
            run_id=42,
        )
        # The unused vars must NOT appear in the rendered output
        # (would mean str.format() accidentally substituted them):
        assert "ai-ml" not in actual
        assert "42" not in actual

    def test_multiline_cand_block_preserves_newlines(
        self, pm: UnifiedPromptManager
    ):
        """topic_ranking.py joins candidates with ``\\n`` to build the
        block. The renderer must preserve those newlines so the LLM
        sees one candidate per line, not a single wall of text."""
        cand_block = "[c1] A\n[c2] B\n[c3] C\n[c4] D"
        actual = pm.get_prompt(
            "topic.ranking", weights_descr="x", cand_block=cand_block
        )
        # The candidate block should appear as-is with all newlines intact:
        assert "[c1] A\n[c2] B\n[c3] C\n[c4] D" in actual
        # And it should sit between the "Candidates:" header and the
        # "Return STRICT JSON" instruction (i.e. order is preserved):
        assert actual.index("Candidates:") < actual.index("[c1] A")
        assert actual.index("[c4] D") < actual.index("Return STRICT JSON")

    def test_rendered_prompt_ends_with_trailing_newline(
        self, pm: UnifiedPromptManager
    ):
        """The YAML block-scalar uses ``|`` (literal, keep), so a
        trailing newline is part of the contract. Some LLM providers
        strip leading/trailing whitespace from prompts; we want the
        canonical form to ship with the newline so operator-side diff
        tools (Langfuse) show identical content."""
        actual = pm.get_prompt(
            "topic.ranking", weights_descr="w", cand_block="c"
        )
        assert actual.endswith("\n")
        # Exactly one trailing newline — not two (YAML literal-keep
        # is single-newline; double-newline would indicate the block
        # scalar style was changed to ``|+`` (keep-all)):
        assert not actual.endswith("\n\n")
