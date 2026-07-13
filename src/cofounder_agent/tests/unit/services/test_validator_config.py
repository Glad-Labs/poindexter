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
from services.site_config import SiteConfig


def _sc(**overrides) -> SiteConfig:
    """Fresh SiteConfig for the now-required ``site_config=`` kwarg (#272
    Phase-2d). ``overrides`` seed app_settings keys (e.g. the legacy
    ``qa_allow_first_person_niches`` CSV)."""
    sc = SiteConfig()
    for k, v in overrides.items():
        sc._config[k] = v
    return sc


def _sc_with_pool(**overrides) -> SiteConfig:
    """SiteConfig with a live-looking ``_pool`` so ``_get_cached_rules``'s
    pool gate (added by #2464) doesn't short-circuit before reaching the
    DB-load attempt these cooldown tests exercise. The pool is a bare
    sentinel — nothing here ever calls into it, only checks it's not None."""
    sc = _sc(**overrides)
    sc._pool = object()
    return sc


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
        assert vc.is_validator_enabled("never_seen_rule", site_config=_sc()) is True

    def test_disabled_rule_returns_false(self):
        vc.seed_cache_for_tests({
            "first_person_claims": {"enabled": False},
        })
        assert vc.is_validator_enabled("first_person_claims", site_config=_sc()) is False

    def test_enabled_rule_with_no_niche_scope_runs_for_any_niche(self):
        vc.seed_cache_for_tests({
            "fake_stat": {"enabled": True, "applies_to_niches": None},
        })
        assert vc.is_validator_enabled("fake_stat", niche="ai_ml", site_config=_sc()) is True
        assert vc.is_validator_enabled("fake_stat", niche="dev_diary", site_config=_sc()) is True
        assert vc.is_validator_enabled("fake_stat", niche=None, site_config=_sc()) is True

    def test_niche_scoped_rule_only_runs_in_listed_niches(self):
        vc.seed_cache_for_tests({
            "code_block_density": {
                "enabled": True,
                "applies_to_niches": ["ai_ml", "gaming"],
            },
        })
        assert vc.is_validator_enabled("code_block_density", niche="ai_ml", site_config=_sc()) is True
        assert vc.is_validator_enabled("code_block_density", niche="gaming", site_config=_sc()) is True
        assert vc.is_validator_enabled("code_block_density", niche="dev_diary", site_config=_sc()) is False

    def test_niche_scoped_rule_with_no_post_niche_returns_false(self):
        """A rule pinned to specific niches but called with niche=None
        treats it as out-of-scope rather than silently applying everywhere."""
        vc.seed_cache_for_tests({
            "code_block_density": {
                "enabled": True,
                "applies_to_niches": ["ai_ml"],
            },
        })
        assert vc.is_validator_enabled("code_block_density", niche=None, site_config=_sc()) is False

    def test_niche_match_is_case_insensitive(self):
        vc.seed_cache_for_tests({
            "first_person_claims": {
                "enabled": True,
                "applies_to_niches": ["dev_diary"],
            },
        })
        assert vc.is_validator_enabled("first_person_claims", niche="DEV_DIARY", site_config=_sc()) is True
        assert vc.is_validator_enabled("first_person_claims", niche="Dev_Diary", site_config=_sc()) is True


# ---------------------------------------------------------------------------
# Failed-load cooldown — a DB-unreachable result must be cached for the TTL
# just like a successful load, or every validator check in validate_content()
# (30+ per post) re-issues its own DB connection attempt.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailedLoadCooldown:
    """Covers the case #2464's pool-presence gate doesn't: a live pool
    IS present (production, or these tests' faked pool) but the load
    itself fails or the DB is slow/unreachable — the original bug this
    fix targets."""

    def test_repeated_calls_after_failed_load_do_not_reload(self, monkeypatch):
        calls = {"n": 0}

        def _fake_load_rules_sync():
            calls["n"] += 1
            return {}

        monkeypatch.setattr(vc, "_load_rules_sync", _fake_load_rules_sync)

        for _ in range(5):
            vc.is_validator_enabled("some_rule", site_config=_sc_with_pool())

        assert calls["n"] == 1

    def test_successful_load_after_failure_refreshes_cache(self, monkeypatch):
        """Sanity check the cooldown doesn't wedge a good load out forever —
        once _load_rules_sync starts succeeding, the very next call (after
        the failed attempt) should pick it up rather than staying stuck on
        the failed-cooldown branch."""
        rule = vc._ValidatorRule(
            name="some_rule",
            enabled=False,
            severity="warning",
            threshold={},
            applies_to_niches=None,
            description="",
        )
        responses = [{}, {"some_rule": rule}]

        def _fake_load_rules_sync():
            return responses.pop(0) if responses else {"some_rule": rule}

        monkeypatch.setattr(vc, "_load_rules_sync", _fake_load_rules_sync)
        monkeypatch.setattr(vc, "_CACHE_TTL_SECONDS", 0.0)

        assert vc.is_validator_enabled("some_rule", site_config=_sc_with_pool()) is True
        assert vc.is_validator_enabled("some_rule", site_config=_sc_with_pool()) is False


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
        sc = _sc(qa_allow_first_person_niches="dev_diary")
        # No DB row: rule otherwise fails open. Bypass should still kick in.
        assert vc.is_validator_enabled("first_person_claims", niche="dev_diary", site_config=sc) is False
        # Other niches still run the rule.
        assert vc.is_validator_enabled("first_person_claims", niche="ai_ml", site_config=sc) is True

    def test_csv_bypass_does_not_affect_other_rules(self):
        """The CSV legacy bridge is scoped to first_person_claims only."""
        sc = _sc(qa_allow_first_person_niches="dev_diary")
        # Some unrelated rule with no DB row -> still enabled regardless of niche.
        assert vc.is_validator_enabled("fake_stat", niche="dev_diary", site_config=sc) is True

    def test_csv_bypass_case_insensitive(self):
        sc = _sc(qa_allow_first_person_niches="DEV_DIARY,gaming")
        assert vc.is_validator_enabled("first_person_claims", niche="dev_diary", site_config=sc) is False
        assert vc.is_validator_enabled("first_person_claims", niche="GAMING", site_config=sc) is False
        assert vc.is_validator_enabled("first_person_claims", niche="ai_ml", site_config=sc) is True

    def test_empty_csv_means_no_bypass(self):
        sc = _sc(qa_allow_first_person_niches="")
        assert vc.is_validator_enabled("first_person_claims", niche="dev_diary", site_config=sc) is True


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
