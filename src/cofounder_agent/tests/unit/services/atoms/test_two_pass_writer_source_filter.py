from __future__ import annotations

import pytest

from modules.content.atoms.two_pass_writer import _resolve_snippet_source_filter


class _Cfg:
    def __init__(self, **kv):
        self._kv = kv

    def get(self, key, default=None):
        return self._kv.get(key, default)


@pytest.mark.unit
class TestResolveSnippetSourceFilter:
    def test_prefers_writer_setting(self):
        cfg = _Cfg(
            writer_rag_source_filter="claude_sessions,posts",
            rag_source_filter="posts",
        )
        assert _resolve_snippet_source_filter(cfg) == ["claude_sessions", "posts"]

    def test_falls_back_to_general_when_writer_empty(self):
        cfg = _Cfg(writer_rag_source_filter="", rag_source_filter="posts")
        assert _resolve_snippet_source_filter(cfg) == ["posts"]

    def test_falls_back_to_posts_when_both_empty(self):
        cfg = _Cfg(writer_rag_source_filter="", rag_source_filter="")
        assert _resolve_snippet_source_filter(cfg) == ["posts"]

    def test_none_site_config_defaults_to_posts(self):
        assert _resolve_snippet_source_filter(None) == ["posts"]
