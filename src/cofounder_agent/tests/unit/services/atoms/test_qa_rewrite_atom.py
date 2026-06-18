"""Unit tests for the qa.rewrite atom (QA rescue cycle)."""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_rewrite
from services.site_config import SiteConfig


def _site_config():
    # pipeline_writer_model lets resolve_local_model return without raising.
    return SiteConfig(initial_config={"pipeline_writer_model": "test-writer"})


@pytest.mark.unit
class TestQaRewriteAtom:
    def test_meta(self):
        m = qa_rewrite.ATOM_META
        assert m.name == "qa.rewrite"
        assert "content" in m.requires
        assert "qa_rewrite_attempts" in m.requires
        assert set(m.produces) >= {"content", "qa_rewrite_attempts", "qa_rail_reviews"}

    async def test_successful_revision(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            # The prompt must carry the critic feedback + the draft.
            assert "weak intro" in prompt
            assert "ORIGINAL DRAFT" in prompt or "CURRENT DRAFT" in prompt
            return "# Revised\n\nMuch better body now.\n"

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)

        state = {
            "task_id": "t1",
            "content": "# Draft\n\nweak body.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak intro"},
                {"reviewer": "ragas_eval", "approved": True, "score": 88.0,
                 "provider": "ollama", "advisory": True, "feedback": "fine"},
            ],
        }
        out = await qa_rewrite.run(state)
        # The atom strips trailing/leading whitespace off the revised body.
        assert out["content"] == "# Revised\n\nMuch better body now."
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]
        assert out["qa_known_wrong_fact_only"] is False

    async def test_only_failing_nonadvisory_feedback_used(self, monkeypatch):
        seen = {}

        async def _fake_chat(prompt, **kw):
            seen["prompt"] = prompt
            return "revised body"

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t2",
            "content": "draft",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "FIX_THIS"},
                {"reviewer": "ragas_eval", "approved": False, "score": 40.0,
                 "provider": "ollama", "advisory": True, "feedback": "ADVISORY_NOISE"},
                {"reviewer": "deepeval_g_eval", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False, "feedback": "PASSED_NOISE"},
            ],
        }
        await qa_rewrite.run(state)
        assert "FIX_THIS" in seen["prompt"]
        assert "ADVISORY_NOISE" not in seen["prompt"]   # advisory excluded
        assert "PASSED_NOISE" not in seen["prompt"]      # passing excluded

    async def test_empty_writer_output_degrades_to_reject(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            return "   "  # whitespace -> treated as empty

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t3",
            "content": "# Original\n\nkeep me.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak"},
            ],
        }
        out = await qa_rewrite.run(state)
        # Degrade-to-reject: no new content (prior draft kept), counter still
        # burned so the loop terminates, reviews reset so the re-run is clean.
        assert "content" not in out
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]

    async def test_writer_exception_degrades_to_reject(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            raise RuntimeError("dispatch boom")

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t4",
            "content": "# Original\n\nkeep me.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak"},
            ],
        }
        out = await qa_rewrite.run(state)
        assert "content" not in out
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]

    async def test_no_content_or_site_config_burns_attempt(self):
        out = await qa_rewrite.run({"qa_rewrite_attempts": 0, "content": ""})
        assert "content" not in out
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]
