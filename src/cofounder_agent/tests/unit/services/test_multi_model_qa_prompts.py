"""Snapshot tests pinning the multi_model_qa YAML prompts.

These tests are the public contract for the three QA prompts that were
migrated out of inline Python constants into ``prompts/content_qa.yaml``
during Lane A batch 1 of the OSS migration. Any future Langfuse edit
that drifts the YAML default (or any in-tree YAML edit) will trip these
snapshots and force a deliberate update.

The match is byte-for-byte intentionally — whitespace, double-brace
escaping, and trailing newlines are all part of the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.prompt_manager import (
    PromptCategory,
    PromptResolution,
    UnifiedPromptManager,
)


@pytest.fixture
def pm() -> UnifiedPromptManager:
    """Fresh UnifiedPromptManager — YAML-only, no Langfuse, no DB."""
    return UnifiedPromptManager()


# ---------------------------------------------------------------------------
# Snapshot bodies
#
# These strings are the production prompt text as it lived in
# ``services/multi_model_qa.py`` immediately before the YAML migration.
# Keeping the snapshots inline (rather than reading from a frozen file)
# means a reviewer can read both halves of the contract in one place.
# ---------------------------------------------------------------------------


_TOPIC_DELIVERY_EXPECTED = """You are a strict editor checking whether an article
delivers on its topic. A reader clicking this article expects what the topic
promises. Did the writer deliver?

REQUESTED TOPIC: Test topic

ARTICLE OPENING (first ~1000 words):
Test opening text

Check these specific failure modes:

  1. Numeric promises. If the topic says "10 X" or "11 Y" or "5 Z", does the
     body actually list that many? Partial lists (two items then a pivot to
     generalities) FAIL.
  2. Named entities. If the topic names a specific product, person, or
     technology ("Llama 4", "Claude", "indie hackers making $1M+"), does the
     body actually discuss that specific thing? An article titled "Llama 4"
     that only discusses Llama 3.1 FAILS.
  3. Format promise. If the topic implies a guide, tutorial, list, or review,
     does the body deliver that format? A "guide" that's actually an opinion
     piece FAILS.
  4. Angle/thesis. Is the article's thesis actually about the topic, or did
     the writer pivot to a tangential point they preferred?

Respond with ONLY valid JSON:
{"delivers": true/false, "score": NUMBER 0-100, "reason": "concise — name the specific gap when one exists"}

Scoring guidance: delivers=true and score 85-100 if the body is a faithful
execution of the topic. delivers=false and score 0-40 if the body is a
bait-and-switch or numeric underdelivery or misnamed version. delivers=true
and score 60-80 if the body is mostly on-topic but weaker than the topic
implies.
"""


_CONSISTENCY_EXPECTED = """You are a strict editor checking an article for
internal contradictions. Readers lose trust when section 1 says X and
section 3 says not-X, even if both are defensible on their own.

ARTICLE (full text):
Test article body. Section A says do X. Section B says do not X.

Read the entire article and look for:

  1. Recommendation contradictions. Does one section recommend tool/approach
     A and another section recommend incompatible tool/approach B without
     acknowledging the switch? ("Don't use React" followed by "use Next.js"
     is a contradiction; Next.js is React.)
  2. Factual contradictions. Does one section state a number, version, or
     claim that another section directly contradicts?
  3. Principle contradictions. Does the article lay out a principle in one
     section ("never build custom auth") and then show code that violates it
     in another section?
  4. Code vs prose contradictions. Does the prose claim the code does X when
     the code actually does Y? (e.g. "the code validates the referrer" when
     the code just inserts without validating.)

Respond with ONLY valid JSON:
{"consistent": true/false, "score": NUMBER 0-100, "contradictions": ["list","of","specific","pairs"]}

Scoring guidance: consistent=true and score 85-100 if no contradictions.
consistent=false and score 0-50 if one or more contradictions found.
Be specific in the contradictions list — name the sections and the conflict.
"""


_QA_REVIEW_EXPECTED = """Review this blog post for publication readiness. Be critical but fair.

TODAY'S DATE: 2026-05-09

TITLE: Test Title
TOPIC: Test Topic

---SOURCES (research corpus the writer consulted)---
Source A: example.com — relevant excerpt.
---END SOURCES---

---CONTENT---
Test content body.
---END---

Evaluate:
1. Is the content factually accurate? Flag any claims that seem fabricated.
2. Is the writing clear, engaging, and well-structured?
3. Are there any hallucinated people, statistics, or quotes?
4. Would this be valuable to the target audience (developers and founders)?
5. Is this FINISHED ARTICLE PROSE from the first line to the last? Planning
   notes, outlines, bullet-point drafting scaffolds, echoed writing
   instructions, or notes-to-self ("I should add...", "Check word count")
   are never publishable — whether they make up the whole text or only
   open it before the article begins.

UNFINISHED CONTENT IS AN AUTOMATIC REJECT:

When any part of the CONTENT is a plan, outline, instruction echo, or
drafting scaffold rather than finished prose, set approved=false and cap
quality_score at 25. Score only the text actually on the page — never
the article the plan describes or the title promises. A well-organized
outline is still a reject: readers must never see it.

GROUND YOUR REVIEW IN THE CONTENT:

Quote a short phrase from the CONTENT for anything you praise or
criticize. If you cannot point to text that supports a judgment, do not
make that judgment.

HANDLING CLAIMS YOU DO NOT RECOGNIZE:

Your training data has a cutoff. The article may cover hardware,
software, or events released after your cutoff but before today's
date above. Apply this rubric to claims about products, versions,
frameworks, or events you do not personally recognize:

  - Treat "I have not heard of this" as "outside my knowledge",
    distinct from "this is fabricated". A name being unfamiliar
    is signal but not proof.
  - Reject as fabricated when claims are internally contradictory,
    suspiciously specific (fake-looking statistics, quotes attributed
    to real people, made-up studies with impossible citations), or
    physically/logically impossible.
  - Mark as "uncertain — cannot verify" when the claim is plausible
    for today's date but outside your knowledge, and lower the score
    modestly for that unverifiable specificity.
  - Reject outright the universal failure modes regardless of date:
    fabricated people, fake statistics, invented quotes.
  - Accept claims that fall outside your knowledge but match common
    industry patterns (a startup with a real-looking product page, a
    library with a plausible API, a metric within typical ranges).

USING THE SOURCES SECTION (when present above):

The SOURCES block contains the research corpus the writer consulted
while drafting this post — real links, pulled excerpts, internal
reference material. Treat it as authoritative ground truth for this
specific article. For each factual claim:

  - When the claim appears in or is supported by the SOURCES, accept
    it as grounded. This holds even when the claim falls outside
    your training knowledge.
  - When the claim is absent from SOURCES and outside your knowledge,
    flag it as "unverified — not backed by provided research" and
    lower the score modestly. Reject only when the claim is
    additionally implausible.
  - When the claim contradicts the SOURCES, reject it.
  - Common knowledge ("HTTP uses status codes", "Postgres supports
    JSONB") passes without needing a SOURCES entry.

When the SOURCES block is absent, evaluate from your training
knowledge using the cutoff rubric above.

Output one JSON object. The first character is `{` and the last
character is `}`:
{"approved": true/false, "quality_score": NUMBER 0-100, "feedback": "concise — name what's strong and what needs revision"}
"""


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultiModelQaPromptSnapshots:
    def test_topic_delivery_snapshot(self, pm: UnifiedPromptManager):
        actual = pm.get_prompt(
            "qa.topic_delivery",
            topic="Test topic",
            opening="Test opening text",
        )
        assert actual == _TOPIC_DELIVERY_EXPECTED

    def test_consistency_snapshot(self, pm: UnifiedPromptManager):
        actual = pm.get_prompt(
            "qa.consistency",
            content="Test article body. Section A says do X. Section B says do not X.",
        )
        assert actual == _CONSISTENCY_EXPECTED

    def test_review_snapshot(self, pm: UnifiedPromptManager):
        sources_block = (
            "---SOURCES (research corpus the writer consulted)---\n"
            "Source A: example.com — relevant excerpt.\n"
            "---END SOURCES---\n\n"
        )
        actual = pm.get_prompt(
            "qa.review",
            title="Test Title",
            topic="Test Topic",
            content="Test content body.",
            current_date="2026-05-09",
            sources_block=sources_block,
        )
        assert actual == _QA_REVIEW_EXPECTED

    def test_review_handles_empty_sources_block(self, pm: UnifiedPromptManager):
        """The production call site passes ``sources_block=""`` when no
        research corpus is available. The rendered prompt must still be
        valid (no orphaned heading, no format error).
        """
        rendered = pm.get_prompt(
            "qa.review",
            title="T",
            topic="X",
            content="Body.",
            current_date="2026-01-01",
            sources_block="",
        )
        assert "---SOURCES" not in rendered
        assert "---CONTENT---\nBody.\n---END---" in rendered

    def test_review_uses_today_when_called_in_production_shape(
        self, pm: UnifiedPromptManager,
    ):
        """Smoke test mirroring the real call site — confirms ``current_date``
        accepts the strftime output the production caller produces.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rendered = pm.get_prompt(
            "qa.review",
            title="T", topic="X", content="C",
            current_date=today, sources_block="",
        )
        assert f"TODAY'S DATE: {today}" in rendered

    def test_review_contains_unfinished_content_veto(
        self, pm: UnifiedPromptManager,
    ):
        """The critic prompt must carry the finished-prose veto criterion.

        2026-07-01 incident (tasks e46b449c / 9921678f / ecaf0c01): drafts
        whose bodies were the writer's planning/outline dump scored 82-85
        and reached awaiting_approval — the critic's four criteria all
        addressed factual accuracy/clarity/audience, so a well-organized
        outline failed none of them, and the judge anchored its review on
        the TITLE instead of the text on the page. Guards the veto text
        against a future prompt edit silently dropping it (the snapshot
        test would flag the diff, but this states the intent).
        """
        rendered = pm.get_prompt(
            "qa.review",
            title="T", topic="X", content="C",
            current_date="2026-01-01", sources_block="",
        )
        assert "UNFINISHED CONTENT IS AN AUTOMATIC REJECT" in rendered
        assert "FINISHED ARTICLE PROSE" in rendered
        assert "GROUND YOUR REVIEW IN THE CONTENT" in rendered


# ---------------------------------------------------------------------------
# Edge cases + error paths on the UnifiedPromptManager accessors that back
# these QA prompts. The snapshots above pin the happy-path render; the tests
# below pin the failure modes (missing variable / unknown key), the
# provenance/version metadata the lab stamps on outcome rows, the literal-
# JSON-brace escaping contract these prompts depend on, and the metadata /
# listing accessors. None of these are exercised by the migration tests,
# which only cover the downstream resolver wrappers.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultiModelQaPromptEdgeCases:
    def test_missing_required_variable_raises_helpful_keyerror(
        self, pm: UnifiedPromptManager,
    ):
        """qa.review needs ``content``; omitting it must surface the
        remediation-flavoured KeyError from get_prompt_resolution, not a
        bare ``KeyError('content')`` from str.format."""
        with pytest.raises(KeyError) as excinfo:
            pm.get_prompt(
                "qa.review",
                title="T",
                topic="X",
                current_date="2026-01-01",
                sources_block="",
                # content deliberately omitted
            )
        message = str(excinfo.value)
        assert "qa.review" in message
        assert "content" in message
        assert "Please provide" in message

    def test_unknown_qa_key_raises_keyerror_listing_available(
        self, pm: UnifiedPromptManager,
    ):
        """A typo'd key must fail loud and list the registered keys so the
        caller can self-correct — not return an empty / default string."""
        with pytest.raises(KeyError) as excinfo:
            pm.get_prompt("qa.does_not_exist")
        message = str(excinfo.value)
        assert "qa.does_not_exist" in message
        assert "Available:" in message
        # A real registered key should appear in the available list.
        assert "qa.review" in message

    def test_resolution_reports_yaml_source_and_int_version(
        self, pm: UnifiedPromptManager,
    ):
        """With no Langfuse wired, qa.review resolves from YAML. The
        provenance record must say so and coerce the ``v1.1`` YAML version
        string to the major int (1) the outcome rows store."""
        resolution = pm.get_prompt_resolution(
            "qa.review",
            title="T",
            topic="X",
            content="Body.",
            current_date="2026-01-01",
            sources_block="",
        )
        assert isinstance(resolution, PromptResolution)
        assert resolution.key == "qa.review"
        assert resolution.source == "yaml"
        assert resolution.version == 1
        assert resolution.text.startswith("Review this blog post")

    def test_literal_json_braces_survive_formatting(
        self, pm: UnifiedPromptManager,
    ):
        """These QA prompts embed literal JSON examples (``{"delivers": ...}``)
        which only render because the YAML escapes them as ``{{...}}``. Calling
        with ONLY the documented placeholders must not raise KeyError on those
        braces and must emit single, unescaped braces in the output."""
        rendered = pm.get_prompt(
            "qa.topic_delivery",
            topic="Quantum gardening",
            opening="Some opening text",
        )
        # The literal JSON example is emitted verbatim, single-braced.
        assert '{"delivers": true/false' in rendered
        # And the escaped doubled braces never leak through to the output.
        assert "{{" not in rendered
        assert "}}" not in rendered

    def test_extra_kwargs_are_ignored(self, pm: UnifiedPromptManager):
        """str.format tolerates surplus keyword args. Callers that pass a
        superset of placeholders (e.g. a shared context dict) must not trip
        an error — the unused keys are simply dropped."""
        rendered = pm.get_prompt(
            "qa.consistency",
            content="Section A says X. Section B says not X.",
            unused_key="ignored",
            another_unused=42,
        )
        assert "Section A says X" in rendered

    @pytest.mark.parametrize(
        "key", ["qa.topic_delivery", "qa.consistency", "qa.review"],
    )
    def test_qa_prompt_metadata_is_content_qa(
        self, pm: UnifiedPromptManager, key: str,
    ):
        """get_metadata for each migrated QA prompt reports the CONTENT_QA
        category — the seam list_prompts(category=...) filtering relies on."""
        meta = pm.get_metadata(key)
        assert meta.category == PromptCategory.CONTENT_QA

    def test_get_metadata_unknown_key_raises(self, pm: UnifiedPromptManager):
        """Metadata lookups for an unregistered key fail loud rather than
        returning a placeholder PromptMetadata."""
        with pytest.raises(KeyError):
            pm.get_metadata("qa.not_a_real_prompt")

    def test_list_prompts_filtered_by_content_qa_includes_qa_review(
        self, pm: UnifiedPromptManager,
    ):
        """Category filtering returns only CONTENT_QA prompts, and the
        per-key payload carries the category string + version for the UI."""
        qa_prompts = pm.list_prompts(category=PromptCategory.CONTENT_QA)
        assert "qa.review" in qa_prompts
        assert qa_prompts["qa.review"]["category"] == "content_qa"
        assert qa_prompts["qa.review"]["version"] == "v1.1"
        # The filter must exclude prompts from other categories.
        all_prompts = pm.list_prompts()
        assert len(qa_prompts) < len(all_prompts)
        assert all(
            payload["category"] == "content_qa"
            for payload in qa_prompts.values()
        )
