"""QA-gate tests for the leaked-planning-scaffold rule (#1968).

Safety net for the 2026-06-28 incident: the writer leaked its planning
outline + echoed prompt instructions into the body. normalize_draft strips
the common (heading-anchored) case; this content_validator rule hard-rejects
any residual scaffold so it can't reach awaiting_approval (quality 82 on the
original) and trigger the QA rescue/rewrite cycle.
"""

from modules.content.content_validator import validate_content
from services.site_config import SiteConfig
from services.validator_config import reset_cache, seed_cache_for_tests

_SC = SiteConfig()


def _scaffold_issues(result):
    return [i for i in result.issues if i.category == "leaked_planning_scaffold"]


class TestLeakedPlanningScaffold:
    def test_flags_leaked_scaffold_as_critical(self):
        content = (
            "* Key elements from sources:\n"
            "* Models used/tested: gemma-4-31B.\n"
            "* Vary sentence length.\n\n"
            "## Real Section\n\nThe article body goes here with real prose.\n"
        )
        result = validate_content("Title", content, "topic", site_config=_SC)
        issues = _scaffold_issues(result)
        assert issues, "expected a leaked_planning_scaffold issue"
        assert issues[0].severity == "critical"
        assert result.passed is False

    def test_clean_content_no_scaffold_flag(self):
        content = (
            "FastAPI is a modern Python web framework.\n\n"
            "## Why It Is Fast\n\n"
            "It builds on Starlette and Pydantic for async performance.\n"
        )
        result = validate_content("FastAPI", content, "FastAPI", site_config=_SC)
        assert _scaffold_issues(result) == []

    def test_single_tell_does_not_fire(self):
        # A single benign mention is not a scaffold — needs >= 2 tells.
        content = (
            "## Writing Tips\n\n"
            "One trick: vary sentence length so prose does not read flat.\n"
        )
        result = validate_content("Tips", content, "writing", site_config=_SC)
        assert _scaffold_issues(result) == []

    def test_tells_inside_code_fence_ignored(self):
        # A post ABOUT the pipeline may show the scaffold rules as a code
        # example — fenced code is blanked before scanning, so it must not fire.
        content = (
            "## How We Constrain the Writer\n\n"
            "Our writer prompt ships these rules:\n\n"
            "```\n"
            "* Key elements from sources:\n"
            "* Vary sentence length.\n"
            "* No placeholder brackets.\n"
            "```\n\n"
            "The model usually respects them.\n"
        )
        result = validate_content("Prompt design", content, "llm", site_config=_SC)
        assert _scaffold_issues(result) == []

    def test_rule_can_be_disabled_via_db(self):
        content = (
            "* Key elements from sources:\n"
            "* Vary sentence length.\n\n"
            "## Section\n\nBody.\n"
        )
        try:
            seed_cache_for_tests({"leaked_planning_scaffold": {"enabled": False}})
            result = validate_content("T", content, "t", site_config=_SC)
            assert _scaffold_issues(result) == []
        finally:
            reset_cache()


class TestRevisionBriefingLeak:
    """qa.rewrite briefing echo (prod task 342a26b7, 2026-07-23): the rescue
    reviser restated its revision instructions before the article and the
    residue reached awaiting_approval at quality 76. These tells make the
    programmatic gate hard-reject any residue the qa.rewrite strip misses."""

    def test_flags_revision_briefing_as_critical(self):
        content = (
            "*   Task: Revise a draft article based on specific fixes.\n"
            "    *   Constraints: Preserve structure, headings, length.\n"
            "    *   Fix 1: Unlinked citation — rephrase.\n"
            "    *   Fix 2: Terminology contradiction.\n\n"
            "## Real Section\n\nThe article body goes here with real prose.\n"
        )
        result = validate_content("Title", content, "topic", site_config=_SC)
        issues = _scaffold_issues(result)
        assert issues, "expected a leaked_planning_scaffold issue"
        assert issues[0].severity == "critical"

    def test_single_benign_task_bullet_does_not_fire(self):
        content = (
            "## How We Plan Sprints\n\n"
            "Every card carries one line:\n\n"
            "- Task: ship the exporter swap\n\n"
            "That single label keeps standups honest, and the rest of the "
            "board stays prose-first.\n"
        )
        result = validate_content("Title", content, "topic", site_config=_SC)
        assert not _scaffold_issues(result)

    def test_flags_label_free_briefing_dialect_as_critical(self):
        # ece2f516 dialect (2026-07-24, poindexter#897): bare "Revise a
        # draft article about…" opener, unlabeled constraint bullets, and
        # first-person deliberation — no "Task:"/"Constraints:"/"Fix N:"
        # labels at all. Residual net for any echo the strip misses.
        content = (
            'Revise a draft article about "Postgres survival for startups."\n'
            "\n"
            "        *   Preserve structure, headings, length, links, "
            "citations, and voice.\n"
            "        *   No new sections/removals unless required by fixes.\n"
            "    *   *Wait*, the fix list also asks for a tighter closing.\n"
            "    *   Verify Markdown structure.\n\n"
            "## The startup's Postgres survival guide\n\n"
            "Most seed-stage teams meet their first real outage inside "
            "Postgres.\n"
        )
        result = validate_content("Title", content, "topic", site_config=_SC)
        issues = _scaffold_issues(result)
        assert issues, "expected a leaked_planning_scaffold issue"
        assert issues[0].severity == "critical"
        assert result.passed is False

    def test_single_revision_mention_in_prose_does_not_fire(self):
        # "revise the draft" is one tell — legitimate in a post about
        # editing workflow, so it must stay below the >=2 bar.
        content = (
            "## The Two-Pass Rule\n\n"
            "Our editors revise the draft twice before it ships: first "
            "pass for facts, second pass for voice.\n"
        )
        result = validate_content("Title", content, "topic", site_config=_SC)
        assert not _scaffold_issues(result)


class TestScaffoldTellSync:
    def test_scaffold_tell_regexes_stay_in_sync(self):
        # The strip (atoms/_scaffold_helpers) and this gate are twins by
        # contract ("strip there, detect here") — a tell added to one and
        # not the other reproduces the ece2f516 class of miss, so the
        # patterns must stay byte-identical.
        from modules.content.atoms._scaffold_helpers import SCAFFOLD_TELL_RE
        from modules.content.content_validator import (
            LEAKED_PLANNING_SCAFFOLD_RE,
        )

        assert SCAFFOLD_TELL_RE.pattern == LEAKED_PLANNING_SCAFFOLD_RE.pattern


# ---------------------------------------------------------------------------
# Revision-brief / deliberation dialect (Glad-Labs/poindexter#1000)
# ---------------------------------------------------------------------------
#
# The writer_self_review contradiction-revise pass leaked its own brief +
# reasoning as the draft SIX times between 2026-07-04 and 2026-08-07 without
# planning_dump ever firing: the bullet structure screamed (41/50 opening
# lines) but NONE of the 11 vocabulary families matched the "Role: / Input: /
# Task: / Output Format:" label set. Task 1bdf0360 reached awaiting_approval
# at quality 94 on 2026-08-07.


def _dump_issues(result):
    return [i for i in result.issues if i.category == "planning_dump"]


class TestRevisionBriefDialect:
    _ARTICLE = (
        "## Money as a Byproduct\n\n"
        "You've seen the graph. Somebody posts their MRR chart six months "
        "after launch and the caption is always some version of just build "
        "something people want.\n"
    )

    _BRIEF_DUMP = (
        "*   Role: Reviewer checking for internal contradictions.\n"
        '    *   Input: A draft titled "Why Revenue is Exhaust".\n'
        "    *   Task: Fix specific contradictions and *nothing else*.\n"
        "    *   Output Format: Revised draft only. Preserve image markers.\n"
        "    *   Constraint 1: Fix *only* the identified contradictions.\n"
        "    *   Constraint 2: Output only the revised draft.\n"
        "    *   Section 1: MRR charts hide the struggle.\n"
        "    *   Section 2: Money is a byproduct of excellence.\n\n"
    )

    def test_revision_brief_bullet_dump_flagged(self):
        result = validate_content(
            "Why Revenue is Exhaust", self._BRIEF_DUMP + self._ARTICLE,
            "money", site_config=_SC,
        )
        issues = _dump_issues(result)
        assert issues, "revision-brief dump must be a planning_dump issue"
        assert issues[0].severity == "critical"
        assert result.passed is False

    def test_deliberation_prose_stream_flagged(self):
        """Path B: the reasoning leaked as indented PROSE, so the bullet
        share collapses (prod task f7df9674 sat at 0.22) — the
        deliberation-voice tells carry it instead."""
        stream = (
            "*   Task: Fix specific contradictions in a draft.\n"
            "    Wait, the prompt says fix contradictions and nothing else.\n"
            "    Let's re-read carefully: the analysis concludes with PASS.\n"
            "    The user's own analysis says these are not contradictions.\n"
            "    If I change things already vetted as PASS, am I overstepping?\n"
            "    Section 1 says check the logs before you touch anything.\n"
            "    Section 4 says not a question that sends you crawling.\n"
            "    These are complementary, not contradictory.\n"
            "    Actually, looking at the structure of the prompt again.\n"
            "    The prompt is slightly paradoxical about image markers.\n"
            "    If the user concludes PASS, there is nothing to fix.\n"
            "    Is there any other contradiction? I don't think so.\n"
            "    I'll provide the original text.\n\n"
        )
        result = validate_content(
            "A Title", stream + self._ARTICLE, "topic", site_config=_SC,
        )
        assert _dump_issues(result), "deliberation stream must be flagged"

    def test_clean_article_not_flagged(self):
        result = validate_content(
            "Money as a Byproduct", self._ARTICLE, "money", site_config=_SC,
        )
        assert _dump_issues(result) == []

    def test_api_doc_bullets_alone_stay_silent(self):
        """FP guard: a technical post that legitimately bullets Input:/
        Output Format: hits ONE family and must stay below the >=2 bar."""
        content = (
            "- Input: a UTF-8 string of arbitrary length\n"
            "- Output Format: JSON with a `result` key\n"
            "- Input: an optional locale tag\n"
            "- Output Format: the same shape, localized\n"
            "- Input: a timeout in milliseconds\n"
            "- Output Format: 504 on expiry\n\n"
            "## How the endpoint behaves\n\n"
            "The service validates each field before dispatching the call.\n"
        )
        result = validate_content(
            "API Reference", content, "api", site_config=_SC,
        )
        assert _dump_issues(result) == []

    def test_single_rhetorical_wait_stays_silent(self):
        """FP guard: one 'Wait,' in a real intro is a rhetorical device, not
        a deliberation stream — Path B needs >=3 distinct tells."""
        content = (
            "Wait, you might say -- isn't this just premature optimization?\n"
            "It is a fair objection and worth answering directly before we\n"
            "go any further into the mechanics of the build pipeline here.\n\n"
            "## The objection\n\nIt holds up better than you would expect.\n"
        )
        result = validate_content(
            "On Optimization", content, "perf", site_config=_SC,
        )
        assert _dump_issues(result) == []
