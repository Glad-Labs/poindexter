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
