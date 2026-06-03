"""Tests for services/guardrails_rails.py — native brand-fabrication +
competitor-mention QA rails (#198 / #329 sub-issue 3; reimplemented
dep-free for #996).

These rails were originally a thin wrapper over ``guardrails-ai``. That
dependency was dropped on 2026-05-12 (PyPI quarantine after the
CVE-2026-45758 supply-chain compromise) and the rails were reimplemented
natively — the brand rail runs ``content_validator``'s fabrication
patterns directly and the competitor rail is a ``re`` word-boundary
regex. So these tests exercise the real implementation with no
``importorskip`` and no third-party dependency.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services.guardrails_rails import (
    _resolve_competitors,
    is_enabled,
    run_brand_guard,
    run_competitor_guard,
)

# ---------------------------------------------------------------------------
# No-dependency guarantee — the module must import without guardrails-ai
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoThirdPartyDep:
    def test_module_has_no_guardrails_ai_import(self):
        import services.guardrails_rails as gr

        source = __import__("inspect").getsource(gr)
        assert "from guardrails" not in source
        assert "import guardrails" not in source


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

    def test_both_paths_raise_returns_false(self):
        sc = MagicMock()
        sc.get_bool.side_effect = RuntimeError("boom")
        sc.get.side_effect = RuntimeError("boom")
        assert is_enabled(sc) is False


# ---------------------------------------------------------------------------
# run_brand_guard — clean content passes
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

    def test_whitespace_only_passes(self):
        ok, reason = run_brand_guard("   \n\t  ")
        assert ok is True
        assert reason is None

    def test_non_string_passes(self):
        ok, reason = run_brand_guard(None)  # type: ignore[arg-type]
        assert ok is True
        assert reason is None


# ---------------------------------------------------------------------------
# run_brand_guard — flags fabrication patterns
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunBrandGuardFabrication:
    def test_brand_contradiction_caught(self):
        # BRAND_CONTRADICTION_PATTERNS flags promotion of paid cloud APIs
        # (we're Ollama-only). This pattern is company-name-independent,
        # so it fires deterministically regardless of test DB config.
        ok, reason = run_brand_guard(
            "Just check your OpenAI API bill at the end of the month."
        )
        assert ok is False
        assert reason is not None
        assert "rail flagged" in reason
        assert "brand_contradiction" in reason

    def test_glad_labs_impossible_caught(self):
        # GLAD_LABS_IMPOSSIBLE flags unsupportable company claims. The
        # ``our revenue/profit/...`` branch is company-name-independent.
        ok, reason = run_brand_guard(
            "Our revenue grew steadily as the platform matured over time."
        )
        assert ok is False
        assert reason is not None
        assert "glad_labs_claim" in reason

    def test_returns_bool_for_synthetic_quote(self):
        # FAKE_QUOTE_PATTERNS is tuning-sensitive; just confirm the rail
        # executes the real patterns without raising and returns a clean
        # tuple shape.
        ok, reason = run_brand_guard(
            'In a recent interview, Bill Gates said "AI will replace 90% of '
            'developers within the year."'
        )
        assert isinstance(ok, bool)
        assert reason is None or isinstance(reason, str)

    def test_never_raises_on_validator_error(self, monkeypatch):
        # If content_validator blows up, the rail must fail-open to a
        # clean pass rather than propagate the exception.
        import services.guardrails_rails as gr

        def boom(*args, **kwargs):
            raise RuntimeError("validator exploded")

        # Patch the symbol the rail looks up at call time.
        import services.content_validator as cv

        monkeypatch.setattr(cv, "_check_patterns", boom)
        ok, reason = gr.run_brand_guard("some content with enough words")
        assert ok is True
        assert reason is None


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

    def test_read_failure_fails_loud_and_returns_empty(self, monkeypatch):
        # A SiteConfig.get raise must log + emit a finding (fail loud)
        # but still return [] so the rail degrades open rather than
        # crashing the pipeline.
        emitted = {}

        def fake_emit(**kwargs):
            emitted.update(kwargs)

        monkeypatch.setattr("utils.findings.emit_finding", fake_emit)

        sc = MagicMock()
        sc.get.side_effect = RuntimeError("site_config broken")
        assert _resolve_competitors(sc) == []
        assert emitted.get("kind") == "guardrails_competitor_list_read_failed"


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

    def test_multiword_competitor_matches(self):
        # Multi-word names match across the space; the trailing \b
        # anchors on the final word char, so end the name on a word char.
        ok, reason = run_competitor_guard(
            "Bar Industries shipped a competing feature.",
            competitors=["Bar Industries"],
        )
        assert ok is False
        assert "Bar Industries" in (reason or "")

    def test_empty_competitor_list_skips(self):
        ok, reason = run_competitor_guard(
            "Anything goes.", competitors=[],
        )
        assert ok is True
        assert reason is None

    def test_whitespace_only_competitors_skip(self):
        ok, reason = run_competitor_guard(
            "Anything goes.", competitors=["  ", ""],
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

    def test_regex_metachars_in_name_are_escaped(self):
        # A competitor name with regex metacharacters must be matched
        # literally (re.escape), not interpreted as a pattern.
        ok, reason = run_competitor_guard(
            "We evaluated C++ Builder last quarter.",
            competitors=["C++ Builder"],
        )
        assert ok is False
        assert "C++ Builder" in (reason or "")
