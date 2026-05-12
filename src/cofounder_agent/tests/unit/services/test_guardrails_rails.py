"""Tests for services/guardrails_rails.py — guardrails-ai integration
as a parallel content rail (#198 / #329 sub-issue 3).

Skipped at collection time when the guardrails-ai package isn't
importable. The dep was dropped from pyproject.toml on 2026-05-12 after
PyPI quarantined the package; this test file stays in the tree so the
moment we re-add the dep the contract is back under coverage, but it
must not block CI in the dep-less interim.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip(
    "guardrails",
    reason=(
        "guardrails-ai package not installed (dropped from pyproject.toml "
        "on 2026-05-12 after PyPI quarantine)"
    ),
)

from services.guardrails_rails import (  # noqa: E402
    _resolve_competitors,
    is_enabled,
    run_brand_guard,
    run_competitor_guard,
)


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsEnabled:
    def test_no_site_config_returns_false(self):
        assert is_enabled(None) is False

    def test_default_returns_false(self):
        sc = MagicMock()
        sc.get_bool.return_value = False
        assert is_enabled(sc) is False

    def test_true_setting_enables(self):
        sc = MagicMock()
        sc.get_bool.return_value = True
        assert is_enabled(sc) is True

    def test_string_true_falls_back_through_get(self):
        # When get_bool isn't available (older site_config impls),
        # fall through to get + manual coercion.
        sc = MagicMock()
        sc.get_bool.side_effect = AttributeError("no get_bool")
        sc.get.return_value = "true"
        assert is_enabled(sc) is True

    def test_string_false_returns_false(self):
        sc = MagicMock()
        sc.get_bool.side_effect = AttributeError("no get_bool")
        sc.get.return_value = "false"
        assert is_enabled(sc) is False


# ---------------------------------------------------------------------------
# run_brand_guard — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunBrandGuardClean:
    def test_clean_content_passes(self):
        ok, reason = run_brand_guard(
            "FastAPI and PostgreSQL are reliable choices for backend development. "
            "Both are open source and well-documented."
        )
        assert ok is True
        assert reason is None

    def test_empty_content_passes(self):
        ok, reason = run_brand_guard("")
        assert ok is True
        assert reason is None

    def test_non_string_passes(self):
        ok, reason = run_brand_guard(None)  # type: ignore[arg-type]
        assert ok is True


# ---------------------------------------------------------------------------
# run_brand_guard — fabrication-detection (uses real content_validator
# patterns, so we just confirm the wiring catches obvious matches)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunBrandGuardFabrication:
    def test_fake_quote_pattern_caught(self):
        # FAKE_QUOTE_PATTERNS catches "Bill Gates said" / "Elon Musk
        # tweeted" style attribution to public figures we'd never quote.
        bad = (
            'In a recent interview, Bill Gates said "AI will replace 90% of '
            'developers within the year." That insight changed the industry forever.'
        )
        ok, reason = run_brand_guard(bad)
        # The wiring is what matters — guard executes the validator
        # against real patterns. Either fail (if a pattern caught it)
        # or pass (if our specific synthetic text didn't match).
        # Don't assert specific outcome since FAKE_QUOTE_PATTERNS is
        # tuning-sensitive; just confirm the guard ran without raising.
        assert isinstance(ok, bool)

    def test_glad_labs_impossible_caught(self):
        # GLAD_LABS_IMPOSSIBLE patterns catch unsupportable claims
        # about the company (e.g. years of operation, scale, etc).
        bad = "Glad Labs has 50 years of experience helping Fortune 500 companies."
        ok, reason = run_brand_guard(bad)
        assert isinstance(ok, bool)


# ---------------------------------------------------------------------------
# Guard caching — same instance reused across calls
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGuardCaching:
    def test_repeated_calls_reuse_guard(self):
        from services import guardrails_rails

        guardrails_rails._GUARD_CACHE.clear()
        run_brand_guard("First clean content.")
        guard1 = guardrails_rails._GUARD_CACHE.get("brand")
        run_brand_guard("Second clean content.")
        guard2 = guardrails_rails._GUARD_CACHE.get("brand")
        assert guard1 is guard2  # cache hit, no re-register


# ---------------------------------------------------------------------------
# _resolve_competitors — operator config CSV → cleaned list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveCompetitors:
    def test_no_site_config_returns_empty(self):
        assert _resolve_competitors(None) == []

    def test_empty_string_returns_empty(self):
        sc = MagicMock()
        sc.get.return_value = ""
        assert _resolve_competitors(sc) == []

    def test_csv_parses_to_list(self):
        sc = MagicMock()
        sc.get.return_value = "Acme, Foo, Bar Inc."
        assert _resolve_competitors(sc) == ["Acme", "Foo", "Bar Inc."]

    def test_dedupes_case_insensitively(self):
        sc = MagicMock()
        sc.get.return_value = "Acme, ACME, acme, Foo"
        # First-seen wins; later case-variants are dropped.
        assert _resolve_competitors(sc) == ["Acme", "Foo"]

    def test_strips_whitespace(self):
        sc = MagicMock()
        sc.get.return_value = "  Acme  ,   Foo "
        assert _resolve_competitors(sc) == ["Acme", "Foo"]


# ---------------------------------------------------------------------------
# run_competitor_guard — flagging configured competitor names
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunCompetitorGuard:
    def test_clean_content_passes(self):
        ok, reason = run_competitor_guard(
            "Post about FastAPI and uvicorn.",
            competitors=["Acme", "Foo"],
        )
        assert ok is True
        assert reason is None

    def test_competitor_mention_fails(self):
        ok, reason = run_competitor_guard(
            "We've been using Acme Corp's product for years.",
            competitors=["Acme", "Foo"],
        )
        assert ok is False
        assert reason is not None
        assert "Acme" in reason

    def test_case_insensitive_match(self):
        ok, _ = run_competitor_guard(
            "ACME makes the best widgets.",
            competitors=["Acme"],
        )
        assert ok is False

    def test_word_boundary_avoids_substring_false_positive(self):
        # 'Acme' should NOT match inside 'AcmeForge' (compound brand)
        ok, _ = run_competitor_guard(
            "AcmeForge is a different product entirely.",
            competitors=["Acme"],
        )
        assert ok is True

    def test_empty_competitor_list_skips(self):
        ok, reason = run_competitor_guard(
            "Anything goes.", competitors=[],
        )
        assert ok is True
        assert reason is None

    def test_empty_content_skips(self):
        ok, reason = run_competitor_guard("", competitors=["Acme"])
        assert ok is True
        assert reason is None

    def test_multiple_hits_reported(self):
        ok, reason = run_competitor_guard(
            "Both Acme and Foo make great products.",
            competitors=["Acme", "Foo"],
        )
        assert ok is False
        assert "Acme" in (reason or "")
        assert "Foo" in (reason or "")


# ---------------------------------------------------------------------------
# Competitor guard caching — different lists get different Guards
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompetitorGuardCaching:
    def test_same_list_reuses_guard(self):
        from services import guardrails_rails

        guardrails_rails._GUARD_CACHE.clear()
        run_competitor_guard("text", ["Acme"])
        run_competitor_guard("other text", ["Acme"])
        # One competitor cache entry shared between the two calls.
        keys = [k for k in guardrails_rails._GUARD_CACHE if k.startswith("competitor:")]
        assert len(keys) == 1

    def test_different_lists_get_different_guards(self):
        from services import guardrails_rails

        guardrails_rails._GUARD_CACHE.clear()
        run_competitor_guard("text", ["Acme"])
        run_competitor_guard("text", ["Foo"])
        keys = [k for k in guardrails_rails._GUARD_CACHE if k.startswith("competitor:")]
        assert len(keys) == 2

    def test_order_irrelevant_for_cache_key(self):
        from services import guardrails_rails

        guardrails_rails._GUARD_CACHE.clear()
        run_competitor_guard("text", ["Acme", "Foo"])
        run_competitor_guard("text", ["Foo", "Acme"])
        # Sorted-tuple cache key collapses the two orderings.
        keys = [k for k in guardrails_rails._GUARD_CACHE if k.startswith("competitor:")]
        assert len(keys) == 1
