"""Tests for the curated wiki-link knowledge graph (poindexter#1035).

The graph is DERIVED from operator-authored links, so the failure modes are
resolution failures that look like "the corpus has no links", and destructive
rebuilds that look like a successful run.
"""

from __future__ import annotations

import pytest

from services.knowledge_graph import (
    Edge,
    build_edges,
    extract_wikilinks,
    replace_edges,
    stem_of,
)


class TestExtractWikilinks:
    def test_finds_links(self):
        assert extract_wikilinks("see [[alpha]] and [[beta_two]]") == ["alpha", "beta_two"]

    def test_deduplicates_preserving_order(self):
        assert extract_wikilinks("[[b]] [[a]] [[b]]") == ["b", "a"]

    def test_ignores_ordinary_brackets_and_markdown_links(self):
        assert extract_wikilinks("[not a link] and [text](url.md)") == []

    def test_empty_input(self):
        assert extract_wikilinks("") == []
        assert extract_wikilinks(None) == []


class TestStemOf:
    def test_strips_path_and_md_extension(self):
        """The .md strip is load-bearing: source_ids keep the extension and
        wiki-links never do. Without it every link in the corpus dangles and
        the extractor reports zero edges — indistinguishable from 'no links'.
        Observed live: 922 dangling / 0 edges before this."""
        assert stem_of("claude-code/proj/always-commit-work.md") == "always-commit-work"

    def test_handles_missing_extension(self):
        assert stem_of("claude-code/proj/already-bare") == "already-bare"

    def test_lowercases(self):
        assert stem_of("a/b/MixedCase.md") == "mixedcase"

    def test_empty(self):
        assert stem_of("") == ""


class TestBuildEdges:
    def test_resolves_links_between_documents(self):
        docs = {
            "cc/p/a.md": "relates to [[b]]",
            "cc/p/b.md": "no links here",
        }
        edges, stats = build_edges(docs)
        assert len(edges) == 1
        assert edges[0].src_id == "cc/p/a.md"
        assert edges[0].dst_id == "cc/p/b.md"
        assert stats.dangling == 0

    def test_dangling_link_counted_not_dropped_silently(self):
        """A link to a not-yet-written memory is a marker of work to do in the
        memory convention, so it must be reported."""
        docs = {"cc/p/a.md": "see [[nonexistent]]"}
        edges, stats = build_edges(docs)
        assert edges == []
        assert stats.dangling == 1

    def test_self_link_excluded(self):
        """A self-edge would make graph expansion return the seed as its own
        neighbour."""
        docs = {"cc/p/a.md": "I am [[a]]"}
        edges, stats = build_edges(docs)
        assert edges == []
        assert stats.self_links == 1

    def test_largest_project_wins_when_a_stem_matches_several(self):
        """Several project keys can coexist after a machine migration. The
        bigger project is taken as the live one — derived from the corpus, not
        from a hardcoded list of this operator's paths (which would both leak
        into the public mirror and mis-resolve on anyone else's install)."""
        docs = {
            "claude-code/live-project/target.md": "x",
            "claude-code/live-project/other-a.md": "x",
            "claude-code/live-project/other-b.md": "x",
            "claude-code/retired-project/target.md": "x",
            "claude-code/live-project/src.md": "see [[target]]",
        }
        edges, _ = build_edges(docs)
        assert len(edges) == 1
        assert "retired-project" not in edges[0].dst_id

    def test_preference_is_stable_regardless_of_dict_order(self):
        base = {
            "claude-code/big/target.md": "x",
            "claude-code/big/filler.md": "x",
            "claude-code/small/target.md": "x",
            "claude-code/big/src.md": "see [[target]]",
        }
        first = build_edges(base)[0][0].dst_id
        reordered = dict(reversed(list(base.items())))
        assert build_edges(reordered)[0][0].dst_id == first

    def test_duplicate_links_between_same_pair_collapse(self):
        docs = {"cc/p/a.md": "[[b]] and again [[b]] later", "cc/p/b.md": ""}
        edges, _ = build_edges(docs)
        assert len(edges) == 1

    def test_stats_scanned_counts_documents(self):
        docs = {f"cc/p/{i}.md": "" for i in range(4)}
        _, stats = build_edges(docs)
        assert stats.scanned == 4


class _Conn:
    def __init__(self):
        self.deleted = False
        self.inserted = 0

    async def execute(self, sql, *a):
        if "DELETE" in sql:
            self.deleted = True

    async def executemany(self, sql, rows):
        self.inserted = len(rows)

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Acquire:
    def __init__(self, conn):
        self._c = conn

    async def __aenter__(self):
        return self._c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, conn):
        self._c = conn

    def acquire(self):
        return _Acquire(self._c)


class TestReplaceEdges:
    @pytest.mark.asyncio
    async def test_writes_edges(self):
        conn = _Conn()
        n = await replace_edges(_Pool(conn), [Edge("memory", "a", "memory", "b")])
        assert n == 1
        assert conn.deleted is True
        assert conn.inserted == 1

    @pytest.mark.asyncio
    async def test_empty_extraction_does_not_wipe_the_graph(self):
        """An extractor that produced nothing has failed. Deleting a working
        graph on that basis is the worst possible response — the same
        'a check that scanned nothing has not passed' rule the CI lints use."""
        conn = _Conn()
        n = await replace_edges(_Pool(conn), [])
        assert n == 0
        assert conn.deleted is False
        assert conn.inserted == 0
