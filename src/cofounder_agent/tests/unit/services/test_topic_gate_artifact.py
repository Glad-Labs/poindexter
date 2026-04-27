"""Unit tests for ``services/stages/topic_decision_gate.py`` (#146).

Covers the topic-decision artifact builder + the research summarizer
helper. The Stage's pause/passthrough behaviour itself is exercised
by ``test_approval_gate_stage.py`` against the inner
``ApprovalGateStage``; here we verify only the topic-specific
artifact shape and the summary truncation rules.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.stage import StageResult
from services.stages.topic_decision_gate import (
    TopicDecisionGateStage,
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


# ---------------------------------------------------------------------------
# Stage wiring — confirm the wrapper Stage routes through ApprovalGateStage
# with the right gate_name + artifact_fn, and that PluginConfig overrides
# don't get to override gate_name.
# ---------------------------------------------------------------------------


class TestStageMetadata:
    def test_stable_name(self):
        stage = TopicDecisionGateStage()
        assert stage.name == "topic_decision_gate"

    def test_halts_on_failure(self):
        # Approval gates must halt — silently continuing past a gate
        # failure would defeat the point of the HITL checkpoint.
        assert TopicDecisionGateStage().halts_on_failure is True


@pytest.mark.asyncio
class TestStageExecution:
    async def _make_context(self) -> dict[str, Any]:
        from types import SimpleNamespace

        site_cfg = SimpleNamespace(
            get=lambda key, default=None: {
                "pipeline_gate_topic_decision": "on",
            }.get(key, default),
            get_int=lambda key, default=0: int(default),
            get_bool=lambda key, default=False: False,
        )
        pool = MagicMock()
        return {
            "task_id": "t-topic-1",
            "topic": "A great topic",
            "tags": ["ai"],
            "site_config": site_cfg,
            "database_service": SimpleNamespace(pool=pool),
        }

    async def test_passes_topic_decision_gate_name(self):
        ctx = await self._make_context()
        captured: dict[str, Any] = {}

        async def _fake_pause(**kwargs):
            captured.update(kwargs)
            return {
                "ok": True,
                "task_id": kwargs["task_id"],
                "gate_name": kwargs["gate_name"],
                "paused_at": "2026-04-26T12:00:00+00:00",
                "notify": {"sent": True, "reason": "ok"},
            }

        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(side_effect=_fake_pause),
        ):
            result = await TopicDecisionGateStage().execute(ctx, {})

        assert isinstance(result, StageResult)
        assert result.ok is True
        assert result.continue_workflow is False
        assert captured["gate_name"] == "topic_decision"
        # Artifact shape is the topic-specific one.
        assert captured["artifact"]["topic"] == "A great topic"
        assert captured["artifact"]["tags"] == ["ai"]
        assert "score_signals" in captured["artifact"]

    async def test_plugin_config_cannot_override_gate_name(self):
        # The wrapper Stage pins gate_name="topic_decision". A misconfigured
        # PluginConfig pointing the gate at "preview_approval" must NOT
        # silently re-route — the topic-decision gate stays the topic-decision
        # gate.
        ctx = await self._make_context()
        captured: dict[str, Any] = {}

        async def _fake_pause(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "paused_at": "x", "notify": {"sent": False}}

        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(side_effect=_fake_pause),
        ):
            await TopicDecisionGateStage().execute(
                ctx, {"gate_name": "preview_approval"},
            )

        assert captured["gate_name"] == "topic_decision"

    async def test_plugin_config_can_pass_other_keys(self):
        # Other PluginConfig keys (skip_if_setting, halt_status) should
        # still flow through. We can't observe them from the artifact
        # capture, but execution should succeed without raising.
        ctx = await self._make_context()

        async def _fake_pause(**kwargs):
            return {"ok": True, "paused_at": "x", "notify": {"sent": False}}

        with patch(
            "services.stages.approval_gate.pause_at_gate",
            AsyncMock(side_effect=_fake_pause),
        ):
            result = await TopicDecisionGateStage().execute(
                ctx, {"halt_status": "in_progress"},
            )
        assert result.ok is True
