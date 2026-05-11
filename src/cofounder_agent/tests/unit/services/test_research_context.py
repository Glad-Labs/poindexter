"""Unit tests for services/research_context.build_rag_context (poindexter#470).

The bug: ``research_context.py`` resolves slug + excerpt for similarity
hits via ``SELECT slug, excerpt FROM posts WHERE id::text = $1`` — with
no ``status = 'published'`` filter. Drafts / rejected / awaiting_approval
embeddings happily index into pgvector, so a similarity hit alone is not
proof the target post is live. Without the filter, a draft could ship
with a link pointing at a slug that 404s on the live site.

These tests pin the contract:

  1. Only ``status='published'`` posts come back as link candidates.
  2. Self-link suppression: the current post's id is short-circuited.
  3. Empty result when no published candidates exist.

The asyncpg pool double records every fetchrow query + binding so the
``status = 'published'`` clause can be asserted at the SQL level too.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Test doubles — MemoryClient stub + asyncpg pool double
# ---------------------------------------------------------------------------


class _FakeHit:
    """Minimal stand-in for ``poindexter.memory.MemoryHit``.

    The production dataclass has ``source_table``, ``source_id``,
    ``similarity``, ``text_preview``, ``writer``, ``origin_path``, plus
    ``metadata``. ``build_rag_context`` only reads ``source_id``,
    ``similarity`` and ``metadata`` so the stub keeps the surface small.
    """

    def __init__(
        self, source_id: str, similarity: float = 0.85, title: str = "Untitled"
    ) -> None:
        self.source_id = source_id
        self.similarity = similarity
        self.metadata = {"title": title}


@pytest.fixture
def stub_memory_client(monkeypatch):
    """Inject a ``poindexter.memory.MemoryClient`` whose
    ``find_similar_posts`` returns a controllable list of ``_FakeHit``.

    The stub also tracks how many times ``find_similar_posts`` is called
    so tests can be sure the seam fired before the slug-resolution
    query.
    """
    holder: dict[str, Any] = {
        "find_similar_posts_result": [],
        "calls": 0,
    }

    class _StubClient:
        def __init__(self, *_a, **_kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def find_similar_posts(
            self, topic: str, *, limit: int = 5, min_similarity: float = 0.75
        ) -> list[_FakeHit]:
            holder["calls"] += 1
            holder["last_topic"] = topic
            holder["last_limit"] = limit
            return list(holder["find_similar_posts_result"])

    fake_module = types.ModuleType("poindexter.memory")
    fake_module.MemoryClient = _StubClient

    grandparent = sys.modules.get("poindexter") or types.ModuleType("poindexter")
    grandparent.memory = fake_module

    monkeypatch.setitem(sys.modules, "poindexter", grandparent)
    monkeypatch.setitem(sys.modules, "poindexter.memory", fake_module)

    return holder


class _PoolDouble:
    """Hand-rolled asyncpg pool double for ``build_rag_context``.

    Two things to assert: the SQL string includes ``status = 'published'``,
    and the bound row only comes back when the seeded post-status table
    says ``published``. The double is keyed by post id (UUID-as-string).
    """

    def __init__(self, posts: dict[str, dict[str, Any]]) -> None:
        # ``posts`` shape: {post_id: {"slug": ..., "excerpt": ..., "status": ...}}
        self._posts = posts
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, sql: str, *args: Any):
        # Record every fetchrow for SQL-level assertions.
        self.fetchrow_calls.append((sql, args))

        # The production query selects ``slug, excerpt`` from posts where
        # ``id::text = $1 AND status = 'published'``. Replicate that
        # filter against the seeded table.
        post_id = args[0]
        row = self._posts.get(post_id)
        if row is None:
            return None
        if "status = 'published'" in sql and row.get("status") != "published":
            return None
        # asyncpg.Record exposes .get(); a dict suffices for the test.
        return {"slug": row.get("slug", ""), "excerpt": row.get("excerpt", "")}


class _DatabaseServiceDouble:
    def __init__(self, pool: _PoolDouble) -> None:
        self.pool = pool


# ---------------------------------------------------------------------------
# build_rag_context — status-filter regression tests (poindexter#470)
# ---------------------------------------------------------------------------


class TestBuildRagContextStatusFilter:
    """Only ``status='published'`` posts may surface as link candidates."""

    @pytest.mark.asyncio
    async def test_returns_only_published_candidate(self, stub_memory_client):
        """Seed 4 posts (one published, three non-published) — only the
        published one survives the slug-resolution filter.

        Matches the acceptance criterion in the issue body: ``rejected``
        / ``awaiting_approval`` / ``rejected_final`` (treated as any
        non-published status here) MUST NOT show up in the candidate
        list, even when they're a closer similarity match.
        """
        # Four embedding hits — only one is published. The non-published
        # ones are ordered first to verify they're dropped, not just
        # de-prioritised.
        stub_memory_client["find_similar_posts_result"] = [
            _FakeHit("11111111-1111-1111-1111-111111111111", 0.95, "Rejected Post"),
            _FakeHit("22222222-2222-2222-2222-222222222222", 0.90, "Pending Post"),
            _FakeHit("33333333-3333-3333-3333-333333333333", 0.85, "Final Reject"),
            _FakeHit("44444444-4444-4444-4444-444444444444", 0.80, "Live Post"),
        ]

        pool = _PoolDouble({
            "11111111-1111-1111-1111-111111111111": {
                "slug": "rejected-slug",
                "excerpt": "rejected excerpt",
                "status": "rejected",
            },
            "22222222-2222-2222-2222-222222222222": {
                "slug": "pending-slug",
                "excerpt": "pending excerpt",
                "status": "awaiting_approval",
            },
            "33333333-3333-3333-3333-333333333333": {
                "slug": "final-reject-slug",
                "excerpt": "rejected_final excerpt",
                "status": "rejected_final",
            },
            "44444444-4444-4444-4444-444444444444": {
                "slug": "live-published-slug",
                "excerpt": "the only live one",
                "status": "published",
            },
        })

        from services.research_context import build_rag_context

        result = await build_rag_context(
            _DatabaseServiceDouble(pool),
            topic="topic that everything is similar to",
        )

        assert result is not None
        # Only the live post's slug should appear in the RAG context.
        assert "/posts/live-published-slug" in result
        assert "/posts/rejected-slug" not in result
        assert "/posts/pending-slug" not in result
        assert "/posts/final-reject-slug" not in result

    @pytest.mark.asyncio
    async def test_sql_includes_status_filter(self, stub_memory_client):
        """Pin the SQL: every fetchrow against ``posts`` must filter by
        ``status = 'published'``. This is the contract regression-tested
        by poindexter#470 — losing the filter is exactly what caused the
        bug.
        """
        stub_memory_client["find_similar_posts_result"] = [
            _FakeHit("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 0.9, "Title"),
        ]
        pool = _PoolDouble({
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {
                "slug": "x",
                "excerpt": "y",
                "status": "published",
            },
        })

        from services.research_context import build_rag_context

        await build_rag_context(
            _DatabaseServiceDouble(pool),
            topic="any topic",
        )

        assert pool.fetchrow_calls, "expected at least one fetchrow"
        for sql, _ in pool.fetchrow_calls:
            assert "status = 'published'" in sql, (
                "every slug-resolution query MUST filter by status='published'; "
                f"saw: {sql!r}"
            )

    @pytest.mark.asyncio
    async def test_returns_none_when_no_published_candidates(
        self, stub_memory_client
    ):
        """When every similarity hit resolves to a non-published row,
        the function returns ``None`` instead of a half-empty RAG block.
        """
        stub_memory_client["find_similar_posts_result"] = [
            _FakeHit("11111111-1111-1111-1111-111111111111", 0.9, "Rejected"),
            _FakeHit("22222222-2222-2222-2222-222222222222", 0.85, "Pending"),
        ]
        pool = _PoolDouble({
            "11111111-1111-1111-1111-111111111111": {
                "slug": "r1",
                "excerpt": "",
                "status": "rejected",
            },
            "22222222-2222-2222-2222-222222222222": {
                "slug": "r2",
                "excerpt": "",
                "status": "awaiting_approval",
            },
        })

        from services.research_context import build_rag_context

        result = await build_rag_context(
            _DatabaseServiceDouble(pool),
            topic="any topic",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_similar_posts(self, stub_memory_client):
        """No similarity hits → no RAG block (defensive baseline)."""
        stub_memory_client["find_similar_posts_result"] = []
        pool = _PoolDouble({})

        from services.research_context import build_rag_context

        result = await build_rag_context(
            _DatabaseServiceDouble(pool),
            topic="any topic",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_post_prefix_id_is_stripped(self, stub_memory_client):
        """auto-embed.py stores ``source_id`` as either ``<uuid>`` or
        ``post/<uuid>``. The slug-resolution query must strip the
        prefix before binding, or the status filter would never match.
        """
        stub_memory_client["find_similar_posts_result"] = [
            _FakeHit("post/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 0.9, "Title"),
        ]
        pool = _PoolDouble({
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {
                "slug": "stripped-prefix-slug",
                "excerpt": "",
                "status": "published",
            },
        })

        from services.research_context import build_rag_context

        result = await build_rag_context(
            _DatabaseServiceDouble(pool),
            topic="any topic",
        )

        assert result is not None
        assert "stripped-prefix-slug" in result
        # The bind value must be the bare UUID, not the prefixed form.
        bound_ids = [args[0] for _, args in pool.fetchrow_calls]
        assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" in bound_ids


# ---------------------------------------------------------------------------
# build_rag_context — self-link suppression (poindexter#470)
# ---------------------------------------------------------------------------


class TestBuildRagContextSelfLink:
    """A post never appears in its own candidate list."""

    @pytest.mark.asyncio
    async def test_self_link_dropped_before_db(self, stub_memory_client):
        """When ``current_post_id`` matches a hit's ``source_id`` the
        candidate is dropped before the slug-resolution query — saves a
        DB round-trip and prevents a self-link even if the post is
        published.
        """
        stub_memory_client["find_similar_posts_result"] = [
            _FakeHit("11111111-1111-1111-1111-111111111111", 0.99, "Self Post"),
            _FakeHit("22222222-2222-2222-2222-222222222222", 0.80, "Other Post"),
        ]
        pool = _PoolDouble({
            "11111111-1111-1111-1111-111111111111": {
                "slug": "self-post-slug",
                "excerpt": "",
                "status": "published",
            },
            "22222222-2222-2222-2222-222222222222": {
                "slug": "other-post-slug",
                "excerpt": "",
                "status": "published",
            },
        })

        from services.research_context import build_rag_context

        result = await build_rag_context(
            _DatabaseServiceDouble(pool),
            topic="topic that matches",
            current_post_id="11111111-1111-1111-1111-111111111111",
        )

        assert result is not None
        assert "/posts/other-post-slug" in result
        assert "/posts/self-post-slug" not in result
        # Only one fetchrow — the self-link short-circuit avoided the DB.
        bound_ids = [args[0] for _, args in pool.fetchrow_calls]
        assert "11111111-1111-1111-1111-111111111111" not in bound_ids
        assert "22222222-2222-2222-2222-222222222222" in bound_ids

    @pytest.mark.asyncio
    async def test_self_link_handles_post_prefix(self, stub_memory_client):
        """Source ids in the form ``post/<uuid>`` are normalized for
        the self-link comparison, regardless of which prefix shape the
        caller supplies as ``current_post_id``.
        """
        stub_memory_client["find_similar_posts_result"] = [
            _FakeHit("post/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 0.99, "Self"),
            _FakeHit("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 0.80, "Other"),
        ]
        pool = _PoolDouble({
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {
                "slug": "self-slug",
                "excerpt": "",
                "status": "published",
            },
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": {
                "slug": "other-slug",
                "excerpt": "",
                "status": "published",
            },
        })

        from services.research_context import build_rag_context

        # Pass current_post_id WITHOUT the prefix — must still match.
        result = await build_rag_context(
            _DatabaseServiceDouble(pool),
            topic="topic",
            current_post_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        )

        assert result is not None
        assert "/posts/other-slug" in result
        assert "/posts/self-slug" not in result

    @pytest.mark.asyncio
    async def test_no_current_post_id_keeps_all_hits(self, stub_memory_client):
        """Backwards-compat: when ``current_post_id`` is None (the
        default), no self-link gate is applied and every published hit
        survives.
        """
        stub_memory_client["find_similar_posts_result"] = [
            _FakeHit("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 0.9, "A"),
            _FakeHit("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 0.8, "B"),
        ]
        pool = _PoolDouble({
            "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": {
                "slug": "slug-a",
                "excerpt": "",
                "status": "published",
            },
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": {
                "slug": "slug-b",
                "excerpt": "",
                "status": "published",
            },
        })

        from services.research_context import build_rag_context

        result = await build_rag_context(
            _DatabaseServiceDouble(pool),
            topic="topic",
        )

        assert result is not None
        assert "/posts/slug-a" in result
        assert "/posts/slug-b" in result
