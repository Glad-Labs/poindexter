"""Unit tests for ``scripts/_voice_memory.py``
(Glad-Labs/poindexter#390 — Slice 3 voice semantic recall).

Covers the four guarantees the issue lays out:

* embed-on-save persists the row even if the embedder is down (no crash)
* recall returns top-K filtered to the current conversation
* empty voice_messages table → recall returns ``[]``
* recall filters by ``discord_user_id`` / ``discord_channel_id``

The helpers live in ``scripts/`` so the discord-voice-bot doesn't have
to depend on ``services/`` being on PYTHONPATH. The tests mirror the
``test_oauth_helper.py`` rig — sys.path inject ``scripts/``, then
import the helper directly. asyncpg is mocked end-to-end (no real DB).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# scripts/ isn't on the default PYTHONPATH; insert it the same way
# test_oauth_helper.py does.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _voice_memory import (  # noqa: E402
    embed_text,
    format_recalled_context,
    recall_similar_turns,
    save_message_with_embedding,
    vector_to_pg_text,
)


# ---------------------------------------------------------------------------
# Mock conn helper — minimal asyncpg.Connection look-alike
# ---------------------------------------------------------------------------


class _FakeRow(dict):
    """Dict that also supports row['col'] subscripting like asyncpg.Record."""


class FakeConn:
    """Stand-in for an open asyncpg connection.

    Records every (sql, params) the code under test issues so assertions
    can inspect them. ``rows_for_fetch`` and ``row_for_fetchrow`` are
    rotated through one entry per call.
    """

    def __init__(
        self,
        *,
        rows_for_fetch: list[list[dict]] | None = None,
        rows_for_fetchrow: list[dict | None] | None = None,
        execute_should_raise: bool = False,
    ):
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self._rows_for_fetch = list(rows_for_fetch or [])
        self._rows_for_fetchrow = list(rows_for_fetchrow or [])
        self.execute_should_raise = execute_should_raise

    async def fetch(self, sql: str, *params):
        self.calls.append(("fetch", sql, params))
        if self._rows_for_fetch:
            data = self._rows_for_fetch.pop(0)
        else:
            data = []
        return [_FakeRow(d) for d in data]

    async def fetchrow(self, sql: str, *params):
        self.calls.append(("fetchrow", sql, params))
        if self._rows_for_fetchrow:
            data = self._rows_for_fetchrow.pop(0)
            return _FakeRow(data) if data is not None else None
        return None

    async def execute(self, sql: str, *params):
        self.calls.append(("execute", sql, params))
        if self.execute_should_raise:
            raise RuntimeError("simulated UPDATE failure")
        return "UPDATE 1"


# ---------------------------------------------------------------------------
# vector_to_pg_text
# ---------------------------------------------------------------------------


class TestVectorToPgText:
    def test_round_trip_format(self):
        out = vector_to_pg_text([0.1, 0.2, 0.3])
        assert out.startswith("[") and out.endswith("]")
        # Three comma-separated floats parseable back
        parts = out[1:-1].split(",")
        assert len(parts) == 3
        assert [float(p) for p in parts] == pytest.approx([0.1, 0.2, 0.3])

    def test_empty_vector(self):
        assert vector_to_pg_text([]) == "[]"

    def test_int_inputs_get_floated(self):
        out = vector_to_pg_text([1, 2, 3])
        # No int-y "1," — every element is the repr of a float
        for tok in out[1:-1].split(","):
            float(tok)


# ---------------------------------------------------------------------------
# embed_text — best-effort, never raises
# ---------------------------------------------------------------------------


def _mock_httpx_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestEmbedText:
    @pytest.mark.asyncio
    async def test_happy_path_returns_vector(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = request.content.decode()
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

        client = _mock_httpx_client(handler)
        try:
            vec = await embed_text(
                "hello world",
                ollama_url="http://ollama:11434",
                client=client,
            )
        finally:
            await client.aclose()

        assert vec == [0.1, 0.2, 0.3]
        assert captured["url"].endswith("/api/embed")
        assert "hello world" in captured["body"]

    @pytest.mark.asyncio
    async def test_empty_text_short_circuits(self):
        # No client passed — would raise if it actually tried to call.
        assert await embed_text("", ollama_url="http://does-not-exist") is None
        assert await embed_text("   ", ollama_url="http://does-not-exist") is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        client = _mock_httpx_client(handler)
        try:
            vec = await embed_text(
                "hi", ollama_url="http://ollama:11434", client=client,
            )
        finally:
            await client.aclose()
        assert vec is None

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": []})

        client = _mock_httpx_client(handler)
        try:
            vec = await embed_text(
                "hi", ollama_url="http://ollama:11434", client=client,
            )
        finally:
            await client.aclose()
        assert vec is None


# ---------------------------------------------------------------------------
# save_message_with_embedding
# ---------------------------------------------------------------------------


class TestSaveMessageWithEmbedding:
    @pytest.mark.asyncio
    async def test_inserts_then_updates_embedding(self):
        conn = FakeConn(rows_for_fetchrow=[{"id": 42}])

        async def fake_embed(text, **kwargs):
            return [0.0] * 768

        with patch("_voice_memory.embed_text", new=fake_embed):
            row_id = await save_message_with_embedding(
                conn,
                role="user",
                content="hello",
                discord_user_id="123",
                discord_channel_id="456",
                ollama_url="http://ollama:11434",
            )

        assert row_id == 42
        # First call: INSERT (fetchrow).  Second: UPDATE embedding.
        assert conn.calls[0][0] == "fetchrow"
        assert "INSERT INTO voice_messages" in conn.calls[0][1]
        assert conn.calls[1][0] == "execute"
        assert "UPDATE voice_messages" in conn.calls[1][1]
        # The embedding param is the pgvector text, second positional
        embedding_param = conn.calls[1][2][0]
        assert embedding_param.startswith("[") and embedding_param.endswith("]")

    @pytest.mark.asyncio
    async def test_embed_failure_does_not_crash_save(self):
        """Embed-on-save failure must NOT propagate — the row stays in
        the table with a NULL embedding, which is the documented design."""
        conn = FakeConn(rows_for_fetchrow=[{"id": 99}])

        async def fake_embed_fail(text, **kwargs):
            return None  # simulate any embedder failure

        with patch("_voice_memory.embed_text", new=fake_embed_fail):
            row_id = await save_message_with_embedding(
                conn,
                role="assistant",
                content="something",
                ollama_url="http://ollama:11434",
            )

        assert row_id == 99
        # INSERT happened, UPDATE did NOT (no vector to write)
        assert any("INSERT INTO voice_messages" in c[1] for c in conn.calls)
        assert not any("UPDATE voice_messages" in c[1] for c in conn.calls)

    @pytest.mark.asyncio
    async def test_embed_raises_does_not_crash_save(self):
        """Even if the embed helper raises (network blip), the row id
        is still returned and the conversation continues."""
        conn = FakeConn(rows_for_fetchrow=[{"id": 7}])

        async def fake_embed_raise(text, **kwargs):
            raise RuntimeError("network down")

        with patch("_voice_memory.embed_text", new=fake_embed_raise):
            row_id = await save_message_with_embedding(
                conn,
                role="user",
                content="hi",
                ollama_url="http://ollama:11434",
            )

        assert row_id == 7

    @pytest.mark.asyncio
    async def test_insert_failure_returns_none(self):
        conn = MagicMock()
        conn.fetchrow = AsyncMock(side_effect=RuntimeError("DB gone"))
        row_id = await save_message_with_embedding(
            conn,
            role="user",
            content="hi",
            ollama_url="http://ollama:11434",
        )
        assert row_id is None


# ---------------------------------------------------------------------------
# recall_similar_turns
# ---------------------------------------------------------------------------


class TestRecallSimilarTurns:
    @pytest.mark.asyncio
    async def test_empty_table_returns_empty_list(self):
        """No prior voice_messages → recall returns []."""
        conn = FakeConn(rows_for_fetch=[[]])

        async def fake_embed(text, **kwargs):
            return [0.1] * 768

        with patch("_voice_memory.embed_text", new=fake_embed):
            hits = await recall_similar_turns(
                conn,
                query_text="anything",
                ollama_url="http://ollama:11434",
                discord_user_id="u1",
                discord_channel_id="c1",
            )

        assert hits == []
        # We did issue the query, just got nothing back
        assert conn.calls and conn.calls[0][0] == "fetch"

    @pytest.mark.asyncio
    async def test_returns_top_k_in_chronological_order(self):
        """Top-K hits resorted oldest → newest so the LLM sees a
        conversational ordering, not a relevance ordering."""
        # asyncpg returns rows sorted by similarity DESC; we then resort
        # by created_at ASC inside the helper.
        t_old = datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc)
        t_mid = datetime(2026, 5, 5, 11, 0, 0, tzinfo=timezone.utc)
        t_new = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
        rows = [
            {"id": 3, "role": "user", "content": "newest match",
             "created_at": t_new, "similarity": 0.9},
            {"id": 1, "role": "user", "content": "oldest match",
             "created_at": t_old, "similarity": 0.85},
            {"id": 2, "role": "assistant", "content": "middle",
             "created_at": t_mid, "similarity": 0.80},
        ]
        conn = FakeConn(rows_for_fetch=[rows])

        async def fake_embed(text, **kwargs):
            return [0.1] * 768

        with patch("_voice_memory.embed_text", new=fake_embed):
            hits = await recall_similar_turns(
                conn,
                query_text="match",
                ollama_url="http://ollama:11434",
                discord_user_id="u1",
                discord_channel_id="c1",
                k=3,
            )

        assert [h["id"] for h in hits] == [1, 2, 3]
        # Similarity preserved
        assert hits[0]["similarity"] == 0.85

    @pytest.mark.asyncio
    async def test_filters_by_user_and_channel(self):
        """SQL must include both filter clauses + the params passed."""
        conn = FakeConn(rows_for_fetch=[[]])

        async def fake_embed(text, **kwargs):
            return [0.1] * 768

        with patch("_voice_memory.embed_text", new=fake_embed):
            await recall_similar_turns(
                conn,
                query_text="topic",
                ollama_url="http://ollama:11434",
                discord_user_id="user-123",
                discord_channel_id="chan-456",
                k=5,
            )

        sql, params = conn.calls[0][1], conn.calls[0][2]
        assert "discord_user_id =" in sql
        assert "discord_channel_id =" in sql
        # qvec_text + min_sim + user_id + channel_id + limit
        assert "user-123" in params
        assert "chan-456" in params

    @pytest.mark.asyncio
    async def test_excludes_current_exchange_ids(self):
        conn = FakeConn(rows_for_fetch=[[]])

        async def fake_embed(text, **kwargs):
            return [0.1] * 768

        with patch("_voice_memory.embed_text", new=fake_embed):
            await recall_similar_turns(
                conn,
                query_text="topic",
                ollama_url="http://ollama:11434",
                exclude_ids=[10, 11],
            )

        sql, params = conn.calls[0][1], conn.calls[0][2]
        assert "id <> ALL" in sql
        # exclude list must reach the params as int list
        assert [10, 11] in [p for p in params if isinstance(p, list)]

    @pytest.mark.asyncio
    async def test_embed_failure_returns_empty(self):
        """If the query embed fails, recall returns [] rather than
        crashing the conversation."""
        conn = FakeConn(rows_for_fetch=[[]])

        async def fake_embed_fail(text, **kwargs):
            return None

        with patch("_voice_memory.embed_text", new=fake_embed_fail):
            hits = await recall_similar_turns(
                conn,
                query_text="topic",
                ollama_url="http://ollama:11434",
            )

        assert hits == []
        # No DB call should happen when embedding failed
        assert conn.calls == []

    @pytest.mark.asyncio
    async def test_k_zero_short_circuits(self):
        conn = FakeConn(rows_for_fetch=[[]])
        hits = await recall_similar_turns(
            conn,
            query_text="topic",
            ollama_url="http://ollama:11434",
            k=0,
        )
        assert hits == []
        assert conn.calls == []


# ---------------------------------------------------------------------------
# format_recalled_context
# ---------------------------------------------------------------------------


class TestFormatRecalledContext:
    def test_empty_list_returns_empty_string(self):
        assert format_recalled_context([]) == ""

    def test_renders_role_and_content(self):
        hits = [
            {"role": "user", "content": "first thing I said"},
            {"role": "assistant", "content": "my reply"},
        ]
        out = format_recalled_context(hits)
        assert "[user] first thing I said" in out
        assert "[assistant] my reply" in out
        # Header makes the LLM aware these are recalled, not live
        assert "Recalled context" in out

    def test_long_content_is_truncated(self):
        long = "x" * 1000
        out = format_recalled_context([{"role": "user", "content": long}])
        # 240-char cap + ellipsis
        assert "..." in out
        assert len(out) < 1000
