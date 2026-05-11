"""Unit tests for services/internal_link_coherence.py (GH-88).

Covers the four cases the issue asks for:
  1. Tag coherence passes when source and target share a tag.
  2. Tag coherence fails when tags don't overlap.
  3. Single-target cap rejects the N+1th attempt against the same target.
  4. The audit scanner finds the live "Consider exploring CadQuery" pattern.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.internal_link_coherence import (
    InternalLinkCoherenceFilter,
    LinkCandidate,
    count_inbound_links_to_slug,
    get_tag_slugs_for_post,
    normalize_tag_set,
)

# ---------------------------------------------------------------------------
# normalize_tag_set — input cleaning
# ---------------------------------------------------------------------------


class TestNormalizeTagSet:
    def test_lowercases_and_slugifies(self):
        assert normalize_tag_set(["3D Modeling", "Parametric CAD"]) == {
            "3d-modeling",
            "parametric-cad",
        }

    def test_accepts_dicts_with_slug(self):
        assert normalize_tag_set([{"slug": "python"}, {"name": "FastAPI"}]) == {
            "python",
            "fastapi",
        }

    def test_ignores_empty_and_none(self):
        assert normalize_tag_set([None, "", "Python"]) == {"python"}

    def test_handles_none_input(self):
        assert normalize_tag_set(None) == set()


# ---------------------------------------------------------------------------
# tags_overlap — the coherence primitive
# ---------------------------------------------------------------------------


class TestTagsOverlap:
    def test_overlap_returns_true(self):
        assert InternalLinkCoherenceFilter.tags_overlap(
            ["Python", "Async"], ["Python", "Web"]
        )

    def test_no_overlap_returns_false(self):
        # asyncio post vs CadQuery post — the exact GH-88 scenario.
        assert not InternalLinkCoherenceFilter.tags_overlap(
            ["python", "asyncio", "concurrency"],
            ["3d-modeling", "parametric-cad", "python-library"],
        )

    def test_empty_source_returns_false(self):
        assert not InternalLinkCoherenceFilter.tags_overlap([], ["python"])

    def test_empty_target_returns_false(self):
        assert not InternalLinkCoherenceFilter.tags_overlap(["python"], [])

    def test_slug_name_mix_still_overlaps(self):
        # Source tag "3D Modeling" (name) should match target "3d-modeling" (slug).
        assert InternalLinkCoherenceFilter.tags_overlap(
            ["3D Modeling"], ["3d-modeling"]
        )


# ---------------------------------------------------------------------------
# get_tag_slugs_for_post — DB shim
# ---------------------------------------------------------------------------


class TestGetTagSlugsForPost:
    @pytest.mark.asyncio
    async def test_returns_empty_without_pool(self):
        assert await get_tag_slugs_for_post(None, post_id="abc") == set()

    @pytest.mark.asyncio
    async def test_returns_empty_without_identifiers(self):
        pool = AsyncMock()
        assert await get_tag_slugs_for_post(pool) == set()
        pool.fetch.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetches_by_post_id(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[{"slug": "python"}, {"slug": "asyncio"}]
        )
        result = await get_tag_slugs_for_post(pool, post_id="uuid-123")
        assert result == {"python", "asyncio"}
        assert pool.fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_fetches_by_slug(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[{"slug": "3d-modeling"}])
        result = await get_tag_slugs_for_post(pool, slug="some-post")
        assert result == {"3d-modeling"}

    @pytest.mark.asyncio
    async def test_db_error_returns_empty(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=Exception("connection refused"))
        assert await get_tag_slugs_for_post(pool, post_id="x") == set()


# ---------------------------------------------------------------------------
# count_inbound_links_to_slug
# ---------------------------------------------------------------------------


class TestCountInboundLinks:
    @pytest.mark.asyncio
    async def test_counts_posts_with_internal_link(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                {"id": "1", "content": "See /posts/cadquery-post for more."},
                {"id": "2", "content": "Also check [cad](/posts/cadquery-post)."},
                {"id": "3", "content": "Unrelated content."},
            ]
        )
        count = await count_inbound_links_to_slug(pool, "cadquery-post")
        # rows 1 and 2 match; row 3 shouldn't be in the result set (mocked
        # pool returns all; the Python-side filter catches row 3 because
        # it doesn't contain the slug).
        assert count == 2

    @pytest.mark.asyncio
    async def test_returns_zero_without_pool(self):
        assert await count_inbound_links_to_slug(None, "anything") == 0

    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_slug(self):
        pool = AsyncMock()
        assert await count_inbound_links_to_slug(pool, "") == 0

    @pytest.mark.asyncio
    async def test_db_error_returns_zero(self):
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=Exception("boom"))
        assert await count_inbound_links_to_slug(pool, "cad") == 0

    @pytest.mark.asyncio
    async def test_does_not_double_count_prefix_match(self):
        """A slug like 'foo' shouldn't match inside '/posts/foo-bar-baz'."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(
            return_value=[
                # A prefix-only match — should NOT count.
                {"id": "1", "content": "link to /posts/foo-bar-baz"},
                # A real match — SHOULD count.
                {"id": "2", "content": "real hit /posts/foo"},
            ]
        )
        count = await count_inbound_links_to_slug(pool, "foo")
        assert count == 1


# ---------------------------------------------------------------------------
# InternalLinkCoherenceFilter.filter_candidates — end-to-end gating
# ---------------------------------------------------------------------------


class TestFilterCandidates:
    @pytest.mark.asyncio
    async def test_pass_overlapping_tags(self):
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=True,
            cap_enabled=False,  # disable cap for this test
        )
        cand = LinkCandidate(
            slug="fastapi-intro",
            title="FastAPI Intro",
            tag_slugs={"python", "fastapi"},
        )
        survivors = await filt.filter_candidates(
            source_tags=["Python", "APIs"], candidates=[cand]
        )
        assert survivors == [cand]
        assert cand.rejection_reason is None

    @pytest.mark.asyncio
    async def test_fail_no_tag_overlap(self):
        """The GH-88 scenario: asyncio post shouldn't link to CadQuery."""
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=True,
            cap_enabled=False,
        )
        cadquery = LinkCandidate(
            slug="beyond-blocks-and-lines-how-cadquery-is-revolution-481",
            title="Beyond Blocks and Lines: How CadQuery is Revolutionizing...",
            tag_slugs={"3d-modeling", "parametric-cad"},
        )
        survivors = await filt.filter_candidates(
            source_tags=["python", "asyncio", "concurrency"],
            candidates=[cadquery],
        )
        assert survivors == []
        assert cadquery.rejection_reason == "no_tag_overlap"

    @pytest.mark.asyncio
    async def test_fail_target_has_no_tags(self):
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=True,
            cap_enabled=False,
        )
        cand = LinkCandidate(slug="legacy-post", title="Legacy", tag_slugs=set())
        survivors = await filt.filter_candidates(
            source_tags=["python"], candidates=[cand]
        )
        assert survivors == []
        assert cand.rejection_reason == "target_has_no_tags"

    @pytest.mark.asyncio
    async def test_fail_source_has_no_tags(self):
        """Source with no tags can't prove coherence → reject (no silent pass)."""
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=True,
            cap_enabled=False,
        )
        cand = LinkCandidate(
            slug="a", title="A", tag_slugs={"python"}
        )
        survivors = await filt.filter_candidates(
            source_tags=[], candidates=[cand]
        )
        assert survivors == []
        assert cand.rejection_reason == "source_has_no_tags"

    @pytest.mark.asyncio
    async def test_cap_rejects_over_limit(self):
        """Same target beyond the cap is rejected."""
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=False,  # isolate cap behaviour
            cap_enabled=True,
            single_target_cap=3,
        )
        # Candidate pre-seeded with an inbound_count at the cap.
        cand = LinkCandidate(
            slug="popular-post",
            title="Popular",
            inbound_count=3,  # already at cap → reject
            tag_slugs={"python"},
        )
        survivors = await filt.filter_candidates(
            source_tags=["python"], candidates=[cand]
        )
        assert survivors == []
        assert cand.rejection_reason == "single_target_cap"

    @pytest.mark.asyncio
    async def test_cap_accepts_under_limit(self):
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=False,
            cap_enabled=True,
            single_target_cap=3,
        )
        cand = LinkCandidate(
            slug="medium-popular",
            title="Medium",
            inbound_count=2,  # under cap → allow
            tag_slugs={"python"},
        )
        survivors = await filt.filter_candidates(
            source_tags=["python"], candidates=[cand]
        )
        assert survivors == [cand]
        assert cand.rejection_reason is None

    @pytest.mark.asyncio
    async def test_cap_enforcement_across_repeated_attempts(self):
        """Simulate N+1 attempts at the same target — the N+1th is rejected."""
        # Fresh filter with cap=3.
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=False,
            cap_enabled=True,
            single_target_cap=3,
        )

        def make_cand(inbound: int) -> LinkCandidate:
            return LinkCandidate(
                slug="cadquery", title="CadQuery", inbound_count=inbound,
                tag_slugs={"3d"},
            )

        # First three attempts simulate incrementing inbound counts:
        for inbound in (0, 1, 2):
            c = make_cand(inbound)
            survivors = await filt.filter_candidates(
                source_tags=["3d"], candidates=[c]
            )
            assert survivors == [c], f"Expected pass at inbound={inbound}"

        # Fourth attempt — at cap.
        c = make_cand(3)
        survivors = await filt.filter_candidates(
            source_tags=["3d"], candidates=[c]
        )
        assert survivors == []
        assert c.rejection_reason == "single_target_cap"

    @pytest.mark.asyncio
    async def test_both_gates_applied(self):
        """Tag gate is applied before cap; rejection_reason reflects first fail."""
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=True,
            cap_enabled=True,
            single_target_cap=1,
        )
        # No tag overlap AND over cap — tag gate should fire first.
        cand = LinkCandidate(
            slug="x",
            title="X",
            tag_slugs={"cooking"},
            inbound_count=99,
        )
        survivors = await filt.filter_candidates(
            source_tags=["python"], candidates=[cand]
        )
        assert survivors == []
        assert cand.rejection_reason == "no_tag_overlap"

    @pytest.mark.asyncio
    async def test_self_link_rejected(self):
        """poindexter#470: a post must never link to itself.

        When ``current_post_id`` matches the candidate's ``post_id`` the
        candidate is short-circuited as ``self_link`` — no DB work, no
        tag lookup, no cap check.
        """
        pool = AsyncMock()
        # If self-link short-circuit is broken, the filter falls through
        # to hydrate tags via this pool — we'd see fetch awaited.
        pool.fetch = AsyncMock(return_value=[{"slug": "python"}])
        filt = InternalLinkCoherenceFilter(
            pool=pool,
            tag_coherence_required=True,
            cap_enabled=False,
        )
        cand = LinkCandidate(
            slug="my-own-post",
            title="My Own Post",
            post_id="11111111-1111-1111-1111-111111111111",
            tag_slugs={"python"},
        )
        survivors = await filt.filter_candidates(
            source_tags=["python"],
            candidates=[cand],
            current_post_id="11111111-1111-1111-1111-111111111111",
        )
        assert survivors == []
        assert cand.rejection_reason == "self_link"
        pool.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_self_link_rejected_with_post_prefix(self):
        """poindexter#470: ``post/<uuid>`` is normalized for comparison."""
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=False,
            cap_enabled=False,
        )
        cand = LinkCandidate(
            slug="x",
            title="X",
            post_id="post/22222222-2222-2222-2222-222222222222",
            tag_slugs={"python"},
        )
        survivors = await filt.filter_candidates(
            source_tags=["python"],
            candidates=[cand],
            current_post_id="22222222-2222-2222-2222-222222222222",
        )
        assert survivors == []
        assert cand.rejection_reason == "self_link"

    @pytest.mark.asyncio
    async def test_no_current_post_id_skips_self_check(self):
        """Backwards-compat: when current_post_id is None, no self-link gate."""
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=False,
            cap_enabled=False,
        )
        cand = LinkCandidate(
            slug="x",
            title="X",
            post_id="33333333-3333-3333-3333-333333333333",
            tag_slugs={"python"},
        )
        # current_post_id omitted -> existing behaviour, candidate passes.
        survivors = await filt.filter_candidates(
            source_tags=["python"], candidates=[cand]
        )
        assert survivors == [cand]
        assert cand.rejection_reason is None

    @pytest.mark.asyncio
    async def test_self_link_does_not_affect_siblings(self):
        """poindexter#470: only the matching candidate is dropped."""
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=False,
            cap_enabled=False,
        )
        self_cand = LinkCandidate(
            slug="self",
            title="Self",
            post_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            tag_slugs={"python"},
        )
        other_cand = LinkCandidate(
            slug="other",
            title="Other",
            post_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            tag_slugs={"python"},
        )
        survivors = await filt.filter_candidates(
            source_tags=["python"],
            candidates=[self_cand, other_cand],
            current_post_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )
        assert survivors == [other_cand]
        assert self_cand.rejection_reason == "self_link"
        assert other_cand.rejection_reason is None

    @pytest.mark.asyncio
    async def test_hydrates_tag_slugs_from_pool(self):
        """If the candidate has no pre-seeded tags, the filter queries the DB."""
        pool = AsyncMock()
        # Called by get_tag_slugs_for_post.
        pool.fetch = AsyncMock(return_value=[{"slug": "python"}])
        filt = InternalLinkCoherenceFilter(
            pool=pool,
            tag_coherence_required=True,
            cap_enabled=False,
        )
        cand = LinkCandidate(slug="x", title="X", post_id="abc")
        # tag_slugs left as None → must hydrate
        survivors = await filt.filter_candidates(
            source_tags=["python"], candidates=[cand]
        )
        assert survivors == [cand]
        assert cand.tag_slugs == {"python"}


# ---------------------------------------------------------------------------
# Audit scanner — verifies the regex patterns catch the live CadQuery pattern.
# The script itself is exercised via subprocess in a separate test that's
# skipped when asyncpg / a DB isn't reachable; here we just import and call
# the helpers directly.
# ---------------------------------------------------------------------------


_AUDIT_SCRIPT = (
    Path(__file__).resolve().parents[5] / "scripts" / "audit_internal_link_coherence.py"
)


def _load_audit_module():
    """Import the audit script as a module without running __main__."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("audit_module", _AUDIT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestAuditScanner:
    def test_cadquery_phrase_matches_live_pattern(self):
        """Real content from GH-88 published post `0b5e81ef`."""
        mod = _load_audit_module()
        content = (
            "We need to prioritize understanding and validation over speed. "
            "Consider exploring tools like "
            "[CadQuery](https://www.gladlabs.io/posts/"
            "beyond-blocks-and-lines-how-cadquery-is-revolution-481) "
            "to deepen your understanding of underlying principles."
        )
        phrase_hits = mod._find_cadquery_phrases(content)
        url_hits = mod._find_cadquery_urls(content)
        assert len(phrase_hits) == 1, phrase_hits
        assert "CadQuery" in phrase_hits[0]
        assert len(url_hits) == 1, url_hits
        assert "beyond-blocks-and-lines-how-cadquery" in url_hits[0]

    def test_cadquery_phrase_matches_asyncio_flavor(self):
        """Real content from GH-88 pending task `859bdb97`."""
        mod = _load_audit_module()
        content = (
            "Consider exploring CadQuery to see how asyncio is used in a "
            "more complex application."
        )
        phrase_hits = mod._find_cadquery_phrases(content)
        assert len(phrase_hits) == 1

    def test_audit_phrase_is_case_insensitive(self):
        mod = _load_audit_module()
        assert mod._find_cadquery_phrases("consider EXPLORING CADQUERY here")

    def test_audit_skips_unrelated_content(self):
        mod = _load_audit_module()
        assert mod._find_cadquery_phrases("Consider exploring FastAPI.") == []
        assert mod._find_cadquery_urls("Go to /posts/unrelated-slug") == []

    def test_internal_link_regex_extracts_slug(self):
        mod = _load_audit_module()
        content = (
            "Read [one](/posts/first-post) and "
            "[two](https://gladlabs.io/posts/second-post-xyz) then "
            "visit /posts/third-post next."
        )
        slugs = mod._find_internal_links(content)
        assert {"first-post", "second-post-xyz", "third-post"} == slugs

    def test_internal_link_regex_ignores_non_post_paths(self):
        mod = _load_audit_module()
        content = "Link to /about and /tags/python and /posts/"
        slugs = mod._find_internal_links(content)
        assert slugs == set()


# ---------------------------------------------------------------------------
# Integration-style smoke test: filter behaviour with realistic candidate
# list mirroring the RAG context builder's output.
# ---------------------------------------------------------------------------


class TestRealisticRagScenario:
    @pytest.mark.asyncio
    async def test_cadquery_stripped_from_asyncio_post_candidates(self):
        """End-to-end: CadQuery-like candidate rejected; on-topic kept."""
        filt = InternalLinkCoherenceFilter(
            pool=None,
            tag_coherence_required=True,
            cap_enabled=False,
        )
        candidates = [
            LinkCandidate(
                slug="fastapi-async-patterns-that-actually-matter",
                title="FastAPI Async Patterns",
                tag_slugs={"python", "asyncio", "fastapi"},
            ),
            LinkCandidate(
                slug="beyond-blocks-and-lines-how-cadquery-is-revolution-481",
                title="Beyond Blocks and Lines: How CadQuery is Revolutionizing...",
                tag_slugs={"3d-modeling", "parametric-cad"},
            ),
            LinkCandidate(
                slug="why-solo-developers-should-embrace-docker",
                title="Docker for Solo Devs",
                tag_slugs={"docker", "devops"},
            ),
        ]
        survivors = await filt.filter_candidates(
            source_tags=["python", "asyncio"],
            candidates=candidates,
        )
        survivor_slugs = [c.slug for c in survivors]
        assert "fastapi-async-patterns-that-actually-matter" in survivor_slugs
        assert (
            "beyond-blocks-and-lines-how-cadquery-is-revolution-481"
            not in survivor_slugs
        ), "CadQuery should be stripped from asyncio post candidates"
        assert "why-solo-developers-should-embrace-docker" not in survivor_slugs
