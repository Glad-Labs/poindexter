"""Tests for the leaked-planning-scaffold strip in content.normalize_draft.

Regression for the 2026-06-28 incident: the writer model (gemma-4-31B)
emitted its planning/outline scaffold — bulleted meta-notes plus echoed
prompt instructions — as a preamble BEFORE the article, and the article's
first H2 was glued mid-line onto the last scaffold bullet. The existing
``strip_reasoning_artifacts`` defense only removes control-token reasoning
(``<think>`` / ``<|channel|>``), so a plain-Markdown planning scaffold sailed
through to ``awaiting_approval`` (quality 82) and rendered as a wall of
bullets with the real article buried below.
"""

import asyncio

from modules.content.atoms.content_normalize_draft import (
    run,
    strip_leaked_planning_scaffold,
)

# Condensed reproduction of prod task 0f70f736's leaked body.
_LEAKED = (
    "*   Topic: Ollama model configuration (based on provided context).\n"
    "    *   Key elements from sources:\n"
    "        *   Models used/tested: gemma-4-31B (writer model winner).\n"
    "    *   *Voice:* First person grounded in context.\n"
    "    *   *Citations:* Use inline markdown links.\n"
    "    *   *Structure:* H2 headings, no H1, concluding paragraph.\n"
    "    *   Avoid \"delve\", \"tapestry\", etc.\n"
    "    *   Vary sentence length.\n"
    "    *   No placeholder brackets.## The Current Ollama Model Stack\n\n"
    "Optimizing a local LLM pipeline requires matching models to tasks.\n"
)


class TestStripLeakedPlanningScaffold:
    def test_strips_scaffold_and_keeps_article(self):
        out = strip_leaked_planning_scaffold(_LEAKED)
        # Article (un-glued first heading) survives, scaffold is gone.
        assert out.lstrip().startswith("## The Current Ollama Model Stack")
        assert "Optimizing a local LLM pipeline" in out
        assert "Vary sentence length" not in out
        assert "Key elements from sources" not in out
        assert "*Voice:*" not in out

    def test_idempotent(self):
        once = strip_leaked_planning_scaffold(_LEAKED)
        twice = strip_leaked_planning_scaffold(once)
        assert once == twice

    def test_preserves_normal_intro_paragraph(self):
        # A legit post opens with a prose hook before the first heading and
        # carries NO scaffold tells — it must pass through untouched.
        content = (
            "If you are running local LLMs, you know VRAM is the only currency "
            "that matters.\n\n"
            "## The 32GB Threshold\n\n"
            "The jump from 24GB to 32GB is not linear.\n"
        )
        assert strip_leaked_planning_scaffold(content) == content

    def test_preserves_legitimate_body_bullets(self):
        # Bullets that are real article content (no tells) must survive.
        content = (
            "## Setup steps\n\n"
            "Follow these:\n\n"
            "* Install the driver\n"
            "* Reboot the box\n"
            "* Verify with nvidia-smi\n"
        )
        assert strip_leaked_planning_scaffold(content) == content

    def test_single_label_is_not_enough(self):
        # One isolated "Sources:" bullet is not a scaffold — needs >=2 tells.
        content = (
            "Quick notes before the guide:\n\n"
            "* Sources: the official docs\n\n"
            "## Real Section\n\nThe actual article body goes here.\n"
        )
        assert strip_leaked_planning_scaffold(content) == content

    def test_empty_content(self):
        assert strip_leaked_planning_scaffold("") == ""

    def test_run_wires_the_strip(self):
        # The atom's run() must apply the strip end-to-end.
        result = asyncio.run(run({"content": _LEAKED, "title": "X"}))
        body = result["content"]
        assert "Vary sentence length" not in body
        assert "## The Current Ollama Model Stack" in body
        assert "Optimizing a local LLM pipeline" in body
