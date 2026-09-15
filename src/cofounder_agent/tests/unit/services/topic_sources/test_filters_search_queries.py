"""Direct coverage for the search-query junk helpers in ``_filters``.

These are shared: any TopicSource that mines search queries should route through
them, so they get tested independently of the one source using them today.

Motivating audit (2026-08-09): of the GSC impressions Google will name, over
half came from a single cluster of 103 reorderings of "cadquery official
documentation parametric cad python" — 3,696 impressions, zero clicks — and the
live topic source's output included `site:www.gladlabs.io`.
"""
from __future__ import annotations

import pytest

from poindexter.services.topic_sources._filters import (
    is_junk_search_query,
    is_news_or_junk,
    permutation_clusters,
)

BRAND = ("glad labs", "gladlabs.io", "gladlabs")


class TestIsJunkSearchQuery:
    @pytest.mark.parametrize("q", [
        "site:www.gladlabs.io", "site: gladlabs.io", "SITE:example.com",
        "inurl:posts", "intitle:llm", "filetype:pdf", "cache:gladlabs.io",
        "related:news.ycombinator.com", "allintitle:vram",
    ])
    def test_search_operators_are_junk(self, q):
        assert is_junk_search_query(q) is True

    @pytest.mark.parametrize("q", [
        "https://www.gladlabs.io/about", "http://example.com",
        "www.gladlabs.io", "zhanymkanov/fastapi-best-practices",
    ])
    def test_urls_and_paths_are_junk(self, q):
        assert is_junk_search_query(q) is True

    @pytest.mark.parametrize("q", ["8000-6400", "2026", "   ", "", "-- --", "128 256"])
    def test_letterless_fragments_are_junk(self, q):
        assert is_junk_search_query(q) is True

    @pytest.mark.parametrize("q", ["glad labs", "Glad Labs blog", "gladlabs", "gladlabs.io reviews"])
    def test_brand_navigation_is_junk(self, q):
        assert is_junk_search_query(q, brand_tokens=BRAND) is True

    def test_brand_matching_requires_brand_tokens(self):
        """Without config there is no brand to know about — must not guess."""
        assert is_junk_search_query("glad labs") is False

    @pytest.mark.parametrize("q", [
        "5090 local llm",                    # 7.1% CTR in prod
        "fast api best practices",           # 20% CTR in prod
        "dot product vs cosine similarity",
        "content pipeline automation",
        "automated infrastructure monitoring",
        "socket.io tutorial",                # bare-domain rule would kill this
        "next.js routing",
        "ddr5 6400 vs 8000",                 # has letters, keep
    ])
    def test_real_queries_survive(self, q):
        assert is_junk_search_query(q, brand_tokens=BRAND) is False

    def test_short_queries_survive_unlike_the_title_filter(self):
        """``is_news_or_junk`` rejects <4 words because it grades article
        titles. Queries are short by nature and the short ones convert best —
        reusing the title filter would discard the best traffic."""
        q = "5090 local llm"
        assert is_news_or_junk(q) is True          # the title filter would drop it
        assert is_junk_search_query(q) is False    # the query filter keeps it


class TestPermutationClusters:
    def test_detects_a_reordering_cluster(self):
        words = "cadquery official documentation parametric cad python".split()
        variants = [" ".join(words[i:] + words[:i]) for i in range(len(words))]
        assert permutation_clusters(variants, min_variants=5) == set(variants)

    def test_whole_cluster_is_returned_not_just_the_extras(self):
        """Callers drop the returned set, so it must include the first variant
        too — otherwise one machine query always survives."""
        v = ["a b c", "b c a", "c a b", "a c b", "b a c"]
        assert permutation_clusters(v, min_variants=5) == set(v)

    def test_two_phrasings_are_not_a_cluster(self):
        v = ["local llm vram requirements", "vram requirements local llm"]
        assert permutation_clusters(v, min_variants=5) == set()

    def test_threshold_is_respected(self):
        v = ["local llm vram", "vram local llm"]
        assert permutation_clusters(v, min_variants=2) == set(v)

    def test_distinct_topics_are_untouched(self):
        v = ["speculative decoding", "kv cache quantization", "ddr5 scaling"]
        assert permutation_clusters(v, min_variants=2) == set()

    def test_case_and_whitespace_insensitive(self):
        v = ["Local LLM  VRAM", "vram local   llm", "llm vram local"]
        assert permutation_clusters(v, min_variants=3) == set(v)

    @pytest.mark.parametrize("bad", [0, 1, -3])
    def test_degenerate_threshold_disables_clustering(self, bad):
        """min_variants < 2 would mark every single query as its own cluster
        and empty the pipeline."""
        assert permutation_clusters(["anything at all"], min_variants=bad) == set()

    def test_empty_and_blank_queries_are_ignored(self):
        assert permutation_clusters(["", "   ", None], min_variants=2) == set()


class TestImageSearchIntentIsNotReaderDemand:
    """Image searches bank impressions off a post's photos and never a click.

    Google Images answers them with the picture itself, so no article can
    satisfy the searcher. Proposing one writes for nobody -- the same
    zero-click shape the operator/permutation rules already reject.

    Added 2026-09-15: "ddr5 ram module close up" (21 impressions, average
    position 91.9) survived every existing rule and was the one piece of junk
    in the recalibrated gsc_query_gap candidate set.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "ddr5 ram module close up",
            "ddr5 ram module close-up",
            "ddr5 ram closeup pins",
            "amd ryzen 9 cpu processor close-up golden pins",
            "rtx 5090 wallpaper",
            "server rack wallpapers",
            "gpu clipart",
            "datacenter stock photo",
            "rack stock images",
            "royalty free server photo",
            "gpu diagram 1920x1080",
        ],
    )
    def test_image_intent_is_junk(self, query):
        assert is_junk_search_query(query) is True

    @pytest.mark.parametrize(
        "query",
        [
            # The reason the rule does NOT key on bare image/photo/picture:
            # every one of these is a legitimate topic for an AI/infra site.
            "docker image size optimization",
            "ai image generation local",
            "image generation model comparison",
            "how to cache a docker image layer",
            "flux image model vram",
            "picture in picture video ffmpeg",
            "photo metadata exif python",
            # And the real on-topic gaps the source is meant to surface.
            "content pipeline automation",
            "ai content pipeline",
            "automated infrastructure monitoring",
            "local rag pipeline",
            "fastapi best practices",
        ],
    )
    def test_legitimate_queries_survive(self, query):
        assert is_junk_search_query(query) is False
