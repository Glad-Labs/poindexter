"""Unit tests for the qa.rewrite atom (QA rescue cycle)."""

from __future__ import annotations

import pytest

from modules.content.atoms import qa_rewrite
from services.site_config import SiteConfig


def _site_config():
    # pipeline_writer_model lets resolve_writer_model return without raising.
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

    async def test_failing_feedback_includes_advisory_excludes_passing(self, monkeypatch):
        # The reviser must hear ALL failing reviews — blocking vetoes AND
        # advisory rails (topic_delivery / g_eval / ragas), which carry the
        # most specific fixes ("name the product"). Only PASSING reviews are
        # dropped. (Prior behavior excluded advisory feedback, leaving the
        # reviser blind to the issues most worth fixing.)
        seen = {}

        async def _fake_chat(prompt, **kw):
            seen["prompt"] = prompt
            return "revised body."

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t2",
            "content": "draft",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "FIX_THIS"},
                {"reviewer": "topic_delivery", "approved": False, "score": 20.0,
                 "provider": "consistency_gate", "advisory": True, "feedback": "NAME_THE_PRODUCT"},
                {"reviewer": "deepeval_g_eval", "approved": True, "score": 90.0,
                 "provider": "ollama", "advisory": False, "feedback": "PASSED_NOISE"},
            ],
        }
        await qa_rewrite.run(state)
        assert "FIX_THIS" in seen["prompt"]              # blocking veto — included
        assert "NAME_THE_PRODUCT" in seen["prompt"]      # failing advisory — now included
        assert "(advisory)" in seen["prompt"]            # …and labeled as advisory
        assert "PASSED_NOISE" not in seen["prompt"]      # passing review — excluded

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

    async def test_uses_cross_model_reviser_when_set(self, monkeypatch):
        # qa_rewrite_model routes the revise step to a DIFFERENT model than the
        # writer (resolve_writer_model strips the ollama/ prefix).
        seen = {}

        async def _fake_chat(prompt, **kw):
            seen["model"] = kw.get("model")
            return "revised body."

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        sc = SiteConfig(initial_config={
            "pipeline_writer_model": "glm-writer",
            "qa_rewrite_model": "ollama/gemma-reviser",
        })
        state = {
            "task_id": "t5", "content": "draft", "qa_rewrite_attempts": 0,
            "site_config": sc,
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "advisory": False,
                 "provider": "ollama", "feedback": "fix"},
            ],
        }
        await qa_rewrite.run(state)
        assert seen["model"] == "gemma-reviser"

    async def test_falls_back_to_writer_when_reviser_unset(self, monkeypatch):
        # Empty qa_rewrite_model → reviser=None → writer model (backcompat).
        seen = {}

        async def _fake_chat(prompt, **kw):
            seen["model"] = kw.get("model")
            return "revised body."

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        sc = SiteConfig(initial_config={
            "pipeline_writer_model": "glm-writer",
            "qa_rewrite_model": "",
        })
        state = {
            "task_id": "t6", "content": "draft", "qa_rewrite_attempts": 0,
            "site_config": sc,
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "advisory": False,
                 "provider": "ollama", "feedback": "fix"},
            ],
        }
        await qa_rewrite.run(state)
        assert seen["model"] == "glm-writer"


@pytest.mark.unit
class TestQaRewriteBriefingLeakStrip:
    """The reviser can echo its revision briefing before the article (prod
    task 342a26b7, 2026-07-23) — and the rescue loop re-enters at
    qa.programmatic, never re-running normalize_draft, so qa.rewrite itself
    must strip the scaffold before returning content."""

    _BRIEFING_ECHO = (
        "*   Task: Revise a draft article based on specific fixes.\n"
        "    *   Constraints: Preserve structure, headings, length, links. "
        "Return Markdown body only.\n"
        "    *   Fix 1: Unlinked citation — rephrase as an observation.\n"
        "    *   Fix 2: Terminology contradiction.\n\n"
        "## The Real Article\n\nActual prose the reader should see.\n"
    )

    # Label-free dialect (prod task ece2f516, 2026-07-24, poindexter#897):
    # bare "Revise a draft article about…" opener, unlabeled deep-indented
    # constraint bullets, first-person deliberation, and the briefing fused
    # onto the article's first heading with no blank line.
    _BARE_BRIEFING_ECHO = (
        'Revise a draft article about "Postgres survival for startups."\n'
        "\n"
        "        *   Preserve structure, headings, length, links, "
        "citations, and voice.\n"
        "        *   No new sections/removals unless required by fixes.\n"
        "    *   *Wait*, the fix list also asks for a tighter closing.\n"
        "    *   Verify Markdown structure.## The startup's Postgres "
        "survival guide\n"
        "\n"
        "Most seed-stage teams meet their first real outage inside "
        "Postgres.\n"
    )

    async def test_briefing_echo_is_stripped_from_revision(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            return self._BRIEFING_ECHO

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t-leak",
            "content": "# Draft\n\nweak body.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak intro"},
            ],
        }
        out = await qa_rewrite.run(state)
        assert out["content"].startswith("## The Real Article")
        assert "Fix 1" not in out["content"]
        assert "Revise a draft article" not in out["content"]

    async def test_label_free_briefing_echo_is_stripped(self, monkeypatch):
        # ece2f516 regression: no "Task:"/"Fix N:" labels, heading fused
        # mid-line — the strip must still fire and re-anchor the article.
        async def _fake_chat(prompt, **kw):
            return self._BARE_BRIEFING_ECHO

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t-bare-leak",
            "content": "# Draft\n\nweak body.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak intro"},
            ],
        }
        out = await qa_rewrite.run(state)
        assert out["content"].startswith(
            "## The startup's Postgres survival guide"
        )
        assert "Most seed-stage teams" in out["content"]
        assert "Revise a draft article" not in out["content"]
        assert "*Wait*" not in out["content"]

    async def test_clean_revision_is_untouched(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            return "## Clean Heading\n\nNo scaffold here, just prose.\n"

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            "task_id": "t-clean",
            "content": "# Draft\n\nweak body.\n",
            "qa_rewrite_attempts": 0,
            "site_config": _site_config(),
            "qa_rail_reviews": [
                {"reviewer": "ollama_critic", "approved": False, "score": 55.0,
                 "provider": "ollama", "advisory": False, "feedback": "weak intro"},
            ],
        }
        out = await qa_rewrite.run(state)
        assert out["content"] == "## Clean Heading\n\nNo scaffold here, just prose."


@pytest.mark.unit
class TestQaRewriteTruncationGuard:
    """A truncated revision must never replace the complete draft under
    review (Glad-Labs/poindexter#984) — same degrade-to-reject shape as an
    empty revision, plus a dedicated finding."""

    _STATE = {
        "task_id": "t-trunc",
        "qa_rewrite_attempts": 0,
        "qa_rail_reviews": [
            {"reviewer": "ollama_critic", "approved": False, "advisory": False,
             "provider": "ollama", "feedback": "weak"},
        ],
    }

    async def test_truncated_revision_degrades_to_reject(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            # Verbatim failure shape from prod: severed mid-word.
            return "## Revised\n\nreplicas solve read scaling, but th"

        findings = []
        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        monkeypatch.setattr(
            "utils.findings.emit_finding",
            lambda **kw: findings.append(kw),
        )
        state = {
            **self._STATE,
            "content": "# Original\n\nComplete draft to keep.\n",
            "site_config": _site_config(),
        }
        out = await qa_rewrite.run(state)
        assert "content" not in out  # prior draft kept
        assert out["qa_rewrite_attempts"] == 1
        assert out["qa_rail_reviews"] == [{"__reset__": True}]
        assert [f["kind"] for f in findings] == ["qa_rewrite_truncated_revision"]

    async def test_complete_revision_still_flows(self, monkeypatch):
        async def _fake_chat(prompt, **kw):
            return "## Revised\n\nReplicas solve read scaling properly now."

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            **self._STATE,
            "content": "# Original\n\ndraft.\n",
            "site_config": _site_config(),
        }
        out = await qa_rewrite.run(state)
        assert out["content"].endswith("properly now.")

    async def test_max_tokens_forwarded_from_setting(self, monkeypatch):
        seen = {}

        async def _fake_chat(prompt, **kw):
            seen.update(kw)
            return "revised body."

        monkeypatch.setattr("services.llm_text.ollama_chat_text", _fake_chat)
        state = {
            **self._STATE,
            "content": "draft.",
            "site_config": _site_config(),  # key unset -> code default
        }
        await qa_rewrite.run(state)
        assert seen["max_tokens"] == 16384

        sc = SiteConfig(initial_config={
            "pipeline_writer_model": "test-writer",
            "content_router_qa_rewrite_max_tokens": "9000",
        })
        await qa_rewrite.run({**self._STATE, "content": "draft.", "site_config": sc})
        assert seen["max_tokens"] == 9000
