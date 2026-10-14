"""Unit tests for ``modules/content/topic_decision_artifact.py`` (#146).

Covers the topic-decision artifact builder + the research summarizer
helper: the topic-specific artifact shape and the summary truncation
rules. The pause itself is ``topic_proposal_service``'s and is tested
there.
"""

from __future__ import annotations

from typing import Any

from poindexter.modules.content.topic_decision_artifact import (
    _summarize_research,
    build_topic_decision_artifact,
)

# ---------------------------------------------------------------------------
# build_topic_decision_artifact — shape contract
# ---------------------------------------------------------------------------


class TestArtifactShape:
    def test_full_context_produces_full_artifact(self):
        ctx: dict[str, Any] = {
            "topic": "Why custom water cooling beats AIOs in 2026",
            "primary_keyword": "custom water cooling",
            "tags": ["pc-hardware", "cooling"],
            "category": "hardware",
            "topic_source": "anticipation_engine",
            "research_context": "Custom loops outperform on sustained workloads.",
            "novelty_score": 0.78,
            "internal_link_score": 0.62,
            "category_balance": "good",
        }
        artifact = build_topic_decision_artifact(ctx)
        assert artifact["topic"] == ctx["topic"]
        assert artifact["primary_keyword"] == "custom water cooling"
        assert artifact["tags"] == ["pc-hardware", "cooling"]
        assert artifact["category_suggestion"] == "hardware"
        assert artifact["source"] == "anticipation_engine"
        assert artifact["research_summary"].startswith("Custom loops outperform")
        signals = artifact["score_signals"]
        assert signals["novelty"] == 0.78
        assert signals["internal_link_potential"] == 0.62
        assert signals["category_balance"] == "good"

    def test_missing_research_omits_summary_field(self):
        artifact = build_topic_decision_artifact({"topic": "Hello"})
        assert "research_summary" not in artifact

    def test_empty_research_string_omits_summary(self):
        artifact = build_topic_decision_artifact(
            {"topic": "Hello", "research_context": ""},
        )
        assert "research_summary" not in artifact

    def test_primary_keyword_falls_back_to_first_tag(self):
        artifact = build_topic_decision_artifact(
            {"topic": "Topic", "tags": ["llm", "rag"]},
        )
        assert artifact["primary_keyword"] == "llm"

    def test_primary_keyword_empty_when_no_tags(self):
        artifact = build_topic_decision_artifact({"topic": "Topic"})
        assert artifact["primary_keyword"] == ""

    def test_default_source_when_unset(self):
        artifact = build_topic_decision_artifact({"topic": "Topic"})
        assert artifact["source"] == "anticipation_engine"

    def test_score_signals_default_to_none(self):
        artifact = build_topic_decision_artifact({"topic": "Topic"})
        signals = artifact["score_signals"]
        assert signals == {
            "novelty": None,
            "internal_link_potential": None,
            "category_balance": None,
        }

    def test_tags_accepts_comma_string(self):
        # Operator might pass tags as a string from a UI form. The
        # builder normalises to list[str].
        artifact = build_topic_decision_artifact(
            {"topic": "Topic", "tags": "ai, llm , local-inference"},
        )
        assert artifact["tags"] == ["ai", "llm", "local-inference"]

    def test_tags_drops_empty_entries(self):
        artifact = build_topic_decision_artifact(
            {"topic": "Topic", "tags": ["", "ai", "  ", "rag"]},
        )
        assert artifact["tags"] == ["ai", "rag"]

    def test_category_suggestion_empty_when_unset(self):
        artifact = build_topic_decision_artifact({"topic": "Topic"})
        assert artifact["category_suggestion"] == ""

    def test_artifact_is_json_serializable(self):
        # Critical — pause_at_gate JSON-encodes the artifact for the
        # gate_artifact JSONB column. Anything non-serializable would
        # blow up at insert time.
        import json

        artifact = build_topic_decision_artifact(
            {
                "topic": "Topic",
                "tags": ["ai"],
                "novelty_score": 0.5,
                "research_context": "Some research notes.",
            },
        )
        encoded = json.dumps(artifact)
        decoded = json.loads(encoded)
        assert decoded["topic"] == "Topic"


# ---------------------------------------------------------------------------
# _summarize_research — truncation rules
# ---------------------------------------------------------------------------


class TestSummarizeResearch:
    def test_empty_input_returns_empty(self):
        assert _summarize_research("") == ""
        assert _summarize_research(None) == ""

    def test_short_input_unchanged(self):
        text = "This is fine."
        assert _summarize_research(text) == text

    def test_collapses_whitespace(self):
        text = "Many   spaces\nand\tnewlines."
        assert _summarize_research(text) == "Many spaces and newlines."

    def test_truncates_to_about_200_words(self):
        # 250 words — should clip to ~200.
        text = " ".join([f"word{i}" for i in range(250)])
        out = _summarize_research(text)
        # Truncated form ends with the ellipsis marker.
        assert out.endswith("...")
        # Word-count budget — should be <= 200 + the trailing ellipsis.
        no_ellipsis = out[:-3].strip()
        assert len(no_ellipsis.split(" ")) <= 200
        # And the first word survives.
        assert no_ellipsis.startswith("word0")

    def test_exact_200_words_not_truncated(self):
        text = " ".join([f"w{i}" for i in range(200)])
        out = _summarize_research(text)
        assert not out.endswith("...")
        assert out == text

    def test_dict_input_pulls_summary_field(self):
        text = "Here is the summary."
        assert _summarize_research({"summary": text}) == text

    def test_dict_input_falls_back_to_text_field(self):
        assert _summarize_research({"text": "the text"}) == "the text"

    def test_dict_input_with_only_sources_joins_titles(self):
        sources = [
            {"title": "First source"},
            {"title": "Second source"},
            {"title": ""},
        ]
        out = _summarize_research({"sources": sources})
        assert "First source" in out
        assert "Second source" in out

    def test_dict_input_empty_returns_empty(self):
        assert _summarize_research({}) == ""
