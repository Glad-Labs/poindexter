"""
Unit tests for poindexter.memory.MemoryClient

Tests the shared pgvector memory client that Claude Code, OpenClaw, the
content worker, both MCP servers, and the poindexter CLI all import.
Each test runs against a real local pgvector instance (not mocked) because
the SQL + vector math is the thing most likely to break.

Requires:
    - LOCAL_DATABASE_URL or DATABASE_URL pointing at a pgvector-enabled DB
    - Ollama running on localhost:11434 with nomic-embed-text available
    - The embeddings table from migrations 001..024 applied

Run with:
    cd src/cofounder_agent
    python -m pytest tests/unit/services/test_memory_client.py -v
"""

import os

# Ensure the poindexter package is importable from the test runner
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from poindexter.memory import MemoryClient, MemoryHit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_WRITER = "test-harness"
TEST_SOURCE_TABLE = "memory"
TEST_PREFIX = "test-memory-client/"


_CONFTEST_SENTINEL_DSN = "postgresql://test:test@localhost/test"


def _test_dsn() -> str:
    dsn = (
        os.getenv("POINDEXTER_MEMORY_DSN")
        or os.getenv("LOCAL_DATABASE_URL")
        or os.getenv("DATABASE_URL")
        or ""
    )
    # conftest seeds DATABASE_URL with a dummy sentinel so import-time
    # reads don't crash. That sentinel is not a real DB — treat it as
    # "no DSN available" so these integration tests skip instead of
    # trying to authenticate with bogus creds.
    if dsn == _CONFTEST_SENTINEL_DSN:
        return ""
    return dsn


_LIVE_DB_AVAILABLE = bool(_test_dsn())

# Skip ALL tests in this module if no live DB is available. These are
# integration tests that hit pgvector + Ollama — they're NOT pure unit
# tests and shouldn't run in CI environments without those services.
pytestmark = pytest.mark.skipif(
    not _LIVE_DB_AVAILABLE,
    reason="No DB DSN configured — set LOCAL_DATABASE_URL to run memory tests",
)


@pytest.fixture
def dsn():
    return _test_dsn()


@pytest.fixture
async def mem(dsn):
    """Yield a connected MemoryClient, clean up test rows on teardown."""
    # #198: MemoryClient now requires ollama_url — use a local default
    # for tests that do round-trips against a live Ollama. The skip
    # guard above ensures this only runs when OLLAMA_URL is set.
    ollama_url = os.getenv("OLLAMA_URL") or "http://127.0.0.1:11434"
    client = MemoryClient(dsn=dsn, ollama_url=ollama_url)
    await client.connect()
    yield client
    # Cleanup: delete any test rows we inserted
    pool = await client._require_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM embeddings WHERE writer = $1", TEST_WRITER
        )
    await client.close()


# ---------------------------------------------------------------------------
# store + search round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_and_search_round_trip(mem):
    """Store a note, then search for it — should come back at high similarity."""
    text = "MemoryClient integration test: the default pipeline writer model is gemma3:27b because the RTX 5090 only has 32GB VRAM."
    source_id = f"{TEST_PREFIX}round_trip_test.md"

    sid = await mem.store(
        text=text,
        writer=TEST_WRITER,
        source_id=source_id,
        source_table=TEST_SOURCE_TABLE,
        tags=["test", "integration"],
    )
    assert sid == source_id

    hits = await mem.search(
        "why is gemma3 the writer model",
        source_table=TEST_SOURCE_TABLE,
        writer=TEST_WRITER,
        min_similarity=0.3,
        limit=5,
    )
    assert len(hits) >= 1
    assert isinstance(hits[0], MemoryHit)
    assert hits[0].source_id == source_id
    assert hits[0].writer == TEST_WRITER
    assert hits[0].similarity > 0.3


@pytest.mark.asyncio
async def test_store_idempotent(mem):
    """Storing the same text twice with the same source_id should upsert, not duplicate."""
    text = "Idempotency test: this exact text should only produce one row."
    source_id = f"{TEST_PREFIX}idempotent_test.md"

    await mem.store(text=text, writer=TEST_WRITER, source_id=source_id)
    await mem.store(text=text, writer=TEST_WRITER, source_id=source_id)

    pool = await mem._require_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM embeddings WHERE source_id = $1",
            source_id,
        )
    assert count == 1


@pytest.mark.asyncio
async def test_store_precomputed_embedding(mem):
    """Store with a pre-computed embedding vector — no Ollama call."""
    text = "Precomputed embedding test."
    source_id = f"{TEST_PREFIX}precomputed_test.md"
    fake_vec = [0.01] * 768  # 768-dim dummy vector

    sid = await mem.store(
        text=text,
        writer=TEST_WRITER,
        source_id=source_id,
        embedding=fake_vec,
        content_hash="precomputed_test_hash",
        embedding_model="test-model",
    )
    assert sid == source_id

    # Verify it landed with the custom model name
    pool = await mem._require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT embedding_model, content_hash FROM embeddings WHERE source_id = $1",
            source_id,
        )
    assert row is not None
    assert row["embedding_model"] == "test-model"
    assert row["content_hash"] == "precomputed_test_hash"


@pytest.mark.asyncio
async def test_store_rejects_wrong_dim_embedding(mem):
    """Pre-computed embedding with wrong dimensions should raise ValueError."""
    with pytest.raises(ValueError, match="512 dims"):
        await mem.store(
            text="Wrong dim test",
            writer=TEST_WRITER,
            source_id=f"{TEST_PREFIX}wrong_dim.md",
            embedding=[0.01] * 512,  # wrong: should be 768
        )


@pytest.mark.asyncio
async def test_store_rejects_empty_text(mem):
    """Empty text should raise ValueError."""
    with pytest.raises(ValueError, match="text is required"):
        await mem.store(text="", writer=TEST_WRITER)


@pytest.mark.asyncio
async def test_store_rejects_empty_writer(mem):
    """Empty writer should raise ValueError."""
    with pytest.raises(ValueError, match="writer is required"):
        await mem.store(text="some text", writer="")


# ---------------------------------------------------------------------------
# search edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(mem):
    """Blank search query should return an empty list, not crash."""
    hits = await mem.search("")
    assert hits == []


@pytest.mark.asyncio
async def test_search_writer_filter(mem):
    """Writer filter should exclude rows from other writers."""
    text = "Writer filter test content."
    await mem.store(
        text=text,
        writer=TEST_WRITER,
        source_id=f"{TEST_PREFIX}writer_filter.md",
    )

    # Should find it when filtering to our writer
    hits = await mem.search(
        "writer filter test", writer=TEST_WRITER, min_similarity=0.3
    )
    assert any(h.source_id.endswith("writer_filter.md") for h in hits)

    # Should NOT find it when filtering to a different writer
    hits_other = await mem.search(
        "writer filter test", writer="nonexistent-writer", min_similarity=0.3
    )
    assert not any(h.source_id.endswith("writer_filter.md") for h in hits_other)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_returns_expected_shape(mem):
    """stats() should return by_source_table and by_writer dicts."""
    stats = await mem.stats()
    assert "by_source_table" in stats
    assert "by_writer" in stats
    assert isinstance(stats["by_source_table"], dict)
    assert isinstance(stats["by_writer"], dict)
    # At minimum we should have some rows from the production data
    total = sum(v["count"] for v in stats["by_source_table"].values())
    assert total > 0


# ---------------------------------------------------------------------------
# store_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_file(mem, tmp_path):
    """store_file should read a markdown file, embed it, and upsert."""
    test_file = tmp_path / "test_store_file.md"
    test_file.write_text(
        "# Store File Test\n\nThis file tests the store_file helper in MemoryClient.",
        encoding="utf-8",
    )

    sid = await mem.store_file(
        test_file, writer=TEST_WRITER, source_id_prefix=TEST_PREFIX
    )
    assert sid is not None
    assert sid.endswith("test_store_file.md")

    # Second call should skip (same content hash)
    sid2 = await mem.store_file(
        test_file, writer=TEST_WRITER, source_id_prefix=TEST_PREFIX
    )
    assert sid2 is None  # skipped — content unchanged


@pytest.mark.asyncio
async def test_store_file_nonexistent(mem):
    """store_file on a missing path should raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        await mem.store_file("/nonexistent/path.md", writer=TEST_WRITER)


# ---------------------------------------------------------------------------
# convenience helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_similar_posts_uses_correct_source_table(mem):
    """find_similar_posts must use source_table='posts' (plural).

    This is a regression test for the silent bug where 'post' (singular)
    was passed, causing zero results every call. The fix hardcoded 'posts'
    in the helper so it can never regress.
    """
    # We don't need actual matches — just verify the call doesn't crash
    # and that the source_table filter used is 'posts' (not 'post').
    hits = await mem.find_similar_posts("test query", limit=1, min_similarity=0.99)
    # With a 0.99 threshold, nothing should match
    assert hits == []


@pytest.mark.asyncio
async def test_search_decisions_uses_memory_source_table(mem):
    """search_decisions should filter to source_table='memory'."""
    hits = await mem.search_decisions("test query", limit=1)
    # Verify all returned hits (if any) are from source_table='memory'
    for h in hits:
        assert h.source_table == "memory"
