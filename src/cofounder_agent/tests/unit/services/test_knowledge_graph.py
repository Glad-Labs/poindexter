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
    build_post_edges,
    extract_post_links,
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


# ---------------------------------------------------------------------------
# Post internal links (poindexter#1036) — the coverage half of the graph
# ---------------------------------------------------------------------------


def _post(pid, slug, status="published", content=""):
    return {"id": pid, "slug": slug, "status": status, "content": content}


class TestExtractPostLinks:
    def test_finds_posts_prefix(self):
        assert extract_post_links("see [x](/posts/alpha-beta)") == ["alpha-beta"]

    def test_finds_legacy_blog_prefix(self):
        """Older posts still carry /blog/ — 3 of them live on the site."""
        assert extract_post_links("see [x](/blog/legacy-one)") == ["legacy-one"]

    def test_strips_anchor_and_query(self):
        """/posts/x#section and /posts/x?utm= are the same document; treating
        them as distinct would fabricate edges to slugs that do not exist."""
        got = extract_post_links("[a](/posts/thing#part) [b](/posts/thing?utm=x)")
        assert got == ["thing"]

    def test_ignores_external_and_affiliate_links(self):
        """External http dominates the corpus (331 vs 116), and /go/ is the
        affiliate redirector — neither is a content relationship."""
        body = "[a](https://example.com/posts/x) [b](/go/some-offer) [c](#anchor)"
        assert extract_post_links(body) == []

    def test_deduplicates_preserving_order(self):
        assert extract_post_links("[a](/posts/b) [c](/posts/a) [d](/posts/b)") == ["b", "a"]

    def test_trailing_slash_normalized(self):
        assert extract_post_links("[a](/posts/thing/)") == ["thing"]


class TestBuildPostEdges:
    def test_resolves_published_to_published(self):
        posts = [
            _post("1", "src", content="see [x](/posts/dst)"),
            _post("2", "dst"),
        ]
        edges, stats = build_post_edges(posts)
        assert len(edges) == 1
        assert (edges[0].src_id, edges[0].dst_id) == ("1", "2")
        assert edges[0].src_table == edges[0].dst_table == "posts"
        assert edges[0].origin == "post_internal_link"
        assert stats.dead_slug == 0

    def test_unpublished_target_skipped_and_counted(self):
        """PostsTap indexes published posts only, so an edge to a draft points
        at a row retrieval cannot return — it would waste an expansion slot."""
        posts = [
            _post("1", "src", content="[x](/posts/draft)"),
            _post("2", "draft", status="draft"),
        ]
        edges, stats = build_post_edges(posts)
        assert edges == []
        assert stats.unpublished_target == 1
        assert stats.dead_slug == 0

    def test_dead_slug_counted_separately_from_unpublished(self):
        """A slug that exists nowhere is a live 404 on the public site; a slug
        that exists but is unpublished is not. Collapsing them would hide the
        first behind the second."""
        posts = [_post("1", "src", content="[x](/posts/nope)")]
        edges, stats = build_post_edges(posts)
        assert edges == []
        assert stats.dead_slug == 1
        assert stats.unpublished_target == 0

    def test_unpublished_source_is_not_scanned(self):
        posts = [
            _post("1", "src", status="draft", content="[x](/posts/dst)"),
            _post("2", "dst"),
        ]
        edges, stats = build_post_edges(posts)
        assert edges == []
        assert stats.scanned == 1  # only the published dst counted as scanned

    def test_self_link_excluded(self):
        posts = [_post("1", "me", content="[x](/posts/me)")]
        edges, stats = build_post_edges(posts)
        assert edges == []
        assert stats.self_links == 1

    def test_duplicate_links_collapse_to_one_edge(self):
        posts = [
            _post("1", "src", content="[a](/posts/dst) then [b](/posts/dst)"),
            _post("2", "dst"),
        ]
        edges, _ = build_post_edges(posts)
        assert len(edges) == 1

    def test_slug_matching_is_case_insensitive(self):
        posts = [
            _post("1", "src", content="[x](/posts/DST)"),
            _post("2", "dst"),
        ]
        edges, _ = build_post_edges(posts)
        assert len(edges) == 1

    def test_memory_and_post_origins_are_distinct(self):
        """replace_edges scopes its delete by origin, so the two extractors
        must never share one — a memory rebuild would wipe the post edges."""
        from services.knowledge_graph import ORIGIN_MEMORY, ORIGIN_POST

        assert ORIGIN_MEMORY != ORIGIN_POST


# ---------------------------------------------------------------------------
# rag_rerank_device misconfiguration must be loud, not silent
# ---------------------------------------------------------------------------


class TestRerankDeviceMisconfigIsLoud:
    """Setting rag_rerank_device to an unusable device used to degrade every
    query to hybrid-only behind a single WARNING. The cross-encoder more than
    doubles recall@1 (0.067 -> 0.150 on the golden set), so that is a material
    regression an operator has no reason to suspect — it must page."""

    def _retriever(self, device):
        from services.rag_engine import _build_rerank_retriever_class

        cls = _build_rerank_retriever_class()

        class _Cfg:
            def get(self, k, d=None):
                return device if k == "rag_rerank_device" else d

            def get_int(self, k, d=0):
                return d

        class _Inner:
            async def _aretrieve(self, qb):
                return [object()]

        return cls(inner=_Inner(), top_k=5, site_config=_Cfg())

    @staticmethod
    def _patch_findings(monkeypatch, sink):
        import sys

        monkeypatch.setitem(
            sys.modules, "utils.findings",
            type("m", (), {"emit_finding": staticmethod(lambda **kw: sink.update(kw))}),
        )

    class _QB:
        query_str = "q"

    @pytest.mark.asyncio
    async def test_cuda_on_cpu_only_torch_emits_a_finding(self, monkeypatch):
        emitted: dict = {}
        self._patch_findings(monkeypatch, emitted)
        r = self._retriever("cuda")
        monkeypatch.setattr(
            r, "_get_model",
            lambda: (_ for _ in ()).throw(
                AssertionError("Torch not compiled with CUDA enabled")
            ),
        )
        await r._aretrieve(self._QB())
        assert emitted.get("kind") == "rag_rerank_device_unusable"
        assert emitted.get("severity") == "warn"
        assert "cuda" in str(emitted.get("dedup_key", ""))

    @pytest.mark.asyncio
    async def test_unrelated_failure_stays_a_plain_warning(self, monkeypatch):
        """Only device errors page; an ordinary model hiccup keeps the old
        non-fatal warning so we do not spam findings on transient noise."""
        emitted: dict = {}
        self._patch_findings(monkeypatch, emitted)
        r = self._retriever("cpu")
        monkeypatch.setattr(
            r, "_get_model",
            lambda: (_ for _ in ()).throw(RuntimeError("transient blip")),
        )
        await r._aretrieve(self._QB())
        assert emitted == {}

    @pytest.mark.asyncio
    async def test_retrieval_still_returns_results_when_rerank_is_lost(self, monkeypatch):
        """Loud, but still non-fatal — the inner retriever's hits remain
        useful, just unordered by the cross-encoder."""
        emitted: dict = {}
        self._patch_findings(monkeypatch, emitted)
        r = self._retriever("cuda")
        monkeypatch.setattr(
            r, "_get_model",
            lambda: (_ for _ in ()).throw(AssertionError("no CUDA device")),
        )
        out = await r._aretrieve(self._QB())
        assert len(out) == 1
