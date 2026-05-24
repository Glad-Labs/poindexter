"""
Tests for ``services.citation_verifier``.

Originally focused on the poindexter#485 follow-up: the crawler User-Agent
string must NOT hardcode ``gladlabs.io`` (or any operator-specific URL).
The ``+url`` portion of the standard crawler-UA convention now comes from
``app_settings.crawler_contact_url`` — empty means the portion is
omitted entirely, so OSS forks identify themselves as
``PoindexterCitationVerifier`` without leaking the source operator's
contact info.

Expanded 2026-05-23 to cover the rest of the public surface:
``extract_urls``, ``CitationReport.summary``, ``verdict_from_report``,
``append_sources_section``. All are pure functions exercised here
without network I/O.
"""

import pytest

from services.citation_verifier import (
    CitationIssue,
    CitationReport,
    _build_citation_headers,
    append_sources_section,
    extract_urls,
    set_site_config,
    verdict_from_report,
)
from services.site_config import SiteConfig


@pytest.mark.unit
class TestCitationHeaders:
    def test_no_contact_url_omits_plus_portion(self):
        """OSS default: ``crawler_contact_url`` unset → User-Agent
        ends at the version string, no ``+url`` tail. The previous
        behaviour leaked ``+https://gladlabs.io)`` into every OSS
        install's HEAD requests.
        """
        set_site_config(SiteConfig())

        headers = _build_citation_headers()

        assert headers["User-Agent"] == (
            "Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0)"
        )
        # The exact leak pattern the helper was added to close:
        assert "gladlabs.io" not in headers["User-Agent"]
        assert "+" not in headers["User-Agent"]

    def test_contact_url_appears_in_user_agent(self):
        """Operator-set ``crawler_contact_url`` lands in the
        ``+<url>`` portion of the User-Agent. Site admins receiving
        traffic from this crawler can reach the operator at that URL.
        """
        set_site_config(SiteConfig(initial_config={
            "crawler_contact_url": "https://example.org/contact",
        }))

        headers = _build_citation_headers()

        assert headers["User-Agent"] == (
            "Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0; "
            "+https://example.org/contact)"
        )

    def test_contact_url_whitespace_stripped(self):
        """Whitespace-only or blank-after-strip values are treated as
        empty. Otherwise an operator who typed a trailing newline in
        OpenClaw would emit ``+   )`` as the UA tail.
        """
        set_site_config(SiteConfig(initial_config={
            "crawler_contact_url": "   ",
        }))

        headers = _build_citation_headers()

        assert "+" not in headers["User-Agent"]

    def test_site_config_read_failure_falls_back_to_no_contact(self):
        """A misbehaving SiteConfig (DB pool exhausted, missing
        column) doesn't break HEAD probing — falls back to the
        no-contact UA. The citation verifier is a quality signal,
        not a critical path, so a missing UA contact never blocks.
        """

        class _BrokenSC:
            def get(self, key, default=""):
                raise RuntimeError("simulated SiteConfig failure")

        set_site_config(_BrokenSC())  # type: ignore[arg-type]
        try:
            headers = _build_citation_headers()
            assert headers["User-Agent"] == (
                "Mozilla/5.0 (compatible; PoindexterCitationVerifier/1.0)"
            )
            assert headers["Accept"] == "*/*"
        finally:
            # Reset so other tests don't see the broken stub.
            set_site_config(SiteConfig())

    def test_accept_header_always_wildcard(self):
        set_site_config(SiteConfig())
        assert _build_citation_headers()["Accept"] == "*/*"
        set_site_config(SiteConfig(initial_config={
            "crawler_contact_url": "https://example.com",
        }))
        assert _build_citation_headers()["Accept"] == "*/*"


@pytest.mark.unit
class TestExtractUrls:
    """Coverage for the three URL-detection regexes + dedup + site-filter."""

    def test_mixed_link_forms_and_dedup(self):
        # All three link shapes the writer emits should be picked up,
        # and a URL that appears in two forms should land once.
        content = (
            "See [docs](https://example.com/a) and <https://example.com/b>. "
            "Also https://example.com/c here. "
            "And the bare twin https://example.com/a again."
        )

        urls = extract_urls(content)

        assert urls == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]

    def test_trailing_punctuation_stripped(self):
        # Prose-embedded URLs often end at a sentence boundary; the
        # rstrip in extract_urls keeps "https://x.com" out of the dead-
        # link list because of a trailing period.
        content = "Read https://example.com/a. Then https://example.com/b!"

        urls = extract_urls(content)

        assert urls == ["https://example.com/a", "https://example.com/b"]

    def test_site_url_filters_internal_links(self):
        # Internal links shouldn't be HEAD'd — the writer's own
        # known-slug allowlist + scrub_fabricated_links already cover
        # them, and probing our own origin would create a feedback loop.
        content = (
            "[home](https://gladlabs.io/) and "
            "[post](https://gladlabs.io/posts/foo) and "
            "[ext](https://example.com/x)"
        )

        urls = extract_urls(content, site_url="https://gladlabs.io")

        assert urls == ["https://example.com/x"]

    def test_malformed_site_url_falls_back_to_no_filter(self):
        # A misconfigured site_url (no scheme/netloc) shouldn't crash
        # the verifier — it should fall through and return every URL.
        content = "[a](https://example.com/a) [b](https://gladlabs.io/b)"

        urls = extract_urls(content, site_url="not a url")

        assert urls == ["https://example.com/a", "https://gladlabs.io/b"]


@pytest.mark.unit
class TestCitationReportSummary:
    """`summary()` is what surfaces in QA feedback — exercise the dead-truncation edge."""

    def test_summary_truncates_dead_list_after_three(self):
        # The summary inlines up to 3 dead URLs and appends "…" — keeps
        # QA feedback readable when a stale-source post has 10+ broken
        # citations.
        dead = [
            CitationIssue(url=f"https://x.com/{i}", reason="dead", detail="x")
            for i in range(5)
        ]
        report = CitationReport(
            total_urls=5, unique_urls=5, dead=dead, dead_ratio=1.0,
        )

        out = report.summary()

        assert out.startswith("5/5 dead (100%):")
        assert out.endswith("…")
        # Only the first 3 dead URLs are inlined verbatim.
        assert "https://x.com/0" in out
        assert "https://x.com/2" in out
        assert "https://x.com/3" not in out


@pytest.mark.unit
class TestVerdictFromReport:
    """Policy thresholds — these are the gate that surfaces in QA verdicts."""

    @pytest.mark.asyncio
    async def test_fail_below_min_citations(self):
        # min_citations=3, only 1 URL → policy fail. The reason string
        # must surface the actual + required counts so operators can
        # tune the threshold without re-running.
        report = CitationReport(total_urls=1, unique_urls=1, alive=["a"])

        passed, reason = await verdict_from_report(
            report, max_dead_ratio=0.3, min_citations=3,
        )

        assert passed is False
        assert "Only 1" in reason
        assert "minimum is 3" in reason

    @pytest.mark.asyncio
    async def test_fail_when_dead_ratio_exceeds_threshold(self):
        # 2 of 4 dead = 50%, max is 30%. Verdict should fail and
        # surface the dead URLs (capped at 5).
        dead = [
            CitationIssue(url="https://x.com/1", reason="timeout", detail=""),
            CitationIssue(url="https://x.com/2", reason="dns", detail=""),
        ]
        report = CitationReport(
            total_urls=4, unique_urls=4, alive=["a", "b"],
            dead=dead, dead_ratio=0.5,
        )

        passed, reason = await verdict_from_report(
            report, max_dead_ratio=0.3, min_citations=0,
        )

        assert passed is False
        assert "50%" in reason
        assert "max 30%" in reason
        assert "https://x.com/1" in reason
        assert "https://x.com/2" in reason

    @pytest.mark.asyncio
    async def test_max_dead_ratio_zero_disables_ratio_gate(self):
        # `max_dead_ratio=0` is the documented "disabled" sentinel
        # (the `> 0` guard in verdict_from_report). Even 100% dead
        # should pass when the operator has explicitly disabled the
        # ratio gate.
        dead = [
            CitationIssue(url="https://x", reason="dead", detail=""),
        ]
        report = CitationReport(
            total_urls=1, unique_urls=1, dead=dead, dead_ratio=1.0,
        )

        passed, _reason = await verdict_from_report(
            report, max_dead_ratio=0.0, min_citations=0,
        )

        assert passed is True


@pytest.mark.unit
class TestAppendSourcesSection:
    """Finalize-stage helper — must be idempotent, case/heading-insensitive."""

    def test_appends_when_missing(self):
        content = "Body paragraph."

        out = append_sources_section(content, ["https://a", "https://b"])

        assert out.endswith(
            "Body paragraph.\n\n## Sources\n- <https://a>\n- <https://b>\n"
        )

    def test_idempotent_when_sources_present_case_insensitive(self):
        # Writer sometimes emits ### sources / ## References / ## SOURCES —
        # any of these should short-circuit the append.
        for heading in ("## Sources", "### sources", "## References", "# SOURCES"):
            content = f"Body.\n\n{heading}\n- <https://existing>\n"

            out = append_sources_section(content, ["https://new"])

            assert out == content, f"heading {heading!r} should be respected"
