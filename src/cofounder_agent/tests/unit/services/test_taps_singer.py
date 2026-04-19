"""Unit tests for services.taps._singer.SingerTap.

Doesn't require an actual Singer binary; uses a mock subprocess that
emits known JSON-lines messages (RECORD / SCHEMA / STATE) to verify
parsing, Document conversion, and STATE persistence behavior.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins import Tap
from services.taps._singer import SingerTap


class _DemoSingerTap(SingerTap):
    name = "demo"
    binary = "tap-demo"
    source_table = "demo"


class TestProtocolConformance:
    def test_singer_tap_subclass_conforms_to_tap(self):
        assert isinstance(_DemoSingerTap(), Tap)


class TestRecordToDocument:
    def test_default_extracts_id_as_source_id(self):
        tap = _DemoSingerTap()
        doc = tap.record_to_document("issues", {"id": 42, "title": "Test", "body": "Body"})
        assert doc is not None
        assert doc.source_id == "issues/42"
        assert doc.source_table == "demo"
        assert doc.writer == "singer:tap-demo"

    def test_default_prefers_number_over_hash_fallback(self):
        tap = _DemoSingerTap()
        doc = tap.record_to_document("prs", {"number": 7, "state": "open"})
        assert doc is not None
        assert doc.source_id == "prs/7"

    def test_default_falls_back_to_hash_when_no_id_field(self):
        tap = _DemoSingerTap()
        doc = tap.record_to_document("stream", {"some_field": "x"})
        assert doc is not None
        assert doc.source_id.startswith("stream/")
        # Hash part should be hex.
        assert len(doc.source_id.split("/")[1]) == 16

    def test_empty_record_skipped(self):
        tap = _DemoSingerTap()
        assert tap.record_to_document("stream", {}) is None

    def test_subclass_can_override(self):
        class CustomTap(SingerTap):
            name = "custom"
            binary = "tap-custom"

            def record_to_document(self, stream, record):
                from plugins.tap import Document
                return Document(
                    source_id=f"custom-{record['x']}",
                    source_table="custom",
                    text=f"x={record['x']}",
                    metadata={},
                    writer="custom",
                )

        doc = CustomTap().record_to_document("any", {"x": "hello"})
        assert doc.source_id == "custom-hello"
        assert doc.writer == "custom"


class TestBinaryRequirement:
    @pytest.mark.asyncio
    async def test_extract_raises_if_binary_unset(self):
        class UnsetTap(SingerTap):
            name = "unset"
            # binary = "" (inherits default)

        tap = UnsetTap()
        with pytest.raises(RuntimeError, match="binary"):
            async for _ in tap.extract(pool=None, config={}):
                pass


class TestStateHandling:
    @pytest.mark.asyncio
    async def test_load_state_returns_empty_when_missing(self):
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=_MockAcquire(conn))

        tap = _DemoSingerTap()
        state = await tap._load_state(pool)
        assert state == {}

    @pytest.mark.asyncio
    async def test_load_state_parses_stored_json(self):
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value='{"bookmark": "2026-04-19"}')
        pool.acquire = MagicMock(return_value=_MockAcquire(conn))

        tap = _DemoSingerTap()
        state = await tap._load_state(pool)
        assert state == {"bookmark": "2026-04-19"}

    @pytest.mark.asyncio
    async def test_load_state_handles_malformed_json(self):
        pool = MagicMock()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value="not json")
        pool.acquire = MagicMock(return_value=_MockAcquire(conn))

        tap = _DemoSingerTap()
        state = await tap._load_state(pool)
        assert state == {}

    @pytest.mark.asyncio
    async def test_save_state_upserts(self):
        pool = MagicMock()
        conn = AsyncMock()
        conn.execute = AsyncMock()
        pool.acquire = MagicMock(return_value=_MockAcquire(conn))

        tap = _DemoSingerTap()
        await tap._save_state(pool, {"bookmark": "abc"})

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args.args
        assert args[1] == "plugin.tap.demo.state"
        assert json.loads(args[2]) == {"bookmark": "abc"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockAcquire:
    """asyncpg's pool.acquire() is an async context manager. Build a
    mock that yields the passed-in connection."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc_val, tb):
        return None


# ---------------------------------------------------------------------------
# End-to-end with a fake subprocess
# ---------------------------------------------------------------------------


class _FakeProc:
    """Mimics asyncio.subprocess.Process enough for the SingerTap loop."""

    def __init__(self, stdout_lines: list[bytes], stderr: bytes = b"", returncode: int = 0):
        self._lines = stdout_lines
        self._stderr = stderr
        self.returncode = returncode
        self.stdout = _AsyncByteIter(stdout_lines)
        self.stderr = _BytesReader(stderr)

    async def wait(self):
        return self.returncode


class _AsyncByteIter:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _BytesReader:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


class TestExtractParsesMessages:
    @pytest.mark.asyncio
    async def test_yields_documents_from_record_messages(self):
        lines = [
            b'{"type": "SCHEMA", "stream": "issues", "schema": {}}\n',
            b'{"type": "RECORD", "stream": "issues", "record": {"id": 1, "title": "A"}}\n',
            b'{"type": "RECORD", "stream": "issues", "record": {"id": 2, "title": "B"}}\n',
            b'{"type": "STATE", "value": {"last_id": 2}}\n',
        ]

        pool = MagicMock()
        save_calls = []

        async def fake_save(pool_arg, state):
            save_calls.append(state)

        tap = _DemoSingerTap()
        tap._load_state = AsyncMock(return_value={})
        tap._save_state = fake_save

        with patch(
            "services.taps._singer.asyncio.create_subprocess_exec",
            AsyncMock(return_value=_FakeProc(lines)),
        ):
            docs = [d async for d in tap.extract(pool=pool, config={})]

        assert [d.source_id for d in docs] == ["issues/1", "issues/2"]
        assert save_calls == [{"last_id": 2}]

    @pytest.mark.asyncio
    async def test_ignores_non_json_lines_and_unknown_types(self):
        lines = [
            b'not json at all\n',
            b'{"type": "ACTIVATE_VERSION", "stream": "x"}\n',
            b'{"type": "RECORD", "stream": "x", "record": {"id": 1}}\n',
        ]

        tap = _DemoSingerTap()
        tap._load_state = AsyncMock(return_value={})
        tap._save_state = AsyncMock()

        with patch(
            "services.taps._singer.asyncio.create_subprocess_exec",
            AsyncMock(return_value=_FakeProc(lines)),
        ):
            docs = [d async for d in tap.extract(pool=MagicMock(), config={})]

        assert len(docs) == 1
        assert docs[0].source_id == "x/1"

    @pytest.mark.asyncio
    async def test_nonzero_return_code_does_not_raise(self):
        lines = [b'{"type": "RECORD", "stream": "s", "record": {"id": 1}}\n']

        tap = _DemoSingerTap()
        tap._load_state = AsyncMock(return_value={})
        tap._save_state = AsyncMock()

        with patch(
            "services.taps._singer.asyncio.create_subprocess_exec",
            AsyncMock(return_value=_FakeProc(lines, stderr=b"binary failed", returncode=1)),
        ):
            docs = [d async for d in tap.extract(pool=MagicMock(), config={})]

        # We still yield whatever records came through before the crash.
        # Caller (runner) decides whether to treat nonzero exit as failure.
        assert len(docs) == 1
