"""Validator-bypass test for the dev_diary niche.

Per Matt's voice-policy update on 2026-05-02, the body-side
``first_person_claims`` validator in ``services/quality_scorers.py``
must NOT penalise legitimate first-person content in the right niche.
The bypass is gated on the ``qa_allow_first_person_niches`` app_setting
(CSV of niche slugs) so future niches can opt in without code changes.

This file's tests directly seed ``site_config`` to control the bypass
flag — the scoring functions read site_config at call time.
"""

from __future__ import annotations

import pytest

from services.quality_scorers import score_accuracy
from services.site_config import site_config


_FIRST_PERSON_CONTENT = (
    "We shipped the per-medium gate engine today. We built the queue. "
    "I wrote the niche seed. We launched the dev diary. We released "
    "the topic source. I created the validator-bypass flag. We made "
    "the dispatcher swap. I developed the cron schedule."
)

_CFG = {
    "accuracy_baseline": 7.0,
    "accuracy_good_link_bonus": 0.3, "accuracy_good_link_max": 1.0,
    "accuracy_bad_link_penalty": 0.5, "accuracy_bad_link_max": 2.0,
    "accuracy_citation_bonus": 0.3,
    "accuracy_first_person_penalty": 1.0, "accuracy_first_person_max": 3.0,
    "accuracy_meta_commentary_penalty": 0.5, "accuracy_meta_commentary_max": 2.0,
}


@pytest.fixture(autouse=True)
def _seed_allow_list():
    """Seed the bypass list before each test, restore after.

    The conftest's `_reset_singletons_between_tests` autouse fixture
    will scrub site_config at test teardown; this fixture prepends
    a known value so each test starts from a clean known state.
    """
    site_config._config["qa_allow_first_person_niches"] = "dev_diary"
    yield
    site_config._config.pop("qa_allow_first_person_niches", None)


@pytest.mark.unit
class TestFirstPersonValidatorBypass:
    def test_default_niche_still_penalises_first_person(self):
        """Sanity: posts with no niche / non-allowlisted niche still get hit."""
        score = score_accuracy(
            _FIRST_PERSON_CONTENT,
            context={"niche": "ai_ml"},  # not in the allow list
            cfg=_CFG,
        )
        # baseline 7.0 minus first-person penalty (capped at 3.0)
        assert score <= 5.0, f"Non-allowlisted niche should be penalised; got {score}"

    def test_no_niche_in_context_still_penalises(self):
        """Posts that don't carry a niche in the context still get hit."""
        score = score_accuracy(
            _FIRST_PERSON_CONTENT,
            context={},
            cfg=_CFG,
        )
        assert score <= 5.0, f"Empty-context post should be penalised; got {score}"

    def test_dev_diary_niche_bypasses_penalty(self):
        """The dev_diary niche skips the first-person penalty entirely."""
        score = score_accuracy(
            _FIRST_PERSON_CONTENT,
            context={"niche": "dev_diary"},
            cfg=_CFG,
        )
        # No penalty applied → score should land near baseline 7.0
        assert score >= 6.5, (
            f"dev_diary should bypass first-person penalty; got {score}"
        )

    def test_dev_diary_via_category_field_also_bypasses(self):
        """Some pipeline code stuffs the niche slug into ``category``
        rather than ``niche``. Both paths should trigger the bypass."""
        score = score_accuracy(
            _FIRST_PERSON_CONTENT,
            context={"category": "dev_diary"},
            cfg=_CFG,
        )
        assert score >= 6.5, f"category-based niche should bypass; got {score}"

    def test_bypass_is_case_insensitive(self):
        """Slug comparison is case-insensitive — ``DEV_DIARY`` works too."""
        score = score_accuracy(
            _FIRST_PERSON_CONTENT,
            context={"niche": "DEV_DIARY"},
            cfg=_CFG,
        )
        assert score >= 6.5, f"case-insensitive bypass should fire; got {score}"

    def test_unrelated_niche_in_allow_list_does_not_help_dev_diary_post(self):
        """If only ``other_niche`` is in the allow list, dev_diary posts
        are still penalised."""
        site_config._config["qa_allow_first_person_niches"] = "other_niche"
        score = score_accuracy(
            _FIRST_PERSON_CONTENT,
            context={"niche": "dev_diary"},
            cfg=_CFG,
        )
        assert score <= 5.0, (
            f"non-listed niche should still be penalised; got {score}"
        )

    def test_empty_allow_list_falls_back_to_strict(self):
        """When the allow list is empty/unset, all niches get the strict
        rule — preserves backward compatibility for operators who haven't
        run migration 0134 yet."""
        site_config._config["qa_allow_first_person_niches"] = ""
        score = score_accuracy(
            _FIRST_PERSON_CONTENT,
            context={"niche": "dev_diary"},
            cfg=_CFG,
        )
        assert score <= 5.0, (
            f"empty allow list = strict rule for everyone; got {score}"
        )
