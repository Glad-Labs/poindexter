"""Unit tests for services/validator_config.py.

Validators CRUD V1 (migration 0135) helper. Covers the two public
lookups (``is_validator_enabled`` + ``get_validator_threshold``) plus
the legacy CSV bridge for ``first_person_claims``.

Tests use ``seed_cache_for_tests`` to pre-populate the rule cache
so we don't hit a real Postgres -- the helper is sync + cache-driven,
and the cache loader is the only side effect that needs DB access.
"""

from __future__ import annotations

import pytest

from services import validator_config as vc


@pytest.fixture(autouse=True)
def _reset_validator_cache():
    vc.reset_cache()
    yield
    vc.reset_cache()


# ---------------------------------------------------------------------------
# is_validator_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsValidatorEnabled:
    def test_unknown_rule_fails_open(self):
        """A rule with no DB row reports as enabled (preserves current behavior)."""
        assert vc.is_validator_enabled("never_seen_rule") is True

    def test_disabled_rule_returns_false(self):
        vc.seed_cache_for_tests({
            "first_person_claims": {"enabled": False},
        })
        assert vc.is_validator_enabled("first_person_claims") is False

    def test_enabled_rule_with_no_niche_scope_runs_for_any_niche(self):
        vc.seed_cache_for_tests({
            "fake_stat": {"enabled": True, "applies_to_niches": None},
        })
        assert vc.is_validator_enabled("fake_stat", niche="ai_ml") is True
        assert vc.is_validator_enabled("fake_stat", niche="dev_diary") is True
        assert vc.is_validator_enabled("fake_stat", niche=None) is True

    def test_niche_scoped_rule_only_runs_in_listed_niches(self):
        vc.seed_cache_for_tests({
            "code_block_density": {
                "enabled": True,
                "applies_to_niches": ["ai_ml", "gaming"],
            },
        })
        assert vc.is_validator_enabled("code_block_density", niche="ai_ml") is True
        assert vc.is_validator_enabled("code_block_density", niche="gaming") is True
        assert vc.is_validator_enabled("code_block_density", niche="dev_diary") is False

    def test_niche_scoped_rule_with_no_post_niche_returns_false(self):
        """A rule pinned to specific niches but called with niche=None
        treats it as out-of-scope rather than silently applying everywhere."""
        vc.seed_cache_for_tests({
            "code_block_density": {
                "enabled": True,
                "applies_to_niches": ["ai_ml"],
            },
        })
        assert vc.is_validator_enabled("code_block_density", niche=None) is False

    def test_niche_match_is_case_insensitive(self):
        vc.seed_cache_for_tests({
            "first_person_claims": {
                "enabled": True,
                "applies_to_niches": ["dev_diary"],
            },
        })
        assert vc.is_validator_enabled("first_person_claims", niche="DEV_DIARY") is True
        assert vc.is_validator_enabled("first_person_claims", niche="Dev_Diary") is True


# ---------------------------------------------------------------------------
# Legacy CSV bridge — `first_person_claims` honours the old PR #160 setting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLegacyFirstPersonBypass:
    """Replicates the PR #160 test against the new validator_config path.

    The legacy ``qa_allow_first_person_niches`` CSV in app_settings still
    bypasses ``first_person_claims`` so operators on the old path don't
    regress when migration 0135 lands.
    """

    def test_csv_bypass_disables_first_person_for_listed_niche(self):
        """Niche listed in the legacy CSV -> validator reports disabled."""
        import services.site_config as _scm
        site_config = _scm.site_config
        site_config._config["qa_allow_first_person_niches"] = "dev_diary"
        # No DB row: rule otherwise fails open. Bypass should still kick in.
        assert vc.is_validator_enabled("first_person_claims", niche="dev_diary") is False
        # Other niches still run the rule.
        assert vc.is_validator_enabled("first_person_claims", niche="ai_ml") is True

    def test_csv_bypass_does_not_affect_other_rules(self):
        """The CSV legacy bridge is scoped to first_person_claims only."""
        import services.site_config as _scm
        site_config = _scm.site_config
        site_config._config["qa_allow_first_person_niches"] = "dev_diary"
        # Some unrelated rule with no DB row -> still enabled regardless of niche.
        assert vc.is_validator_enabled("fake_stat", niche="dev_diary") is True

    def test_csv_bypass_case_insensitive(self):
        import services.site_config as _scm
        site_config = _scm.site_config
        site_config._config["qa_allow_first_person_niches"] = "DEV_DIARY,gaming"
        assert vc.is_validator_enabled("first_person_claims", niche="dev_diary") is False
        assert vc.is_validator_enabled("first_person_claims", niche="GAMING") is False
        assert vc.is_validator_enabled("first_person_claims", niche="ai_ml") is True

    def test_empty_csv_means_no_bypass(self):
        import services.site_config as _scm
        site_config = _scm.site_config
        site_config._config["qa_allow_first_person_niches"] = ""
        assert vc.is_validator_enabled("first_person_claims", niche="dev_diary") is True


# ---------------------------------------------------------------------------
# get_validator_threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetValidatorThreshold:
    def test_unknown_rule_returns_caller_default(self):
        result = vc.get_validator_threshold(
            "never_seen", default={"x": 1, "y": 2},
        )
        assert result == {"x": 1, "y": 2}

    def test_unknown_rule_with_no_default_returns_empty_dict(self):
        assert vc.get_validator_threshold("never_seen") == {}

    def test_db_threshold_overrides_caller_default(self):
        vc.seed_cache_for_tests({
            "code_block_density": {
                "threshold": {"min_blocks_per_700w": 2},
            },
        })
        result = vc.get_validator_threshold(
            "code_block_density",
            default={"min_blocks_per_700w": 1, "min_line_ratio_pct": 20},
        )
        # min_blocks_per_700w pinned to 2 by DB, ratio falls back to default
        assert result == {"min_blocks_per_700w": 2, "min_line_ratio_pct": 20}

    def test_empty_db_threshold_uses_caller_default(self):
        vc.seed_cache_for_tests({
            "code_block_density": {"threshold": {}},
        })
        result = vc.get_validator_threshold(
            "code_block_density",
            default={"min_blocks_per_700w": 1},
        )
        assert result == {"min_blocks_per_700w": 1}


# ---------------------------------------------------------------------------
# get_validator_severity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetValidatorSeverity:
    def test_unknown_rule_returns_default(self):
        assert vc.get_validator_severity("never_seen") == "warning"
        assert vc.get_validator_severity("never_seen", default="info") == "info"

    def test_db_severity_takes_priority(self):
        vc.seed_cache_for_tests({
            "fake_stat": {"severity": "error"},
        })
        assert vc.get_validator_severity("fake_stat") == "error"


# ---------------------------------------------------------------------------
# list_validator_rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListValidatorRules:
    def test_returns_sorted_snapshot(self):
        vc.seed_cache_for_tests({
            "zeta_rule": {"enabled": True},
            "alpha_rule": {"enabled": False, "severity": "error"},
            "mu_rule": {"enabled": True, "applies_to_niches": ["a"]},
        })
        rules = vc.list_validator_rules()
        assert [r.name for r in rules] == ["alpha_rule", "mu_rule", "zeta_rule"]
        assert rules[0].enabled is False
        assert rules[0].severity == "error"
        assert rules[1].applies_to_niches == ("a",)

    def test_returns_seeded_rules_after_seed(self):
        # Empty seed -> list is empty (cache loader still tries to hit DB
        # but seed_cache_for_tests overrides it before the next call).
        vc.seed_cache_for_tests({"only_one": {"enabled": True}})
        rules = vc.list_validator_rules()
        assert [r.name for r in rules] == ["only_one"]
