"""Unit tests for ``services/atoms/narrate_bundle.py``.

Regression guard for Glad-Labs/poindexter#354 — the writer prompt MUST
be built from the ``context_bundle`` (PR titles, URLs, bodies), NOT
from the ``topic`` string the dev_diary job stamps on the task row.

The bug: task 1745 (2026-05-04) had a rich bundle with PR #221
``fix(cli): rank-batch sys#N markers + auto-load POINDEXTER_SECRET_KEY``
but the post wrote about "implements a ranking algorithm for these
system markers" — a pure semantic riff on the topic string with no
reference to the actual PR. These tests pin the prompt-construction
contract so the regression can't recur silently.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.atoms.narrate_bundle import (
    _format_bundle_for_narrative,
    run,
)


# ---------------------------------------------------------------------------
# Repro fixture: the actual PR that triggered #354.
# ---------------------------------------------------------------------------


def _bundle_repro_pr_221() -> dict:
    """Bundle modeled on the exact #354 production repro.

    PR #221 had a substantive body explaining "rank-batch now accepts
    sys#N markers as INPUT to a CLI flag" — but the writer riffed on
    the topic string and wrote about "ranking algorithms" instead.
    """
    return {
        "date": "2026-05-04",
        "merged_prs": [
            {
                "number": 221,
                "title": "fix(cli): rank-batch sys#N markers + auto-load POINDEXTER_SECRET_KEY",
                "url": "https://github.com/Glad-Labs/poindexter/pull/221",
                "body": (
                    "## Summary\n\n"
                    "Two operator-friction fixes: `topics rank-batch` now "
                    "accepts `sys#N` and `#N` markers (the same labels "
                    "`topics show-batch` prints), not just raw UUIDs. Mixed "
                    "lists work; unknown markers fail with a friendly Click "
                    "error. The marker resolver accepts existing markers as "
                    "INPUT to a CLI flag — it does not rank, sort, or "
                    "reformat terminal output.\n\n"
                    "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
                ),
                "author": "matty",
                "merged_at": "2026-05-04T11:00:00Z",
            },
        ],
        "notable_commits": [
            {
                "sha": "abc1234",
                "subject": "fix(cli): accept sys#N markers in rank-batch",
                "prefix": "fix",
                "author": "matty",
                "date": "2026-05-04T10:55:00Z",
            },
        ],
        "brain_decisions": [],
        "audit_resolved": [],
        "recent_posts": [],
        "cost_summary": {"total_usd": 0.0, "total_inferences": 0, "by_model": []},
        "operator_notes": [],
    }


# ---------------------------------------------------------------------------
# _format_bundle_for_narrative — pure unit test (no LLM, no DB).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatBundleForNarrative:
    def test_includes_pr_number_title_url_and_body(self):
        text = _format_bundle_for_narrative(_bundle_repro_pr_221())
        # The PR number must appear so the LLM can cite [PR #221].
        assert "PR #221" in text
        # The full PR title (verbatim) must appear so the LLM can quote it.
        assert "fix(cli): rank-batch sys#N markers + auto-load POINDEXTER_SECRET_KEY" in text
        # The URL must appear so the LLM has a real link to use inline.
        assert "https://github.com/Glad-Labs/poindexter/pull/221" in text
        # Substantive body content must survive the Claude-Code-footer strip.
        assert "operator-friction" in text or "rank-batch" in text
        assert "INPUT to a CLI flag" in text

    def test_strips_claude_code_auto_footer_but_keeps_real_body(self):
        text = _format_bundle_for_narrative(_bundle_repro_pr_221())
        # Footer marker must NOT appear (avoids the model echoing it).
        assert "🤖 Generated with" not in text
        # But content from BEFORE the footer marker must survive.
        assert "marker resolver" in text

    def test_handles_empty_pr_list_without_crashing(self):
        text = _format_bundle_for_narrative({
            "date": "2026-05-04",
            "merged_prs": [],
            "notable_commits": [],
        })
        assert "DATE: 2026-05-04" in text
        # No PR section when there are no PRs.
        assert "MERGED PRs" not in text


# ---------------------------------------------------------------------------
# run() — verifies the bundle data reaches the LLM prompt (NOT the topic).
# ---------------------------------------------------------------------------


class _CaptureSiteConfig:
    """Minimal SiteConfig stand-in for narrate_bundle's late imports."""
    def get(self, key, default=None):
        if key == "pipeline_writer_model":
            return "glm-4.7-5090:latest"
        if key == "local_llm_api_url":
            return "http://localhost:11434"
        return default

    def get_float(self, key, default=None):
        return float(default) if default is not None else 120.0

    def get_bool(self, key, default=None):
        return bool(default) if default is not None else False

    def get_int(self, key, default=None):
        return int(default) if default is not None else 0


@pytest.mark.unit
@pytest.mark.asyncio
class TestRunPromptConstruction:
    """Regression guard for #354: the LLM prompt must contain the
    bundle's PR data, NOT just the topic string."""

    async def test_prompt_contains_pr_title_from_bundle(self):
        """The exact #354 acceptance criterion — spy on the writer
        prompt and assert the bundle's PR title appears in it."""
        bundle = _bundle_repro_pr_221()
        captured: list[str] = []

        async def _capture_chat(prompt, model, *, site_config=None):
            captured.append(prompt)
            return "Stub LLM output."

        with (
            patch("services.atoms.narrate_bundle._ollama_chat_text", _capture_chat),
            # site_config now flows via state (glad-labs-stack#330 DI seam);
            # patching the deleted singleton import is a no-op but kept
            # to avoid editing every test method's with-block.
        ):
            # Pass the bundle directly via state — no DB read needed.
            # The topic string deliberately differs from the PR title to
            # prove the prompt is built from the BUNDLE, not the topic.
            await run({
                "task_id": "354-repro",
                "topic": "Daily dev diary — 2026-05-04: a misleading topic string",
                "context_bundle": bundle,
                "site_config": _CaptureSiteConfig(),
            })

        assert len(captured) == 1, "narrate_bundle must call the LLM exactly once"
        prompt = captured[0]

        # The bundle's PR title must appear verbatim in the prompt.
        assert (
            "fix(cli): rank-batch sys#N markers + auto-load POINDEXTER_SECRET_KEY"
            in prompt
        ), "PR title from bundle must reach the LLM prompt — see #354"

        # The PR number must appear so the model can cite [PR #221].
        assert "PR #221" in prompt
        # The PR URL must appear so the model can build inline links.
        assert "https://github.com/Glad-Labs/poindexter/pull/221" in prompt
        # Substantive body content must reach the LLM (not just the title).
        assert "INPUT to a CLI flag" in prompt

    async def test_prompt_explicitly_subordinates_topic_to_bundle(self):
        """The user-message portion of the prompt must explicitly tell
        the writer the BUNDLE wins over any topic string. This is the
        active anti-hallucination directive that closes #354."""
        bundle = _bundle_repro_pr_221()
        captured: list[str] = []

        async def _capture_chat(prompt, model, *, site_config=None):
            captured.append(prompt)
            return "Stub LLM output."

        with (
            patch("services.atoms.narrate_bundle._ollama_chat_text", _capture_chat),
            # site_config now flows via state (glad-labs-stack#330 DI seam);
            # patching the deleted singleton import is a no-op but kept
            # to avoid editing every test method's with-block.
        ):
            await run({
                "task_id": "354-repro",
                "topic": "any topic",
                "context_bundle": bundle,
                "site_config": _CaptureSiteConfig(),
            })

        prompt = captured[0]
        # The prompt must surface the "BUNDLE is the only source of
        # truth" instruction inline with the bundle block (not just
        # buried in a 5K-token system preamble).
        assert "only source of truth" in prompt.lower()
        # The prompt must explicitly tell the model to OPEN with a PR
        # from the bundle — positive directive per
        # feedback_positive_directives.
        assert "open by referencing" in prompt.lower()

    async def test_topic_string_does_not_leak_into_prompt_as_anchor(self):
        """The topic string must not be the prompt's anchor.

        Concretely: the topic-only fragment ('a misleading topic
        string') we inject as the task topic must NOT appear as the
        primary directive to the writer. The bundle PR title is what
        the LLM should anchor on. We verify by passing a topic that's
        completely unrelated to the bundle and asserting that the
        bundle's content (not the topic) is what the writer receives
        as its grounding signal.
        """
        bundle = _bundle_repro_pr_221()
        captured: list[str] = []

        async def _capture_chat(prompt, model, *, site_config=None):
            captured.append(prompt)
            return "Stub LLM output."

        misleading_topic = "totally unrelated string about widgets"
        with (
            patch("services.atoms.narrate_bundle._ollama_chat_text", _capture_chat),
            # site_config now flows via state (glad-labs-stack#330 DI seam);
            # patching the deleted singleton import is a no-op but kept
            # to avoid editing every test method's with-block.
        ):
            await run({
                "task_id": "354-repro",
                "topic": misleading_topic,
                "context_bundle": bundle,
                "site_config": _CaptureSiteConfig(),
            })

        prompt = captured[0]
        # The misleading topic must NOT appear in the prompt — narrate_
        # bundle deliberately drops the topic and keys off the bundle.
        assert misleading_topic not in prompt, (
            "topic string leaked into the writer prompt — narrate_bundle "
            "must construct the prompt from the BUNDLE only, not the topic"
        )

    async def test_quiet_day_short_circuits_without_calling_llm(self):
        """When the bundle is empty, no LLM call — no risk of riff."""
        captured: list[str] = []

        async def _capture_chat(prompt, model):
            captured.append(prompt)
            return "Stub."

        with (
            patch("services.atoms.narrate_bundle._ollama_chat_text", _capture_chat),
            # site_config now flows via state (glad-labs-stack#330 DI seam);
            # patching the deleted singleton import is a no-op but kept
            # to avoid editing every test method's with-block.
        ):
            result = await run({
                "task_id": "quiet",
                "topic": "any",
                "context_bundle": {
                    "date": "2026-05-04",
                    "merged_prs": [],
                    "notable_commits": [],
                },
            })

        assert captured == [], "no LLM call on a quiet day"
        assert "Quiet day" in result["content"]
        assert result["model_used"] == "none"
