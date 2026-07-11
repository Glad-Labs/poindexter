"""Tests for the optional LlamaIndex routing in MemoryClient.search.

Lane D #329 sub-issue 4 — when ``app_settings.rag_engine_enabled =
'true'`` AND no ``writer`` filter is supplied, ``MemoryClient.search``
delegates to ``services.rag_engine.get_rag_retriever``. Otherwise the
legacy inline-pgvector path runs unchanged.

These tests stub the rag_engine module + the MemoryClient pool so we
can exercise the routing logic without a real DB or llama-index
runtime.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from poindexter.memory.client import MemoryClient, MemoryHit


def _make_client_with_pool(pool):
    """Construct a MemoryClient with internals pre-wired to a fake pool.

    Skips the real connect() path so we don't touch the network. The
    DSN is bogus — `_require_pool` short-circuits because `_pool` is
    already set.
    """
    client = MemoryClient(dsn="postgresql://stub:stub@localhost/stub")
    client._pool = pool
    return client


class _FakePoolWithSetting:
    """Minimal asyncpg-pool stand-in that returns rag_engine_enabled."""

    def __init__(self, value: str | None):
        self._value = value

    def acquire(self):
        outer = self

        class _Conn:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *_a):
                return False

            async def fetchrow(self_inner, sql, *_args):
                if "rag_engine_enabled" in sql:
                    return (
                        {"value": outer._value} if outer._value is not None else None
                    )
                return None

            async def fetch(self_inner, *_a, **_k):
                return []

        return _Conn()


@pytest.mark.unit
class TestRagEngineEnabledFlag:
    @pytest.mark.asyncio
    async def test_default_off_when_setting_missing(self):
        client = _make_client_with_pool(_FakePoolWithSetting(None))
        assert await client._rag_engine_enabled() is False

    @pytest.mark.asyncio
    async def test_true_setting_enables(self):
        client = _make_client_with_pool(_FakePoolWithSetting("true"))
        assert await client._rag_engine_enabled() is True

    @pytest.mark.asyncio
    async def test_false_setting_disables(self):
        client = _make_client_with_pool(_FakePoolWithSetting("false"))
        assert await client._rag_engine_enabled() is False

    @pytest.mark.asyncio
    async def test_arbitrary_truthy_strings_accepted(self):
        for value in ("True", "1", "yes", "on", "TRUE"):
            client = _make_client_with_pool(_FakePoolWithSetting(value))
            assert await client._rag_engine_enabled() is True, value


@pytest.mark.unit
class TestSearchRoutingThroughRagEngine:
    """Search routing logic — when does MemoryClient hit rag_engine vs legacy?"""

    @pytest.mark.asyncio
    async def test_writer_filter_skips_rag_engine(self):
        """The retriever has no writer filter today, so writer-filtered
        queries always fall through to the legacy pgvector path."""
        client = _make_client_with_pool(_FakePoolWithSetting("true"))
        with patch.object(
            client,
            "_search_via_rag_engine",
            new=AsyncMock(),
        ) as rag_mock, patch.object(
            client, "embed", new=AsyncMock(return_value=[0.0] * 768),
        ):
            await client.search("query", writer="claude-code", limit=5)

        rag_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_skips_rag_engine(self):
        client = _make_client_with_pool(_FakePoolWithSetting("false"))
        with patch.object(
            client,
            "_search_via_rag_engine",
            new=AsyncMock(),
        ) as rag_mock, patch.object(
            client, "embed", new=AsyncMock(return_value=[0.0] * 768),
        ):
            await client.search("query", limit=5)

        rag_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_enabled_routes_through_rag_engine(self):
        client = _make_client_with_pool(_FakePoolWithSetting("true"))
        expected = [
            MemoryHit(
                source_table="memory",
                source_id="m1",
                similarity=0.92,
                text_preview="hello",
                writer=None,
                origin_path=None,
                metadata={},
            )
        ]
        with patch.object(
            client,
            "_search_via_rag_engine",
            new=AsyncMock(return_value=expected),
        ) as rag_mock:
            result = await client.search("query", limit=5)

        rag_mock.assert_called_once()
        assert result == expected

    @pytest.mark.asyncio
    async def test_rag_engine_failure_falls_back_to_legacy(self):
        """Loud fallback per `feedback_no_silent_defaults`. Search
        keeps working (fallback to legacy) BUT all three surfaces
        fire so the regression can't hide:
          1. WARNING log
          2. audit_log row
          3. notify_operator
        """
        client = _make_client_with_pool(_FakePoolWithSetting("true"))
        notify_mock = AsyncMock()
        with patch.object(
            client,
            "_search_via_rag_engine",
            new=AsyncMock(side_effect=RuntimeError("llama exploded")),
        ), patch.object(
            client, "embed", new=AsyncMock(return_value=[0.0] * 768),
        ), patch(
            "services.audit_log.audit_log_bg",
        ) as audit_mock, patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify_mock,
        ):
            # Should not raise — fall through to legacy path which
            # returns [] given our fake pool's empty fetch.
            result = await client.search("query", limit=5)

        assert result == []
        # Surface 2: audit_log fired with the right event_type.
        audit_mock.assert_called_once()
        args, kwargs = audit_mock.call_args
        assert args[0] == "rag_engine_fallback"
        assert kwargs.get("severity") == "warning"
        assert "exception_type" in args[2]
        assert args[2]["exception_type"] == "RuntimeError"
        # Surface 3: operator notification fired (non-critical).
        notify_mock.assert_called_once()
        notify_args, notify_kwargs = notify_mock.call_args
        assert "rag_engine fallback" in notify_args[0]
        assert "RuntimeError" in notify_args[0]
        assert notify_kwargs.get("critical") is False

    @pytest.mark.asyncio
    async def test_fallback_survives_audit_logger_uninitialised(self):
        """Surfaces are independent: if audit_log_bg raises (logger not
        wired up yet), the operator notification still fires and the
        search still returns results. No surface can suppress another."""
        client = _make_client_with_pool(_FakePoolWithSetting("true"))
        notify_mock = AsyncMock()
        with patch.object(
            client,
            "_search_via_rag_engine",
            new=AsyncMock(side_effect=RuntimeError("llama exploded")),
        ), patch.object(
            client, "embed", new=AsyncMock(return_value=[0.0] * 768),
        ), patch(
            "services.audit_log.audit_log_bg",
            side_effect=RuntimeError("audit not initialised"),
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            new=notify_mock,
        ):
            result = await client.search("query", limit=5)

        assert result == []
        notify_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_survives_notify_failure(self):
        """If notify_operator itself raises, search must still work
        and the legacy path must still run. notify failure is logged
        at debug, never re-raised."""
        client = _make_client_with_pool(_FakePoolWithSetting("true"))
        with patch.object(
            client,
            "_search_via_rag_engine",
            new=AsyncMock(side_effect=RuntimeError("llama exploded")),
        ), patch.object(
            client, "embed", new=AsyncMock(return_value=[0.0] * 768),
        ), patch(
            "services.audit_log.audit_log_bg",
        ), patch(
            "services.integrations.operator_notify.notify_operator",
            new=AsyncMock(side_effect=RuntimeError("discord webhook down")),
        ):
            # Must not raise.
            result = await client.search("query", limit=5)
        assert result == []


@pytest.mark.unit
class TestRagEngineHitConversion:
    """``_search_via_rag_engine`` converts NodeWithScore → MemoryHit."""

    @pytest.mark.asyncio
    async def test_metadata_round_trips(self):
        """writer + origin_path land on the MemoryHit (via retriever
        metadata), and the remaining metadata stays intact."""
        client = _make_client_with_pool(_FakePoolWithSetting("true"))

        # Stub get_rag_retriever to return a retriever whose aretrieve
        # yields one fake node.
        node = SimpleNamespace(
            text="example text",
            metadata={
                "source_table": "memory",
                "source_id": "m1",
                "writer": "claude-code",
                "origin_path": "/projects/foo",
                "extra": "kept",
            },
        )
        nws = SimpleNamespace(node=node, score=0.87)

        retriever = SimpleNamespace(
            aretrieve=AsyncMock(return_value=[nws]),
        )
        with patch(
            "services.rag_engine.get_rag_retriever",
            new=AsyncMock(return_value=retriever),
        ):
            hits = await client._search_via_rag_engine(
                "query",
                source_table=None,
                min_similarity=0.0,
                limit=5,
            )

        assert len(hits) == 1
        h = hits[0]
        assert h.source_table == "memory"
        assert h.source_id == "m1"
        assert h.text_preview == "example text"
        assert h.writer == "claude-code"
        assert h.origin_path == "/projects/foo"
        assert h.similarity == pytest.approx(0.87)
        # source_table/id/writer/origin_path were extracted; extra
        # metadata stays in the metadata dict.
        assert h.metadata == {"extra": "kept"}

    @pytest.mark.asyncio
    async def test_empty_retriever_returns_empty_list(self):
        client = _make_client_with_pool(_FakePoolWithSetting("true"))
        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=[]))
        with patch(
            "services.rag_engine.get_rag_retriever",
            new=AsyncMock(return_value=retriever),
        ):
            hits = await client._search_via_rag_engine(
                "query",
                source_table=None,
                min_similarity=0.0,
                limit=5,
            )
        assert hits == []

    @pytest.mark.asyncio
    async def test_source_table_passed_as_source_filter(self):
        client = _make_client_with_pool(_FakePoolWithSetting("true"))
        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=[]))
        get_mock = AsyncMock(return_value=retriever)
        with patch("services.rag_engine.get_rag_retriever", new=get_mock):
            await client._search_via_rag_engine(
                "query",
                source_table="posts",
                min_similarity=0.5,
                limit=10,
            )
        kwargs = get_mock.call_args.kwargs
        assert kwargs["source_filter"] == ["posts"]
        assert kwargs["min_similarity"] == 0.5
        assert kwargs["top_k"] == 10


# ---------------------------------------------------------------------------
# 2026-05-27 — hybrid+rerank flag propagation tests
#
# Background: until 2026-05-27, `_search_via_rag_engine` called
# `get_rag_retriever(pool, top_k=..., ...)` WITHOUT passing the
# `site_config` arg or explicit `hybrid` / `rerank` flags. The factory
# then fell into its "no site_config" branch and forced both flags
# to False — even on prod where `rag_engine_enabled=true` AND
# `rag_hybrid_enabled=true` AND `rag_rerank_enabled=true` were all
# set. The BM25+RRF and cross-encoder rerank wrappers shipped 2026-05-10
# but never instantiated in production. The fix: read the flags directly
# from app_settings inside MemoryClient and pass them explicitly.
# ---------------------------------------------------------------------------


class _FakePoolWithMultiSettings:
    """Like _FakePoolWithSetting but returns multiple key/value pairs.

    Used to exercise `_rag_extras_flags`, which reads two rows
    (rag_hybrid_enabled + rag_rerank_enabled) in a single SELECT.
    """

    def __init__(self, values: dict[str, str]):
        self._values = values

    def acquire(self):
        outer = self

        class _Conn:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *_a):
                return False

            async def fetchrow(self_inner, sql, *_args):
                if "rag_engine_enabled" in sql:
                    v = outer._values.get("rag_engine_enabled")
                    return {"value": v} if v is not None else None
                return None

            async def fetch(self_inner, sql, *_args):
                # The extras read selects both hybrid + rerank rows.
                if "rag_hybrid_enabled" in sql or "rag_rerank_enabled" in sql:
                    return [
                        {"key": k, "value": v}
                        for k, v in outer._values.items()
                        if k in ("rag_hybrid_enabled", "rag_rerank_enabled")
                    ]
                return []

        return _Conn()


@pytest.mark.unit
class TestRagExtrasFlagsRead:
    """`_rag_extras_flags` returns (hybrid_enabled, rerank_enabled) from
    app_settings. Both default False when unset — same safe-degrade
    as the rest of the rag_engine machinery."""

    @pytest.mark.asyncio
    async def test_both_true(self):
        client = _make_client_with_pool(_FakePoolWithMultiSettings({
            "rag_hybrid_enabled": "true",
            "rag_rerank_enabled": "true",
        }))
        assert await client._rag_extras_flags() == (True, True)

    @pytest.mark.asyncio
    async def test_hybrid_only(self):
        client = _make_client_with_pool(_FakePoolWithMultiSettings({
            "rag_hybrid_enabled": "true",
            "rag_rerank_enabled": "false",
        }))
        assert await client._rag_extras_flags() == (True, False)

    @pytest.mark.asyncio
    async def test_rerank_only(self):
        client = _make_client_with_pool(_FakePoolWithMultiSettings({
            "rag_hybrid_enabled": "false",
            "rag_rerank_enabled": "true",
        }))
        assert await client._rag_extras_flags() == (False, True)

    @pytest.mark.asyncio
    async def test_both_missing_defaults_false(self):
        client = _make_client_with_pool(_FakePoolWithMultiSettings({}))
        assert await client._rag_extras_flags() == (False, False)


# ---------------------------------------------------------------------------
# 2026-07-11 — embed base_url propagation
#
# Background: the 2026-05-27 fix (above) recovered hybrid+rerank from the
# retriever's "no site_config" branch but missed `base_url`. That branch
# hardcodes `http://localhost:11434`, which inside the worker container has
# no Ollama (it runs on host.docker.internal:11434 — the `local_llm_api_url`
# value). So every `_search_via_rag_engine` embed failed ("Failed to connect
# to Ollama") and find_similar_posts returned zero hits — the internal-linking
# RAG layer + the pre-generation semantic-dedup check were both silently dead
# (~1 RAG injection in 27 runs). Same read-from-pool-and-pass fix as the flags.
# ---------------------------------------------------------------------------


class _FakePoolWithNamedSetting:
    """Returns a single app_settings value when the SELECT names ``key``.

    Used to exercise ``_rag_embed_base_url`` (reads ``local_llm_api_url``).
    ``fetch`` returns [] so ``_rag_extras_flags`` co-reads degrade to False.
    """

    def __init__(self, key: str, value: str | None):
        self._key = key
        self._value = value

    def acquire(self):
        outer = self

        class _Conn:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *_a):
                return False

            async def fetchrow(self_inner, sql, *_args):
                if outer._key in sql:
                    return {"value": outer._value} if outer._value is not None else None
                return None

            async def fetch(self_inner, *_a, **_k):
                return []

        return _Conn()


@pytest.mark.unit
class TestRagEmbedBaseUrlRead:
    """`_rag_embed_base_url` returns local_llm_api_url from app_settings, or
    None when unset/blank so the retriever keeps its localhost default."""

    @pytest.mark.asyncio
    async def test_returns_configured_url(self):
        client = _make_client_with_pool(
            _FakePoolWithNamedSetting(
                "local_llm_api_url", "http://host.docker.internal:11434"
            )
        )
        assert (
            await client._rag_embed_base_url() == "http://host.docker.internal:11434"
        )

    @pytest.mark.asyncio
    async def test_none_when_unset(self):
        client = _make_client_with_pool(
            _FakePoolWithNamedSetting("local_llm_api_url", None)
        )
        assert await client._rag_embed_base_url() is None

    @pytest.mark.asyncio
    async def test_blank_value_returns_none(self):
        client = _make_client_with_pool(
            _FakePoolWithNamedSetting("local_llm_api_url", "   ")
        )
        assert await client._rag_embed_base_url() is None

    @pytest.mark.asyncio
    async def test_search_via_rag_engine_passes_embed_base_url(self):
        """The resolved URL must reach get_rag_retriever as embed_base_url —
        this is the actual wiring the dead-RAG bug was missing."""
        client = _make_client_with_pool(
            _FakePoolWithNamedSetting(
                "local_llm_api_url", "http://host.docker.internal:11434"
            )
        )
        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=[]))
        get_mock = AsyncMock(return_value=retriever)
        with patch("services.rag_engine.get_rag_retriever", new=get_mock):
            await client._search_via_rag_engine(
                "query", source_table="posts", min_similarity=0.3, limit=5,
            )
        assert (
            get_mock.call_args.kwargs["embed_base_url"]
            == "http://host.docker.internal:11434"
        )


@pytest.mark.unit
class TestExtrasFlagsThreadedIntoRetriever:
    """End-to-end: when prod has all three RAG flags on, the retriever
    factory MUST receive hybrid=True + rerank=True. Pre-2026-05-27 this
    silently defaulted to False because the call site didn't pass them."""

    @pytest.mark.asyncio
    async def test_hybrid_and_rerank_flags_propagate(self):
        client = _make_client_with_pool(_FakePoolWithMultiSettings({
            "rag_engine_enabled": "true",
            "rag_hybrid_enabled": "true",
            "rag_rerank_enabled": "true",
        }))
        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=[]))
        get_mock = AsyncMock(return_value=retriever)
        with patch("services.rag_engine.get_rag_retriever", new=get_mock):
            await client._search_via_rag_engine(
                "query",
                source_table=None,
                min_similarity=0.0,
                limit=5,
            )
        kwargs = get_mock.call_args.kwargs
        assert kwargs["hybrid"] is True
        assert kwargs["rerank"] is True

    @pytest.mark.asyncio
    async def test_extras_off_when_settings_say_off(self):
        client = _make_client_with_pool(_FakePoolWithMultiSettings({
            "rag_engine_enabled": "true",
            "rag_hybrid_enabled": "false",
            "rag_rerank_enabled": "false",
        }))
        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=[]))
        get_mock = AsyncMock(return_value=retriever)
        with patch("services.rag_engine.get_rag_retriever", new=get_mock):
            await client._search_via_rag_engine(
                "query",
                source_table=None,
                min_similarity=0.0,
                limit=5,
            )
        kwargs = get_mock.call_args.kwargs
        assert kwargs["hybrid"] is False
        assert kwargs["rerank"] is False


# ---------------------------------------------------------------------------
# 2026-07-11 — rerank score display normalization
#
# When the cross-encoder reranker is on, ``NodeWithScore.score`` is a raw
# cross-encoder LOGIT (unbounded, routinely negative — live samples span
# +6.22 … -10.21), not a 0-1 cosine. Surfacing it verbatim into the writer
# prompt as "[similarity: -7.86]" is cosmetically wrong. ``_search_via_rag_engine``
# now attaches a display-only ``MemoryHit.display_similarity`` = sigmoid(logit)
# on the rerank path, leaving the authoritative ``.similarity`` untouched so
# threshold consumers (e.g. ``topic_dedup_guard``'s ``>= 0.75`` re-check) keep
# their current behavior.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRerankLogitToSimilarity:
    """``_rerank_logit_to_similarity`` maps a cross-encoder logit → (0, 1)."""

    def test_zero_logit_is_half(self):
        from poindexter.memory.client import _rerank_logit_to_similarity

        assert _rerank_logit_to_similarity(0.0) == pytest.approx(0.5)

    def test_strong_positive_logit_near_one(self):
        from poindexter.memory.client import _rerank_logit_to_similarity

        # +6.22 was the top live match for "VRAM optimization for local LLM".
        assert _rerank_logit_to_similarity(6.2212) == pytest.approx(0.998, abs=1e-3)

    def test_deep_negative_logit_stays_small_positive(self):
        from poindexter.memory.client import _rerank_logit_to_similarity

        # -7.86 is a routine reranked score. It must map to a small positive
        # in (0, 1) — never a negative "similarity" and never exactly 0.
        v = _rerank_logit_to_similarity(-7.86)
        assert 0.0 < v < 0.01

    def test_monotonic_preserves_ordering(self):
        from poindexter.memory.client import _rerank_logit_to_similarity

        f = _rerank_logit_to_similarity
        assert f(-4.39) < f(0.0) < f(2.45) < f(6.22)

    def test_extreme_logits_do_not_overflow(self):
        from poindexter.memory.client import _rerank_logit_to_similarity

        # A naive 1/(1+exp(-x)) raises OverflowError at large negative x.
        # The numerically-stable form must clamp to the (0, 1) bounds.
        assert _rerank_logit_to_similarity(1000.0) == pytest.approx(1.0)
        assert _rerank_logit_to_similarity(-1000.0) == pytest.approx(0.0)

    def test_always_in_unit_interval(self):
        from poindexter.memory.client import _rerank_logit_to_similarity

        for x in (-12.5, -3.0, -0.1, 0.0, 0.1, 3.0, 12.5):
            v = _rerank_logit_to_similarity(x)
            assert 0.0 <= v <= 1.0


@pytest.mark.unit
class TestRerankDisplaySimilarity:
    """``_search_via_rag_engine`` attaches a display-only normalized score on
    the rerank path and leaves it ``None`` otherwise (cosine already 0-1)."""

    @pytest.mark.asyncio
    async def test_display_similarity_set_when_rerank_on(self):
        from poindexter.memory.client import _rerank_logit_to_similarity

        client = _make_client_with_pool(_FakePoolWithMultiSettings({
            "rag_engine_enabled": "true",
            "rag_rerank_enabled": "true",
        }))
        node = SimpleNamespace(
            text="t", metadata={"source_table": "posts", "source_id": "p1"},
        )
        nws = SimpleNamespace(node=node, score=-7.86)
        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=[nws]))
        with patch(
            "services.rag_engine.get_rag_retriever",
            new=AsyncMock(return_value=retriever),
        ):
            hits = await client._search_via_rag_engine(
                "query", source_table="posts", min_similarity=0.3, limit=5,
            )

        h = hits[0]
        # Authoritative score is untouched — threshold consumers depend on it.
        assert h.similarity == pytest.approx(-7.86)
        # Display value is the sigmoid of the logit.
        assert h.display_similarity == pytest.approx(
            _rerank_logit_to_similarity(-7.86)
        )

    @pytest.mark.asyncio
    async def test_display_similarity_none_when_rerank_off(self):
        client = _make_client_with_pool(_FakePoolWithMultiSettings({
            "rag_engine_enabled": "true",
            "rag_rerank_enabled": "false",
        }))
        # rerank off → score is a real 0-1 cosine; no normalization needed.
        node = SimpleNamespace(
            text="t", metadata={"source_table": "posts", "source_id": "p1"},
        )
        nws = SimpleNamespace(node=node, score=0.87)
        retriever = SimpleNamespace(aretrieve=AsyncMock(return_value=[nws]))
        with patch(
            "services.rag_engine.get_rag_retriever",
            new=AsyncMock(return_value=retriever),
        ):
            hits = await client._search_via_rag_engine(
                "query", source_table="posts", min_similarity=0.3, limit=5,
            )

        h = hits[0]
        assert h.similarity == pytest.approx(0.87)
        assert h.display_similarity is None
