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
