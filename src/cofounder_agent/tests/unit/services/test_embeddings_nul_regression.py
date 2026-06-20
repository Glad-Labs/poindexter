"""Regression tests for the claude_code_sessions "3 failed" embeddings bug.

Root cause: Claude Code session transcripts occasionally capture a raw NUL
byte (0x00). NUL is valid UTF-8 so the tap's ``errors="replace"`` decode
keeps it, but Postgres ``text``/``varchar`` columns reject it — the
embeddings INSERT died with::

    asyncpg.exceptions.CharacterNotInRepertoireError:
    invalid byte sequence for encoding "UTF8": 0x00

...on the same handful of sessions (parts) every run. The failures were
invisible in ``~/.gladlabs/auto-embed.log`` because the runner logs the
exception on ``services.taps.runner`` — a logger that does NOT propagate to
that script's ``auto-embed`` file handler — so only the failure COUNT showed.

This module covers two of the three guards added:

1. ``MemoryClient.store`` strips NUL before the INSERT (the universal
   backstop for every writer).
2. ``run_tap`` records each store-failure reason on ``TapStats.failures``
   so ``auto-embed.py`` can log the cause, not just the count.

The third guard — the tap's ``_render_session`` stripping NUL at source —
is covered next to the other tap tests in
``test_claude_code_sessions_tap.py::TestRenderSession::test_nul_bytes_stripped``.

Pure unit tests: no real Postgres, no Ollama. asyncpg interactions are
faked so the assertions run in CI (where ``test_memory_client.py`` is
skipped for lack of a live DB).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest

from plugins.tap import Document
from poindexter.memory import MemoryClient
from services.taps.runner import run_tap

# ---------------------------------------------------------------------------
# Fakes — capture asyncpg interactions without a real DB.
# ---------------------------------------------------------------------------


class _FakeConn:
    """Records execute() calls; fetchval()/fetch() model an empty table."""

    def __init__(self) -> None:
        self.execute_calls: list[tuple[Any, ...]] = []

    async def execute(self, *args: Any) -> str:
        self.execute_calls.append(args)
        return "INSERT 0 1"

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def fetch(self, *args: Any, **kwargs: Any) -> list[Any]:
        # #735: run_tap batch-pre-fetches chunk-0 hashes via conn.fetch.
        # Empty → no existing rows, so every doc is treated as new (same
        # semantics as the fetchval-returns-None row-absent model above).
        return []


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakePool:
    """Stands in for an asyncpg.Pool for store() and run_tap()."""

    def __init__(self, conn: _FakeConn | None = None) -> None:
        self.conn = conn or _FakeConn()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn)

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        return None


# ---------------------------------------------------------------------------
# MemoryClient.store — NUL byte is stripped before the INSERT.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_strips_nul_byte_from_text(caplog):
    """A NUL byte in the text must never reach the text_preview parameter
    of the INSERT — Postgres would reject it (CharacterNotInRepertoireError)."""
    conn = _FakeConn()
    mem = MemoryClient(
        dsn="postgresql://x:x@localhost/x", ollama_url="http://localhost:11434"
    )
    mem._pool = _FakePool(conn)  # inject fake pool → skip real connect()

    with caplog.at_level(logging.WARNING):
        sid = await mem.store(
            text="alpha\x00beta",
            writer="claude-code",
            source_id="claude-code-sessions/scope/uuid/part-006",
            source_table="claude_sessions",
            embedding=[0.1] * 768,  # precomputed → no Ollama call
        )

    assert sid == "claude-code-sessions/scope/uuid/part-006"
    assert conn.execute_calls, "INSERT was never issued"
    # Positional args after the SQL string: source_table(1) source_id(2)
    # chunk_index(3) content_hash(4) text_preview(5) ...
    text_preview = conn.execute_calls[0][5]
    assert "\x00" not in text_preview
    assert text_preview == "alphabeta"
    # The mutation is logged, never silent (feedback_no_silent_defaults).
    assert any("NUL" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_store_text_of_only_nul_rejected():
    """Text that is *nothing but* NUL has no content after stripping —
    store() raises the empty-text ValueError rather than writing a blank row."""
    mem = MemoryClient(
        dsn="postgresql://x:x@localhost/x", ollama_url="http://localhost:11434"
    )
    mem._pool = _FakePool()
    with pytest.raises(ValueError, match="text is required"):
        await mem.store(
            text="\x00\x00",
            writer="claude-code",
            source_id="x/1",
            source_table="claude_sessions",
            embedding=[0.1] * 768,
        )


@pytest.mark.asyncio
async def test_store_clean_text_not_logged(caplog):
    """No NUL → no stripping, no warning (the guard is inert on clean text)."""
    conn = _FakeConn()
    mem = MemoryClient(
        dsn="postgresql://x:x@localhost/x", ollama_url="http://localhost:11434"
    )
    mem._pool = _FakePool(conn)
    with caplog.at_level(logging.WARNING):
        await mem.store(
            text="perfectly clean text",
            writer="claude-code",
            source_id="x/2",
            source_table="claude_sessions",
            embedding=[0.1] * 768,
        )
    assert conn.execute_calls[0][5] == "perfectly clean text"
    assert not any("NUL" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Runner — a store failure's reason is captured on TapStats.failures.
# ---------------------------------------------------------------------------


class _OneDocTap:
    name = "faketap"
    interval_seconds = 0

    async def extract(
        self, pool: Any, config: dict[str, Any]
    ) -> AsyncIterator[Document]:
        yield Document(
            source_id="faketap/doc-1",
            source_table="memory",
            text="some content",
            writer="claude-code",
        )


class _RaisingMem:
    """MemoryClient stand-in whose store() always raises (simulates the NUL
    INSERT crash, or any other store failure)."""

    async def store(self, **kwargs: Any) -> str:
        raise RuntimeError("boom: simulated INSERT failure")


@pytest.mark.asyncio
async def test_run_tap_records_store_failure_detail():
    pool = _FakePool()
    stats = await run_tap(_OneDocTap(), pool, _RaisingMem())

    assert stats.failed == 1
    assert len(stats.failures) == 1
    detail = stats.failures[0]
    assert "faketap/doc-1" in detail
    assert "RuntimeError" in detail
    assert "boom" in detail
    # to_dict carries failures so auto-embed.py can log them.
    assert stats.to_dict()["failures"] == stats.failures


@pytest.mark.asyncio
async def test_run_tap_failure_samples_capped():
    """A tap failing on many docs must not balloon TapStats.failures."""
    from services.taps.runner import _MAX_FAILURE_SAMPLES

    class _ManyDocTap:
        name = "manyfail"
        interval_seconds = 0

        async def extract(
            self, pool: Any, config: dict[str, Any]
        ) -> AsyncIterator[Document]:
            for i in range(_MAX_FAILURE_SAMPLES + 10):
                yield Document(
                    source_id=f"manyfail/doc-{i}",
                    source_table="memory",
                    text="content",
                    writer="claude-code",
                )

    stats = await run_tap(_ManyDocTap(), _FakePool(), _RaisingMem())
    assert stats.failed == _MAX_FAILURE_SAMPLES + 10
    assert len(stats.failures) == _MAX_FAILURE_SAMPLES
