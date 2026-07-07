from __future__ import annotations

import pytest

from modules.content.atoms.two_pass_writer import _parse_source_caps, _select_snippets


def _c(source, ref, relevance):
    # vec identical so MMR diversity term is 0 → selection is pure relevance order,
    # making the cap effect deterministic to assert.
    return {
        "source": source,
        "ref": ref,
        "snippet": f"s{ref}",
        "relevance": relevance,
        "vec": [1.0, 0.0],
    }


class _Cfg:
    def __init__(self, **kv):
        self._kv = kv

    def get(self, key, default=None):
        return self._kv.get(key, default)


@pytest.mark.unit
class TestSourceCaps:
    def test_posts_capped_sessions_fill(self):
        cands = [_c("posts", i, 0.99 - i * 0.001) for i in range(10)] + [
            _c("claude_sessions", 100 + i, 0.80 - i * 0.001) for i in range(10)
        ]
        out = _select_snippets(
            cands, k=6, dedup_ceiling=1.0, mmr_lambda=1.0, source_caps={"posts": 2}
        )
        srcs = [s["source"] for s in out]
        assert srcs.count("posts") == 2
        assert srcs.count("claude_sessions") == 4
        assert len(out) == 6

    def test_no_caps_is_unchanged(self):
        cands = [_c("posts", i, 0.9 - i * 0.01) for i in range(5)]
        out = _select_snippets(
            cands, k=3, dedup_ceiling=1.0, mmr_lambda=1.0, source_caps=None
        )
        assert len(out) == 3

    def test_cap_larger_than_supply_is_noop(self):
        cands = [_c("posts", i, 0.9 - i * 0.01) for i in range(2)]
        out = _select_snippets(
            cands, k=5, dedup_ceiling=1.0, mmr_lambda=1.0, source_caps={"posts": 10}
        )
        assert len(out) == 2


@pytest.mark.unit
class TestParseSourceCaps:
    def test_parses_csv(self):
        assert _parse_source_caps(_Cfg(writer_rag_source_caps="posts:2,foo:5")) == {
            "posts": 2,
            "foo": 5,
        }

    def test_empty_is_no_caps(self):
        assert _parse_source_caps(_Cfg(writer_rag_source_caps="")) == {}

    def test_malformed_entries_skipped(self):
        assert _parse_source_caps(
            _Cfg(writer_rag_source_caps="posts:2,junk,bad:x")
        ) == {"posts": 2}
